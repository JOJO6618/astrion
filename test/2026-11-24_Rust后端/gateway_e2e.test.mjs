// From the repository root, use Node 24+ after building the Rust binary:
//   cargo build --manifest-path backend-rust/Cargo.toml --bin astrion-headless
//   node --test test/2026-11-24_Rust后端/gateway_e2e.test.mjs
// ASTRION_HEADLESS_BIN can point to an already-built binary.
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import {
  CANCELED, COMPLETED, WORKSPACE_ID, closeServer, createTask, listenOnLoopback, startGatewayFixture, streamedText, waitFor,
} from './gateway_harness.mjs';
import { SCENARIO, startOpenAiMock } from './gateway_openai_mock.mjs';

function assertBootSession(sessions, conversationId) {
  assert.ok(sessions.some((item) => (item.id ?? item.conversation_id) === conversationId), 'The CLI session list must contain the created conversation');
}

async function taskIndex(gateway, status) {
  const url = new URL('/api/tasks', gateway.baseUrl);
  url.searchParams.set('status', status);
  const response = await fetch(url, { headers: { Authorization: `Bearer ${await gateway.readToken()}` } });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.success, true);
  return result.data;
}

async function assertWorkspaceBinding(gateway) {
  gateway.workspaceId = 'other-workspace';
  await assert.rejects(gateway.listSessions(20), /Unknown workspace/);
  gateway.workspaceId = WORKSPACE_ID;
  assert.deepEqual(await gateway.listSessions(20), []);

  const sessionsUrl = `${gateway.baseUrl}/api/runtime/sessions`;
  const authorization = { Authorization: `Bearer ${await gateway.readToken()}` };
  const headers = { ...authorization, 'X-Astrion-Workspace-Id': WORKSPACE_ID };
  const query = await fetch(`${sessionsUrl}?workspace_id=other`, {
    headers: authorization, signal: AbortSignal.timeout(1_000),
  });
  await query.text();
  assert.equal(query.status, 404, 'Session list must reject an unknown workspace ID in the query');
  const body = await fetch(sessionsUrl, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: 'other' }),
    signal: AbortSignal.timeout(1_000),
  });
  await body.text();
  assert.equal(body.status, 404, 'Session creation must reject a foreign workspace ID in the body');
  assert.deepEqual(await gateway.listSessions(20), [], 'Rejected workspace requests must leave the session list empty');
}

test('real GatewayClient drives Rust through SSE tool continuation, history, cancellation, and recovery', { timeout: 45_000 }, async (t) => {
  const { gateway, mock, restart } = await startGatewayFixture(t, { model: SCENARIO.model, startMock: startOpenAiMock });
  try {
    await assertWorkspaceBinding(gateway);
    const { conversation_id: conversationId } = await gateway.createSession();
    assert.equal(typeof conversationId, 'string');
    assert.ok(conversationId);

    const first = await createTask(gateway, conversationId, SCENARIO.firstPrompt);
    const firstPoll = await first.until('text from the continued tool round', () => streamedText(first.events) === SCENARIO.answerStart);
    assert.equal(firstPoll.status, 'running');
    assert.equal(mock.requests.length, 2);
    const toolStart = first.events.findIndex((event) => event.type === 'tool_start' && event.data.name === SCENARIO.tool);
    const toolComplete = first.events.findIndex((event) =>
      ['tool_update_action', 'update_action'].includes(event.type) && ['completed', 'done', 'success'].includes(event.data.status),
    );
    const textStart = first.events.findIndex((event) => ['text_start', 'ai_message_start'].includes(event.type));
    const firstText = first.events.findIndex((event) => event.type === 'text_chunk');
    assert.ok(toolStart >= 0 && toolStart < toolComplete && toolComplete < textStart && textStart < firstText,
      'Tool start and completion must precede the CLI-renderable continued text');
    assert.deepEqual(first.events[toolStart].data.arguments, { text: SCENARIO.echo });
    assert.equal(first.events[toolStart].data.id, SCENARIO.toolCallId);
    assert.equal(first.events[toolComplete].data.id, SCENARIO.toolCallId);
    assert.equal(first.events[toolComplete].data.result, SCENARIO.echo);
    const toolCompletedAt = first.events[toolComplete].ts;
    const nextRequestAt = first.events.slice(toolComplete + 1).find((event) => event.type === 'api_request_start')?.ts;
    if (Number.isFinite(toolCompletedAt) && Number.isFinite(nextRequestAt)) {
      t.diagnostic(`Tool completion to next api_request_start: ${((nextRequestAt - toolCompletedAt) * 1_000).toFixed(3)} ms`);
    } else {
      t.diagnostic('Tool completion to next api_request_start: event timestamp unavailable');
    }

    const from = first.offset;
    mock.releaseContinuation();
    const complete = await first.until('the completed tool turn', (poll) => COMPLETED.has(poll.status));
    assert.ok(first.offset > from);
    assert.equal(streamedText(first.events.filter((event) => event.idx >= from)), SCENARIO.answerEnd);
    assert.equal(streamedText(first.events), SCENARIO.answerStart + SCENARIO.answerEnd);
    const drained = await gateway.pollTask(complete.task_id, first.offset);
    assert.deepEqual(drained.events, []);
    assert.equal(drained.next_offset, first.offset);
    assertBootSession(await gateway.listSessions(20), conversationId);

    const history = await gateway.getSessionHistory(conversationId);
    assert.deepEqual(history.messages.map((message) => message.role), ['user', 'assistant', 'tool', 'assistant']);
    assert.equal(history.messages[0].content, SCENARIO.firstPrompt);
    assert.equal(history.messages[1].tool_calls[0].id, SCENARIO.toolCallId);
    assert.equal(history.messages[2].tool_call_id, SCENARIO.toolCallId);
    assert.equal(history.messages[2].content, SCENARIO.echo);
    assert.equal(history.messages[3].content, SCENARIO.answerStart + SCENARIO.answerEnd);

    const cancel = await createTask(gateway, conversationId, SCENARIO.cancelPrompt);
    const active = await cancel.until('the active cancellable SSE stream', () => streamedText(cancel.events) === SCENARIO.cancelPartial);
    assert.equal(active.status, 'running');
    const activeRows = await taskIndex(gateway, 'active');
    assert.equal(activeRows.length, 1);
    const [row] = activeRows;
    assert.equal(row.task_id, active.task_id);
    assert.equal(row.status, 'running');
    assert.equal(row.workspace_id, WORKSPACE_ID);
    assert.equal(row.conversation_id, conversationId);
    assert.equal(row.conversation_title, history.title);
    assert.ok(row.conversation_title);
    assert.ok(!Object.hasOwn(row, 'events'));
    assert.equal(mock.cancelStreamClosed, false);
    await gateway.cancelTask(active.task_id);
    await cancel.until('the CLI-compatible canceled terminal status', (poll) => CANCELED.has(poll.status));
    await waitFor('Rust to close the upstream SSE connection after cancellation', () => mock.cancelStreamClosed);
    assert.equal(streamedText(cancel.events), SCENARIO.cancelPartial);
    assert.ok(cancel.events.some((event) => event.type === 'task_stopped'), 'CLI must receive the stopped notification');

    const recovery = await createTask(gateway, conversationId, SCENARIO.recoveryPrompt);
    await recovery.until('the new task in the same conversation to finish', (poll) => COMPLETED.has(poll.status));
    assert.equal(streamedText(recovery.events), SCENARIO.recoveryAnswer);
    assert.equal(mock.requests.length, 4);
    assert.deepEqual(await taskIndex(gateway, 'active'), []);
    const finished = await taskIndex(gateway, 'succeeded,stopped');
    assert.deepEqual(finished.map((entry) => entry.task_id), [recovery.taskId, active.task_id, complete.task_id]);

    const recovered = await gateway.getSessionHistory(conversationId);
    assert.deepEqual(recovered.messages.slice(0, history.messages.length), history.messages);
    assert.equal(recovered.messages.at(-1).role, 'assistant');
    assert.equal(recovered.messages.at(-1).content, SCENARIO.recoveryAnswer);

    const fresh = await restart();
    assertBootSession(await fresh.listSessions(20), conversationId);
    assert.deepEqual((await fresh.getSessionHistory(conversationId)).messages, recovered.messages);
    assert.equal(mock.requests.length, 4, 'Reading history must not contact the model');
  } catch (error) {
    if (mock.errors.length) throw new AggregateError([error, ...mock.errors], `OpenAI mock rejected a request: ${mock.errors[0].message}`);
    throw error;
  }
  assert.deepEqual(mock.errors, []);
});

test('GatewayClient resolves the preferred and explicit models through the provider catalog', { timeout: 30_000 }, async (t) => {
  const override = { provider: 'override', model: 'fixture-override', key: 'test-override-credential' };
  const preferred = { provider: 'preferred', model: 'fixture-preferred', key: 'test-preferred-credential' };
  const modelKey = ({ provider, model }) => `${provider}/${model}`;
  const requests = [];
  const { gateway, mock } = await startGatewayFixture(t, {
    async startMock() {
      const errors = [];
      const server = createServer(async (request, response) => {
        try {
          const chunks = [];
          for await (const chunk of request) chunks.push(chunk);
          const { model, messages, stream } = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          requests.push({
            method: request.method, path: request.url, authorization: request.headers.authorization,
            model, prompt: messages.at(-1)?.content, stream,
          });
          const event = { choices: [{ index: 0, delta: { content: `Reply from ${model}` }, finish_reason: 'stop' }] };
          response.writeHead(200, { 'Content-Type': 'text/event-stream' });
          response.end(`data: ${JSON.stringify(event)}\n\ndata: [DONE]\n\n`);
        } catch (error) {
          errors.push(error);
          response.writeHead(400).end(error.message);
        }
      });
      const port = await listenOnLoopback(server);
      return { baseUrl: `http://127.0.0.1:${port}`, errors, close: () => closeServer(server) };
    },
    async prepare({ dataDir, mock }) {
      const providers = Object.fromEntries([override, preferred].map(({ provider, model, key }) => [provider, {
        base_url: `${mock.baseUrl}/${provider}/v1`, api_key: key, models: [{ id: model }],
      }]));
      await writeFile(join(dataDir, 'providers.json'), JSON.stringify({ version: 2, providers }));
      await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({ default_model: modelKey(preferred) }));
    },
  });

  const response = await fetch(`${gateway.baseUrl}/api/v1/models`, { signal: AbortSignal.timeout(3_000) });
  assert.equal(response.status, 200);
  const catalog = await response.json();
  assert.equal(catalog.success, true);
  assert.deepEqual(catalog.items.map((item) => item.model_key).sort(), [modelKey(override), modelKey(preferred)]);
  for (const privateValue of [mock.baseUrl, override.key, preferred.key]) {
    assert.ok(!JSON.stringify(catalog).includes(privateValue), 'The public catalog must omit private connection settings');
  }

  const { conversation_id: conversationId } = await gateway.createSession();
  const turns = [
    { profile: preferred, prompt: 'Use the preferred catalog model.' },
    { profile: override, prompt: 'Use an explicit catalog model.', settings: { model_key: modelKey(override) } },
  ];
  for (const { profile, prompt, settings } of turns) {
    const task = await createTask(gateway, conversationId, prompt, settings);
    await task.until('the catalog-backed response to complete', (poll) => COMPLETED.has(poll.status));
    assert.equal(streamedText(task.events), `Reply from ${profile.model}`);
  }
  assert.deepEqual(mock.errors, []);
  assert.deepEqual(requests, turns.map(({ profile, prompt }) => ({
    method: 'POST', path: `/${profile.provider}/v1/chat/completions`, authorization: `Bearer ${profile.key}`,
    model: profile.model, prompt, stream: true,
  })));
  const history = await gateway.getSessionHistory(conversationId);
  assert.deepEqual(history.messages.map(({ role, content }) => [role, content]), turns.flatMap(({ profile, prompt }) => [
    ['user', prompt], ['assistant', `Reply from ${profile.model}`],
  ]));
});

import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import test from 'node:test';
import {
  COMPLETED, WORKSPACE_ID, closeServer, listenOnLoopback, startGatewayFixture, trackTask,
} from '../2026-11-24_Rust后端/gateway_harness.mjs';

const WINDOW = 1_050_000;
const KEY = 'offline-model-key';

async function writeJson(path, body) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(body));
}

function event(response, payload) {
  response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function startBudgetMock() {
  const requests = [];
  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.headers.authorization, `Bearer ${KEY}`);
      assert.ok(['/v1/chat/completions', '/v1/responses'].includes(request.url));
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks));
      requests.push({ path: request.url, body, providerHeader: request.headers['x-test-provider'] });
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      if (request.url === '/v1/responses') {
        event(response, { type: 'response.output_text.delta', delta: 'responses reply' });
        event(response, { type: 'response.completed', response: { output: [], usage: { input_tokens: 460, output_tokens: 5 } } });
        response.end();
        return;
      }
      const toolRound = body.messages[0] && body.messages.at(-1).content === 'call echo';
      const withoutUsage = body.messages.at(-1).content === 'call echo without usage';
      const delta = toolRound || withoutUsage ? { tool_calls: [{
        index: 0, id: 'call_budget_echo', type: 'function',
        function: { name: 'astrion_echo', arguments: JSON.stringify({ text: 'x'.repeat(1_200) }) },
      }] } : { content: 'chat reply' };
      event(response, { choices: [{ index: 0, delta, finish_reason: toolRound || withoutUsage ? 'tool_calls' : 'stop' }] });
      if (!withoutUsage) {
        event(response, { choices: [], usage: { prompt_tokens: toolRound ? WINDOW - 14 : 400, completion_tokens: 5 } });
      }
      response.end('data: [DONE]\n\n');
    } catch (error) {
      if (!response.headersSent) response.writeHead(500);
      response.end(String(error));
    }
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1`, requests, close: () => closeServer(server) };
}

async function startCatalog(t) {
  const fixture = await startGatewayFixture(t, { startMock: startBudgetMock });
  const { dataDir, workspace, mock } = fixture;
  await writeJson(join(dataDir, 'config/custom_models.json'), [
    {
      model_name: 'mode-switch', url: mock.endpoint, apikey: KEY,
      context_window: WINDOW, max_output_tokens: 64_000, reasoning_capability: 'fast,thinking',
      thinkmode_status: {
        type: 'switch_model', fast_model_id: 'qwen/qwen-fast', thinking_model_id: 'think-large',
        fast_extra_parameter: { temperature: 0 },
        thinking_extra_parameter: { temperature: 0.5, max_completion_tokens: 72_000 },
      },
    },
    { model_name: 'chat-missing', model_id: 'legacy-chat', url: mock.endpoint, apikey: KEY },
  ]);
  await writeJson(join(workspace, 'config/providers_catalog.json'), {
    providers: [{ id: 'test-catalog', protocol_map: {
      'response-large': 'responses', 'response-missing': 'responses', 'response-window-only': 'responses',
    } }],
  });
  await writeJson(join(dataDir, 'providers.json'), { version: 2, providers: {
    'response-fixture': {
      catalog_id: 'test-catalog', base_url: mock.endpoint, api_key: KEY, headers: { 'x-test-provider': 'fixture' },
      models: [
        { id: 'response-large', context_window: 128_000, max_context_window: WINDOW, max_input_tokens: 1_000_000, max_output_tokens: 90_000 },
        { id: 'response-missing' },
        { id: 'response-window-only', default_context_window: WINDOW },
      ],
    },
  } });
  return fixture;
}

async function savedConversation(fixture, metadata, usage) {
  const { conversation_id: id } = await fixture.gateway.createSession();
  const file = join(fixture.dataDir, 'conversations', WORKSPACE_ID, `${id}.json`);
  const raw = JSON.parse(await readFile(file));
  // Mimic a pre-existing Python document, including settings absent from older files.
  delete raw.metadata.thinking_mode;
  delete raw.metadata.run_mode;
  Object.assign(raw.metadata, metadata);
  raw.messages = [{ role: 'user', content: 'old question' }, { role: 'assistant', content: 'old reply' }];
  if (usage === undefined) delete raw.token_statistics;
  else raw.token_statistics = { current_context_tokens: usage };
  await writeJson(file, raw);
  return { id, file };
}

async function finish(gateway, id, message, options = {}) {
  const task = await gateway.createTask({ conversation_id: id, message, ...options });
  assert.equal(task.conversation_id, id);
  await trackTask(gateway, task.task_id, id).until('completion', (poll) => COMPLETED.has(poll.status));
}

test('saved Chat Completions caps output each turn and restores mode preferences on restart', { timeout: 45_000 }, async (t) => {
  const fixture = await startCatalog(t);
  const { gateway, mock } = fixture;
  const saved = await savedConversation(fixture, {
    model_key: 'mode-switch', run_mode: 'thinking', reasoning_effort: 'high',
  }, WINDOW - 2_000);
  await finish(gateway, saved.id, 'call echo');
  assert.equal(mock.requests.length, 2);
  const [first, second] = mock.requests.map(({ body }) => body);
  assert.equal(first.model, 'think-large');
  assert.equal(first.temperature, 0.5);
  assert.equal(first.reasoning_effort, 'high');
  assert.equal(first.max_completion_tokens, 2_000);
  assert.equal(first.max_tokens, undefined);
  assert.ok(first.messages.some(({ content }) => content === 'old reply'));
  assert.equal(second.messages.at(-1).content, 'x'.repeat(1_200));
  assert.equal(second.max_completion_tokens, 1);

  await finish(gateway, saved.id, 'switch to fast', { thinking_mode: false });
  const fast = mock.requests.at(-1).body;
  assert.equal(fast.model, 'qwen/qwen-fast');
  assert.equal(fast.max_tokens, 64_000);
  assert.equal(fast.temperature, 0);
  assert.equal(fast.enable_thinking, false);
  assert.equal(fast.reasoning_effort, undefined);
  assert.equal(fast.max_completion_tokens, undefined);

  const reopened = await fixture.restart();
  await finish(reopened, saved.id, 'use the saved fast mode');
  assert.equal(mock.requests.at(-1).body.model, 'qwen/qwen-fast');
  const durable = JSON.parse(await readFile(saved.file));
  assert.equal(durable.metadata.thinking_mode, false);
  assert.equal(durable.metadata.run_mode, 'fast');
  assert.equal(durable.metadata.reasoning_effort, 'high');
});

test('non-fixed Responses uses the maximum window and preserves missing-cap semantics', { timeout: 45_000 }, async (t) => {
  const fixture = await startCatalog(t);
  const { gateway, mock, tokenPath } = fixture;
  const res = await fetch(`${process.env.ASTRION_API_BASE}/api/v1/models`, {
    headers: { Authorization: `Bearer ${(await readFile(tokenPath, 'utf8')).trim()}`, 'X-Astrion-Workspace-Id': WORKSPACE_ID },
  });
  assert.equal(res.status, 200);
  const catalog = new Map((await res.json()).items.map((item) => [item.model_key, item]));
  const large = catalog.get('response-fixture/response-large');
  assert.equal(large.default_context_window, 128_000);
  assert.equal(large.max_context_window, WINDOW);
  assert.equal(large.context_window, WINDOW);
  assert.equal(large.max_input_tokens, 1_000_000);
  assert.equal(large.max_output_tokens, 90_000);
  const missing = catalog.get('response-fixture/response-missing');
  assert.equal(missing.context_window, null);
  assert.equal(missing.max_output_tokens, null);

  const near = await savedConversation(fixture, { model_key: large.model_key, reasoning_effort: 'high' }, WINDOW - 41);
  await finish(gateway, near.id, 'large window near the maximum');
  const { path, body, providerHeader } = mock.requests.at(-1);
  assert.equal(path, '/v1/responses');
  assert.equal(body.max_output_tokens, 41);
  assert.deepEqual(body.reasoning, { effort: 'high', summary: 'detailed' });
  assert.ok(body.input.some((item) => item.content?.[0]?.text === 'old reply'));
  assert.equal(body.max_tokens, undefined);
  assert.equal(body.service_tier, undefined);
  assert.equal(providerHeader, 'fixture');

  const unknown = await savedConversation(fixture, { model_key: missing.model_key });
  await finish(gateway, unknown.id, 'legacy missing limits');
  assert.equal(mock.requests.at(-1).body.max_output_tokens, undefined);
  const windowOnly = 'response-fixture/response-window-only';
  const unknownOutput = await savedConversation(fixture, { model_key: windowOnly }, WINDOW - 17);
  await finish(gateway, unknownOutput.id, 'only the window is known');
  assert.equal(mock.requests.at(-1).body.max_output_tokens, undefined);
  const exhausted = await savedConversation(fixture, { model_key: windowOnly }, WINDOW);
  await finish(gateway, exhausted.id, 'the last reported input reached the window');
  assert.equal(mock.requests.at(-1).body.max_output_tokens, 1);
});

test('Chat Completions uses its default cap and reduces the next request without provider usage', { timeout: 45_000 }, async (t) => {
  const fixture = await startCatalog(t);
  const { gateway, mock } = fixture;
  const missing = await savedConversation(fixture, { model_key: 'chat-missing' });
  await finish(gateway, missing.id, 'legacy missing limits');
  assert.equal(mock.requests.at(-1).body.max_tokens, 32_768);

  const near = await savedConversation(fixture, { model_key: 'mode-switch' }, WINDOW - 1_000);
  await finish(gateway, near.id, 'call echo without usage');
  const [first, followup] = mock.requests.slice(-2).map(({ body }) => body);
  assert.equal(first.max_tokens, 1_000);
  assert.ok(followup.max_tokens > 1 && followup.max_tokens < first.max_tokens);
  assert.equal(followup.messages.at(-1).content, 'x'.repeat(1_200));

  await writeJson(join(fixture.dataDir, 'settings.json'), { models: { default_response_max_tokens: 65_536 } });
  const reopened = await fixture.restart();
  await finish(reopened, missing.id, 'deployment default from Python settings');
  assert.equal(mock.requests.at(-1).body.max_tokens, 65_536);
});

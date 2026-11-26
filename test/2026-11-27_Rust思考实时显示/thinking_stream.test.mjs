import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import { COMPLETED, closeServer, createTask, listenOnLoopback, startGatewayFixture, waitFor } from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { startAppRuntimeFixture } from '../2026-11-26_Rust提问计划审批/app_runtime_fixture.mjs';

const model = 'isolated-thinking-model';
const auxiliary = 'isolated-thinking-summary';
const text = 'first thought\nsecond thought';
const answer = 'visible final answer';
const hidden = 'never show provider encrypted data';

async function modelFixture() {
  const release = new Map();
  const summaries = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const bytes of request) chunks.push(bytes);
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const user = body.messages.at(-1)?.content;
    const prompt = typeof user === 'string' ? user : JSON.stringify(user);
    const category = prompt === 'fixture Agent reasoning' ? 'agent' : prompt === 'fixture Chat reasoning' ? 'chat' : null;
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    const send = (delta, finish_reason) => response.write(`data: ${JSON.stringify({ id: 'thinking-fixture', model,
      choices: [{ index: 0, delta, finish_reason }] })}\n\n`);
    if (body.model === auxiliary) {
      summaries.push(body);
      send({ role: 'assistant', content: '核对输入' }, null);
      send({}, 'stop');
      response.end(`data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 20, completion_tokens: 4, total_tokens: 24 } })}\n\ndata: [DONE]\n\n`);
      return;
    }
    if (category) {
      send({ role: 'assistant', reasoning_content: 'first thought' }, null);
      send({ reasoning_details: [{ type: 'reasoning.encrypted', data: hidden, text: hidden },
        { type: 'reasoning.text', text: '\nsecond thought' }] }, null);
      await new Promise(resolve => {
        const timer = setTimeout(resolve, 12_000);
        response.once('close', () => { clearTimeout(timer); resolve(); });
        release.set(category, () => { clearTimeout(timer); resolve(); });
      });
      release.delete(category);
    }
    if (!response.destroyed) {
      if (prompt.includes('fixture auxiliary thinking')) send({ reasoning_content: '检查已有文件和当前请求。'.repeat(35) }, null);
      send({ content: category || prompt.includes('fixture auxiliary thinking') ? answer : 'Fixture title' }, null);
      send({}, 'stop');
      response.end('data: [DONE]\n\n');
    }
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, release, summaries, close: () => closeServer(server) };
}

async function prepareCatalog({ dataDir, configDir, mock }) {
  const config = configDir || join(dataDir, 'config');
  await mkdir(config, { recursive: true });
  await writeFile(join(config, 'custom_models.json'), JSON.stringify({ models: [model, auxiliary].map(name => ({
    model_name: name, model_id: name, url: new URL('/v1', mock.endpoint).href, apikey: 'local-only-fixture',
    reasoning_capability: 'fast,thinking', context_window: 32768, max_output_tokens: 2048,
  })) }));
  await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({ auto_generate_title: false }));
}

function startFixture(t) {
  const options = { model, startMock: modelFixture, web: true, prepare: prepareCatalog, workspaceIds: ['default'] };
  const app = process.env.ASTRION_RUNTIME_TEST_APP;
  return app ? startAppRuntimeFixture(t, app, options) : startGatewayFixture(t, options);
}

test('Agent and Chat expose live thinking for ordinary and minimal history without provider private fields', { timeout: 45_000 }, async (t) => {
  const { gateway, mock } = await startFixture(t);
  const origin = gateway.baseUrl;
  const csrf = await fetch(origin + '/api/csrf-token');
  let cookie = csrf.headers.get('set-cookie').split(';', 1)[0];
  const login = await fetch(origin + '/host-login', { method: 'POST', headers: {
    cookie, origin, 'content-type': 'application/json', 'x-csrf-token': (await csrf.json()).token,
  }, body: '{}' });
  assert.equal(login.status, 200);
  cookie = login.headers.get('set-cookie').split(';', 1)[0];
  const token = await fetch(origin + '/api/csrf-token', { headers: { cookie } }).then(response => response.json());
  async function browser(path, body) {
    const response = await fetch(origin + path, { method: body ? 'POST' : 'GET', headers: {
      cookie, origin, 'content-type': 'application/json', 'x-csrf-token': token.token, 'x-astrion-workspace-id': 'default',
    }, body: body ? JSON.stringify(body) : undefined });
    const reply = await response.json();
    assert(response.ok, JSON.stringify(reply));
    assert(!JSON.stringify(reply).includes(hidden));
    return reply.data;
  }

  const agent = await createTask(gateway, null, 'fixture Agent reasoning', { thinking_mode: true });
  await agent.until('observable thought fragments before the Agent text', (poll) => poll.events.some(event => event.type === 'thinking_chunk'));
  const releaseAgent = await waitFor('Agent model pause', () => mock.release.get('agent'));
  const bootstrap = `/api/conversations/${agent.conversationId}/bootstrap`;
  const active = await waitFor('Agent bootstrap thinking block', async () => {
    const result = await browser(bootstrap);
    return result.display.messages[0]?.actions[0]?.content === text && result;
  });
  assert.equal(active.display.messages[0].currentStreamingType, 'thinking');
  assert.equal(active.display.messages[0].streamingThinking, text);
  assert.equal(active.display.messages[0].actions[0].type, 'thinking');
  releaseAgent();
  await agent.until('Agent final completion', poll => COMPLETED.has(poll.status));
  assert.equal(agent.events.find(event => event.type === 'thinking_end').data.full_content, text);
  assert(agent.events.findIndex(event => event.type === 'thinking_end') < agent.events.findIndex(event => event.type === 'text_start'));
  const committedAgent = (await browser(bootstrap)).messages.find(message => message.role === 'assistant');
  assert.equal(committedAgent.reasoning_content, text);
  assert.equal(committedAgent.content, answer);

  const chat = await browser('/api/chat/tasks', { message: 'fixture Chat reasoning', operation_id: 'thinking-fixture-op', model_key: model, thinking_mode: true });
  const chatPath = `/api/chat/conversations/${chat.conversation_id}`;
  const releaseChat = await waitFor('Chat model pause', () => mock.release.get('chat'));
  const live = await waitFor('Chat display thinking action', async () => {
    const result = await browser(chatPath);
    return result.display.messages[0]?.actions[0]?.content === text && result;
  });
  assert.equal(live.display.messages[0].actions[0].type, 'thinking');
  assert(live.display.messages[0].actions[0].responseMessageId);
  assert.equal(live.display.state.streaming, true);
  releaseChat();
  const finishedChat = await waitFor('Chat durable thinking', async () => {
    const result = await browser(chatPath);
    return result.document.messages.find(message => message.role === 'assistant' && message.content === answer);
  });
  assert.equal(finishedChat.reasoning_content, text);
});

test('auxiliary thinking runs only with a configured selector and bills its own actor', { timeout: 45_000 }, async (t) => {
  const fixture = await startFixture(t);
  const { gateway, dataDir, mock } = fixture;
  const preferences = join(dataDir, 'personalization.json');
  async function execute(enabled, selected, label) {
    await writeFile(preferences, JSON.stringify({ auto_generate_title: false, thinking_summary_enabled: enabled, thinking_summary_model: selected,
      thinking_summary_interval_seconds: 1 }));
    const task = await createTask(gateway, null, `fixture auxiliary thinking ${label}`, { thinking_mode: true });
    await task.until('completion of auxiliary thinking fixture', poll => COMPLETED.has(poll.status));
    return task;
  }
  const disabled = await execute(false, auxiliary, 'disabled');
  const invalid = await execute(true, 'missing-fixture-model', 'unknown selector');
  assert.equal(mock.summaries.length, 0);
  for (const task of [disabled, invalid]) assert(!task.events.some(event => event.type === 'thinking_summary'));
  const enabled = await execute(true, auxiliary, 'enabled');
  assert.equal(mock.summaries.length, 1);
  assert.match(mock.summaries[0].messages.at(-1).content, /fixture auxiliary thinking enabled/);
  assert.match(mock.summaries[0].messages.at(-1).content, /<当前片段原文>/);
  assert.equal(mock.summaries[0].tools, undefined);
  const summary = enabled.events.find(event => event.type === 'thinking_summary');
  assert.equal(summary.data.summary, '核对输入');
  assert(enabled.events.findIndex(event => event.type === 'text_start') < enabled.events.indexOf(summary));
  const ledger = JSON.parse(await readFile(join(dataDir, 'api_costs', `${enabled.conversationId}.json`), 'utf8'));
  assert.equal(ledger.actors.thinking_summary.kind, 'thinking_summary');
  assert.equal(ledger.actors.thinking_summary.requests, 1);
  assert.equal(ledger.actors.thinking_summary.total_tokens, 24);
  assert.equal(ledger.actors.main.total_tokens, 0);
});

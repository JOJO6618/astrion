import assert from 'node:assert/strict';
import { readDocument, readSummary, withDb } from '../2026-11-27_Rust对话格式迁移/fixture.mjs';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import { closeServer, createTask, listenOnLoopback, startGatewayFixture, waitFor } from './gateway_harness.mjs';

const ACTIVE = 'conv_session_active';
const OTHER = 'conv_session_other';
const CHAT = 'conv_scoped_chat';
const TABS = '/api/conversation-tabs';
const currentPath = '/api/conversations/current';
const documentPath = (id, workspace) => `/api/conversations/${id}${workspace ? `?workspace_id=${workspace}` : ''}`;
const loadPath = (id, workspace) => documentPath(`${id}/load`, workspace);
const currentIn = (workspace) => `${currentPath}?workspace_id=${workspace}`;

async function browser(gateway) {
  const origin = gateway.baseUrl;
  const cookie = (response) => response.headers.get('set-cookie').split(';', 1)[0];
  const challenge = await fetch(`${origin}/api/csrf-token`);
  const login = await fetch(`${origin}/host-login`, {
    method: 'POST', body: '{}', headers: { origin, cookie: cookie(challenge),
      'content-type': 'application/json', 'x-csrf-token': (await challenge.json()).token },
  });
  assert.equal(login.status, 200);
  const session = cookie(login);
  const csrf = await fetch(`${origin}/api/csrf-token`, { headers: { cookie: session } });
  const csrfToken = (await csrf.json()).token;
  return async (path, { method = 'GET', status = 200, body, headers = {}, auth = true, csrf = true } = {}) => {
    const response = await fetch(origin + path, {
      method, signal: AbortSignal.timeout(5000),
      headers: { origin, ...(auth && { cookie: session }), ...(csrf && { 'x-csrf-token': csrfToken }),
        ...(body && { 'content-type': 'application/json' }), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    assert.equal(response.status, status, `${method} ${path}: ${text}`);
    return JSON.parse(text);
  };
}

async function fixtureDocument(dataDir, workspaceId, id, title, metadata, messages = []) {
  const dir = join(dataDir, 'conversations', workspaceId);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, `${id}.json`), JSON.stringify({ id, title, messages, metadata }));
}

async function prepare({ dataDir, workspace }) {
  const versioning = { enabled: true, tracking_mode: 'conversation_only' };
  await fixtureDocument(dataDir, 'default', ACTIVE, 'Selected legacy Agent', {
    project_path: workspace, run_mode: 'deep', title_locked: true, versioning,
  }, [{ role: 'user', content: 'Stored question' },
      { role: 'assistant', content: 'Stored answer', metadata: { model_key: 'retired-agent-model' } }]);
  await fixtureDocument(dataDir, 'default', OTHER, 'Detached Agent', {
    thinking_mode: false, reasoning_effort: 'high', model_key: 'detached-model',
  }, [{ role: 'user', content: 'Will be deleted' }]);
  await fixtureDocument(dataDir, 'other', OTHER, 'Other workspace Agent', { run_mode: 'thinking' });
  await fixtureDocument(dataDir, 'default', CHAT, 'Chat is isolated', { conversation_kind: 'chat' });
  await mkdir(join(dataDir, 'save', ACTIVE), { recursive: true });
  await writeFile(join(dataDir, 'save', ACTIVE, 'inputs.jsonl'), `${JSON.stringify({ seq: 4, tree_hash: 'python-tree' })}\n`);
  await writeFile(join(dataDir, 'conversations', 'default', 'index.json'), JSON.stringify({
    [ACTIVE]: { id: ACTIVE, title: 'Selected legacy Agent' },
    [OTHER]: { id: OTHER, title: 'Detached Agent', custom_field: 'legacy data' },
  }));
}

async function startModel() {
  const requests = [];
  let stream;
  const chunk = (delta, finishReason = null) => `data: ${JSON.stringify({
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`;
  const server = createServer(async (request, response) => {
    const content = [];
    for await (const part of request) content.push(part);
    requests.push(JSON.parse(Buffer.concat(content).toString('utf8')));
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    response.write(chunk({ role: 'assistant', content: 'Still running' }));
    stream = response;
  });
  const port = await listenOnLoopback(server);
  return {
    endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, requests,
    hasStream: () => Boolean(stream),
    finish: () => stream.end(`${chunk({}, 'stop')}data: [DONE]\n\n`),
    close: () => closeServer(server),
  };
}

function snapshot(rows) {
  return rows.tabs.map(({ key, workspace_id, conversation_id, placement }) => ({
    key, workspace_id, conversation_id, placement,
  }));
}

test('Agent lifecycle preserves workspace selection and returns authoritative desktop deletion state', { timeout: 40_000 }, async (t) => {
  const { gateway, mock, dataDir, restart } = await startGatewayFixture(t, {
    web: true, workspaceIds: ['default', 'other'], model: 'lifecycle-fixture', startMock: startModel, prepare,
  });
  const api = await browser(gateway);
  await api('/api/conversation-storage/migration',{method:'POST'});
  await waitFor('history migration',async()=>{const s=await api('/api/conversation-storage/migration');return !s.running && s.migrated===3 && s.failed===1;});
  const directory=join(dataDir,'conversations','default');
  const deleteConvo = (id, workspace = 'default', status = 200) => api(documentPath(id, workspace), { method: 'DELETE', status });
  const load = (id, workspace, status = 200) => api(loadPath(id, workspace), { method: 'PUT', status });
  const command = (action, payload) => api(`${TABS}/commands`, {
    method: 'POST', body: { command_id: randomUUID(), action, payload },
  });
  const activeFile = join(dataDir, 'conversations', 'default', `${ACTIVE}.json`);
  const otherFile = join(dataDir, 'conversations', 'default', `${OTHER}.json`);
  const foreignFile = join(dataDir, 'conversations', 'other', `${OTHER}.json`);
  const indexFile = join(dataDir, 'conversations', 'default', 'index.json');
  const activeBefore = await readFile(activeFile, 'utf8');
  const otherBefore = await readFile(otherFile, 'utf8');
  const foreignBefore = await readFile(foreignFile, 'utf8');
  const indexBefore = await readFile(indexFile, 'utf8');

  await api(currentPath, { auth: false, status: 401 });
  const authorization = `Bearer ${await gateway.readToken()}`;
  await api(currentPath, { auth: false, headers: { authorization }, status: 401 });
  await api(loadPath(ACTIVE), { method: 'PUT', csrf: false, status: 403 });
  await api(documentPath(ACTIVE), { method: 'DELETE', csrf: false, status: 403 });
  assert.deepEqual((await api(currentPath)).data, {
    id: null, title: '新对话', messages_count: 0, is_temporary: true, title_locked: false,
  });
  assert.equal((await load(ACTIVE)).safe_navigation, undefined);
  assert.equal((await api(currentPath)).data.id, null);
  assert.equal((await api(`${currentPath}?conversation_id=${ACTIVE.slice(5)}`)).data.id, ACTIVE);
  await api(`${currentIn('other')}&conversation_id=${ACTIVE}`, { status: 404 });
  await load(ACTIVE, 'other', 404);
  await load(CHAT, 'default', 409);
  await deleteConvo(CHAT, 'default', 409);
  await api(loadPath(ACTIVE, 'default'), { method: 'PUT', headers: { 'x-astrion-workspace-id': 'other' }, status: 400 });

  const loaded = await load(ACTIVE, 'default');
  assert.deepEqual(loaded, {
    success: true, conversation_id: ACTIVE, title: 'Selected legacy Agent', messages_count: 2,
    run_mode: 'thinking', thinking_mode: true, model_key: 'retired-agent-model', message: `对话已加载: ${ACTIVE}`,
    versioning: { host_mode: true, enabled: true, mode: 'overwrite', tracking_mode: 'conversation_only',
      latest_seq: 4, latest_commit: 'python-tree' },
  });
  assert.deepEqual((await api(currentPath)).data, {
    id: ACTIVE, title: 'Selected legacy Agent', messages_count: 2, is_temporary: false, title_locked: true,
  });
  assert.equal((await api(currentIn('other'))).data.id, null);
  await api(loadPath(OTHER), { method: 'PUT', headers: { 'x-astrion-workspace-id': 'other' } });
  assert.equal((await api(currentIn('other'))).data.id, null);
  await load(OTHER, 'other');
  assert.equal((await api(currentIn('other'))).data.title, 'Other workspace Agent');
  assert.equal((await api(currentPath)).data.id, ACTIVE);
  assert.equal(await readFile(activeFile, 'utf8'), activeBefore);
  assert.equal(await readFile(otherFile, 'utf8'), otherBefore);
  assert.equal(await readFile(indexFile, 'utf8'), indexBefore);

  await command('open', { workspace_id: 'default', conversation_id: ACTIVE, key: 'active' });
  await command('open', { workspace_id: 'default', conversation_id: OTHER, key: 'detached' });
  await command('detach', { key: 'detached' });
  await command('open', { workspace_id: 'other', conversation_id: OTHER, key: 'foreign' });
  await command('activate', { key: 'active' });
  const task = await createTask(gateway, ACTIVE, 'Continue the selected Agent');
  await waitFor('an open model stream', mock.hasStream);
  const safe = await load(OTHER, 'default');
  assert.deepEqual([safe.safe_navigation, safe.conversation_id, safe.model_key, safe.reasoning_effort],
    [true, OTHER, 'detached-model', 'high']);
  assert.equal((await api(currentPath)).data.id, ACTIVE);
  assert.equal((await api(`${currentPath}?conversation_id=${OTHER}`)).data.id, OTHER);
  assert.equal((await api(`${documentPath(ACTIVE)}/bootstrap`)).data.running.is_truly_active, true);
  assert.equal(await readFile(otherFile, 'utf8'), otherBefore);
  const blocked = await deleteConvo(ACTIVE, 'default', 409);
  assert.equal(blocked.success, false);
  assert.equal((await api(TABS)).tabs.some((tab) => tab.key === 'active' && tab.running), true);

  // A deletion error leaves both its document and tabs available for retry.
  withDb(directory,db=>db.exec("CREATE TRIGGER reject_delete BEFORE DELETE ON conversations BEGIN SELECT RAISE(ABORT,'fixture'); END;"),false);
  await deleteConvo(OTHER, 'default', 503);
  assert.equal(await readFile(otherFile, 'utf8'), otherBefore);
  assert.equal((await api(TABS)).tabs.some((tab) => tab.key === 'detached'), true);
  withDb(directory,db=>db.exec("DROP TRIGGER reject_delete"),false);

  const detached = await deleteConvo(OTHER);
  assert.equal(detached.success, true);
  assert.equal(detached.desktop_navigation.active_key, 'active');
  assert.equal(detached.desktop_navigation.tabs.some((tab) => tab.key === 'active' && tab.running), true);
  assert.deepEqual(snapshot(detached.desktop_navigation), snapshot(await api(TABS)));
  assert.deepEqual(detached.desktop_navigation.tabs.map((tab) => tab.key), ['active', 'foreign']);
  assert.equal(await readFile(foreignFile, 'utf8'), foreignBefore);
  assert.equal(await readFile(otherFile,'utf8'),otherBefore);
  assert.equal(readSummary(directory,OTHER),null);
  assert.equal((await api('/api/conversations')).data.conversations.some((row) => row.id === OTHER), false);
  assert.equal((await api('/api/conversations/search?q=Detached')).data.count, 0);
  await load(OTHER, 'default', 404);
  await api(documentPath(OTHER), { status: 404 });
  await deleteConvo(OTHER, 'default', 404);
  assert.equal((await api(currentPath)).data.id, ACTIVE);
  assert.equal((await api(currentIn('other'))).data.id, OTHER);

  mock.finish();
  await task.until('the original Agent task to finish', (poll) => poll.status === 'succeeded');
  assert.equal(mock.requests.length, 1);
  assert.equal(mock.requests[0].messages.at(-1).content, 'Continue the selected Agent');
  const history=readDocument(directory,ACTIVE).messages;
  assert.equal(history.at(-1).content, 'Still running');
  const deleted = await deleteConvo(ACTIVE);
  assert.equal(deleted.desktop_navigation.active_key, 'foreign');
  assert.deepEqual(snapshot(deleted.desktop_navigation), snapshot(await api(TABS)));
  assert.equal((await api(currentPath)).data.id, null);
  assert.equal((await api(currentIn('other'))).data.id, OTHER);
  assert.equal(await readFile(activeFile,'utf8'),activeBefore);
  assert.equal(readSummary(directory,ACTIVE),null);
  assert.match(await readFile(join(dataDir, 'save', ACTIVE, 'inputs.jsonl'), 'utf8'), /python-tree/);

  const restarted = await restart();
  const afterRestart = await browser(restarted);
  assert.equal((await afterRestart(currentIn('other'))).data.id, null);
  assert.equal((await afterRestart(TABS)).active_key, 'foreign');
  await afterRestart(documentPath(ACTIVE), { status: 404 });
  assert.equal((await afterRestart(documentPath(OTHER, 'other'))).data.title, 'Other workspace Agent');
});

test('Agent lifecycle reports the unconfigured workspace as an ordinary empty state', { timeout: 20_000 }, async (t) => {
  const { gateway } = await startGatewayFixture(t, { web: true, workspaceIds: [] });
  const api = await browser(gateway);
  for (const [method, path] of [['GET', currentPath], ['PUT', loadPath(ACTIVE)], ['DELETE', documentPath(ACTIVE)]]) {
    const response = await api(path, { method });
    assert.equal(response.success, false);
    assert.equal(response.code, 'no_workspace');
  }
});

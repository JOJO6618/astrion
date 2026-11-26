// Run with a fresh astrion-headless binary and the CLI's installed development dependencies.
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { readFile, rename, writeFile } from 'node:fs/promises';
import { createServer, request as httpRequest } from 'node:http';
import { join } from 'node:path';
import { text as readResponse } from 'node:stream/consumers';
import test from 'node:test';
import { COMPLETED, closeServer, createTask, listenOnLoopback, startGatewayFixture, streamedText, waitFor } from './gateway_harness.mjs';

const MODEL = 'workspace-offline-model';
const WORKSPACES = ['workspace-a', 'workspace-b'];
const PROBES = new Map(WORKSPACES.map((id) => [`Read note.txt for ${id}.`, `Unique synthetic file in ${id}\n`]));

function chunk(response, delta, finishReason = null) {
  response.write(`data: ${JSON.stringify({
    id: 'chatcmpl-workspace-fixture', object: 'chat.completion.chunk', model: MODEL,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`);
}

async function startMock(beforeFirstReply) {
  const rounds = new Map();
  const errors = [];
  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.url, '/v1/chat/completions');
      assert.equal(request.headers.authorization, undefined);
      const pieces = [];
      for await (const piece of request) pieces.push(piece);
      const body = JSON.parse(Buffer.concat(pieces));
      assert.equal(body.model, MODEL);
      const prompt = body.messages.findLast((message) => message.role === 'user')?.content;
      assert.ok(PROBES.has(prompt), `Unexpected provider prompt ${JSON.stringify(prompt)}`);
      const round = (rounds.get(prompt) ?? 0) + 1;
      rounds.set(prompt, round);
      if (round === 1) {
        assert.ok(body.tools.some((tool) => tool.function?.name === 'read_file'));
        await beforeFirstReply?.();
      } else {
        assert.equal(round, 2);
        const tool = body.messages.at(-1);
        assert.equal(tool.role, 'tool');
        assert.equal(tool.tool_call_id, 'read_workspace_note');
        const data = JSON.parse(tool.content);
        assert.equal(data.success, true);
        assert.equal(data.path, 'note.txt');
        assert.equal(data.content, PROBES.get(prompt));
      }
      response.writeHead(200, { 'content-type': 'text/event-stream' });
      chunk(response, { role: 'assistant' });
      if (round === 1) {
        chunk(response, { tool_calls: [{
          index: 0, type: 'function', id: 'read_workspace_note',
          function: { name: 'read_file', arguments: JSON.stringify({ path: 'note.txt', type: 'read' }) },
        }] });
        chunk(response, {}, 'tool_calls');
      } else {
        chunk(response, { content: PROBES.get(prompt) });
        chunk(response, {}, 'stop');
      }
      response.end('data: [DONE]\n\n');
    } catch (error) {
      errors.push(error);
      if (!response.headersSent) response.writeHead(500);
      response.end(error.message);
    }
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, rounds, errors, close: () => closeServer(server) };
}

async function request(gateway, path, status, { method = 'GET', workspaceId, body } = {}) {
  const headers = { authorization: `Bearer ${await gateway.readToken()}` };
  if (workspaceId !== undefined) headers['x-astrion-workspace-id'] = workspaceId;
  if (body !== undefined) headers['content-type'] = 'application/json';
  const response = await fetch(gateway.baseUrl + path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(3_000),
  });
  const text = await response.text();
  assert.equal(response.status, status, `${method} ${path} ${workspaceId ?? '(no header)'}: ${text}`);
  return JSON.parse(text);
}

async function pauseBeforeBody(gateway, path, workspaceId, body, cleanupSignal) {
  const payload = JSON.stringify(body);
  const pending = httpRequest(gateway.baseUrl + path, {
    method: 'POST', signal: AbortSignal.any([cleanupSignal, AbortSignal.timeout(10_000)]),
    headers: {
      authorization: `Bearer ${await gateway.readToken()}`,
      'x-astrion-workspace-id': workspaceId,
      'content-type': 'application/json', 'content-length': Buffer.byteLength(payload),
      expect: '100-continue',
    },
  });
  const response = once(pending, 'response');
  // Hyper sends 100 Continue when the handler starts polling the body, after workspace binding.
  const continued = once(pending, 'continue');
  pending.flushHeaders();
  await Promise.race([
    continued,
    response.then(([reply]) => assert.fail(`${path} returned ${reply.statusCode} before reading the body`)),
  ]);
  return async (status) => {
    pending.end(payload);
    const [reply] = await response;
    const body = await readResponse(reply);
    assert.equal(reply.statusCode, status, `${path}: ${body}`);
    return JSON.parse(body);
  };
}

async function browserWorkspaceDelete(gateway) {
  const origin = gateway.baseUrl;
  const browserRequest = (path, init) => fetch(origin + path, { signal: AbortSignal.timeout(3_000), ...init });
  const cookie = (reply) => reply.headers.get('set-cookie').split(';', 1)[0];
  const challenge = await browserRequest('/api/csrf-token');
  assert.equal(challenge.status, 200);
  const login = await browserRequest('/host-login', {
    method: 'POST', body: '{}',
    headers: { origin, cookie: cookie(challenge), 'content-type': 'application/json', 'x-csrf-token': (await challenge.json()).token },
  });
  assert.equal(login.status, 200);
  const session = cookie(login);
  const csrf = await browserRequest('/api/csrf-token', { headers: { cookie: session } });
  assert.equal(csrf.status, 200);
  const headers = { origin, cookie: session, 'content-type': 'application/json', 'x-csrf-token': (await csrf.json()).token };
  return async (workspaceId, status) => {
    const reply = await browserRequest('/api/host/workspaces/delete', {
      method: 'POST', headers, body: JSON.stringify({ workspace_id: workspaceId }),
    });
    const body = await reply.text();
    assert.equal(reply.status, status, body);
    return JSON.parse(body);
  };
}

function ids(sessions) {
  return sessions.map((session) => session.id);
}

const SESSIONS = '/api/runtime/sessions';

function withoutModel() {
  return { endpoint: 'http://127.0.0.1:1/v1/chat/completions', close() {} };
}

test('registered workspaces isolate same-name file reads, histories and old requests after deregistration', { timeout: 30_000 }, async (t) => {
  const { gateway, mock, workspaces, workspaceCatalog } = await startGatewayFixture(t, {
    model: MODEL, startMock, workspaceIds: WORKSPACES,
  });
  const [a, b] = workspaces;
  const first = await gateway.listWorkspaces();
  assert.equal(first.current_workspace_id, a.workspace_id);
  assert.deepEqual(first.workspaces.map((item) => item.workspace_id), WORKSPACES);
  const taskIds = new Map();
  const sessionIds = new Map();
  try {
    for (const workspace of workspaces) {
      const id = workspace.workspace_id;
      gateway.workspaceId = id;
      const prompt = `Read note.txt for ${id}.`;
      await writeFile(join(workspace.path, 'note.txt'), PROBES.get(prompt));
      const { conversation_id: conversationId } = await gateway.createSession();
      const task = await createTask(gateway, conversationId, prompt);
      await task.until('read_file result returned to the offline model', (poll) => COMPLETED.has(poll.status));
      assert.equal(streamedText(task.events), PROBES.get(prompt));
      assert.equal(mock.rounds.get(prompt), 2);
      assert.deepEqual(ids(await gateway.listSessions()), [conversationId]);
      taskIds.set(id, task.taskId);
      sessionIds.set(id, conversationId);
    }
    assert.deepEqual(mock.errors, []);
  } catch (error) {
    if (mock.errors.length) throw new AggregateError([error, ...mock.errors], mock.errors[0].message);
    throw error;
  }

  const aHistory = `${SESSIONS}/${sessionIds.get(a.workspace_id)}/history`;
  const bHistory = `${SESSIONS}/${sessionIds.get(b.workspace_id)}/history`;
  const bTask = `/api/tasks/${taskIds.get(b.workspace_id)}`;
  await request(gateway, aHistory, 404, { workspaceId: b.workspace_id });
  await request(gateway, bHistory, 404, { workspaceId: a.workspace_id });
  await request(gateway, bTask, 404, { workspaceId: a.workspace_id });
  const bSelected = await request(gateway, `${SESSIONS}?workspace_id=${b.workspace_id}`, 200);
  assert.deepEqual(ids(bSelected.conversations), [sessionIds.get(b.workspace_id)]);
  const conflict = await request(gateway, `${SESSIONS}?workspace_id=${b.workspace_id}`, 400, { workspaceId: a.workspace_id });
  assert.match(conflict.error, /conflicting workspace/i);
  await request(gateway, `${SESSIONS}?workspace_id=${a.workspace_id}`, 200, { workspaceId: a.workspace_id });

  const bodyOnly = await request(gateway, SESSIONS, 200, {
    method: 'POST', body: { workspace_id: b.workspace_id },
  });
  assert.ok(bodyOnly.conversation_id);
  const expectedSessions = [[sessionIds.get(a.workspace_id)], [sessionIds.get(b.workspace_id), bodyOnly.conversation_id].sort()];
  const sessionLists = () => Promise.all([a, b].map(async ({ workspace_id: workspaceId }) => {
    return ids((await request(gateway, SESSIONS, 200, { workspaceId })).conversations).sort();
  }));
  assert.deepEqual(await sessionLists(), expectedSessions);
  await request(gateway, SESSIONS, 404, {
    method: 'POST', workspaceId: a.workspace_id, body: { workspace_id: b.workspace_id },
  });
  assert.deepEqual(await sessionLists(), expectedSessions);

  const replacement = `${workspaceCatalog}.replacement`;
  await writeFile(replacement, JSON.stringify({ default_workspace_id: a.workspace_id, workspaces: [a] }));
  await rename(replacement, workspaceCatalog);
  for (const path of [SESSIONS, bHistory, bTask]) {
    await request(gateway, path, 404, { workspaceId: b.workspace_id });
  }
  await request(gateway, SESSIONS, 404, { method: 'POST', workspaceId: b.workspace_id, body: {} });
  await request(gateway, `${SESSIONS}?workspace_id=${b.workspace_id}`, 404);
  assert.equal(await readFile(join(b.path, 'note.txt'), 'utf8'), PROBES.get(`Read note.txt for ${b.workspace_id}.`));
  gateway.workspaceId = '';
  assert.deepEqual((await gateway.listWorkspaces()).workspaces.map((item) => item.workspace_id), [a.workspace_id]);
  gateway.workspaceId = a.workspace_id;
  assert.deepEqual(ids(await gateway.listSessions()), [sessionIds.get(a.workspace_id)]);
  await request(gateway, aHistory, 200, { workspaceId: a.workspace_id });
});

test('same-ID recreation fences a pending task and a newly admitted task blocks deletion', { timeout: 30_000 }, async (t) => {
  const [workspaceId] = WORKSPACES;
  const firstReply = Promise.withResolvers();
  const pendingBody = new AbortController();
  t.after(() => {
    pendingBody.abort();
    firstReply.resolve();
  });
  const { gateway, mock, workspace, workspaceCatalog } = await startGatewayFixture(t, {
    model: MODEL, web: true, workspaceIds: [workspaceId], startMock: () => startMock(() => firstReply.promise),
  });
  const deleteWorkspace = await browserWorkspaceDelete(gateway);
  const originalNote = join(workspace, 'note.txt');
  await writeFile(originalNote, 'Original project\n');
  const sendOldTask = await pauseBeforeBody(gateway, '/api/tasks', workspaceId, { message: 'Stale task' }, pendingBody.signal);

  const removed = await deleteWorkspace(workspaceId, 200);
  assert.deepEqual(removed.data.workspaces, []);
  await request(gateway, SESSIONS, 404, { workspaceId });

  const replacement = join(workspace, '..', 'workspace-replacement');
  const create = () => request(gateway, '/api/host/workspaces/create', 200, {
    method: 'POST', workspaceId, body: { path: replacement, label: workspaceId },
  });
  const registrations = (await Promise.all([create(), create()])).map((reply) => reply.data);
  assert.deepEqual(registrations.map((data) => data.created).sort(), [false, true]);
  const registeredWorkspace = { workspace_id: workspaceId, label: workspaceId, path: replacement };
  assert.deepEqual(registrations.map((data) => data.workspace), [registeredWorkspace, registeredWorkspace]);
  const catalog = JSON.parse(await readFile(workspaceCatalog, 'utf8'));
  assert.equal(catalog.default_workspace_id, workspaceId);
  assert.deepEqual(catalog.workspaces, [registeredWorkspace]);
  assert.deepEqual((await request(gateway, SESSIONS, 200, { workspaceId })).conversations, []);

  const rejected = await sendOldTask(409);
  assert.match(rejected.error, /workspace binding changed/i);
  assert.deepEqual(await gateway.listSessions(), []);

  const prompt = `Read note.txt for ${workspaceId}.`;
  const replacementNote = join(replacement, 'note.txt');
  const replacementText = PROBES.get(prompt);
  await writeFile(replacementNote, replacementText);
  const { conversation_id: conversationId } = await gateway.createSession();
  const task = await createTask(gateway, conversationId, prompt);
  try {
    await waitFor('the replacement task to reach the provider', () => mock.rounds.get(prompt) === 1);
    const before = await readFile(workspaceCatalog, 'utf8');
    await deleteWorkspace(workspaceId, 409);
    assert.equal(await readFile(workspaceCatalog, 'utf8'), before);
  } finally {
    firstReply.resolve();
  }
  await task.until('replacement task completion', (poll) => COMPLETED.has(poll.status));
  assert.equal(streamedText(task.events), replacementText);
  assert.deepEqual(mock.errors, []);

  await deleteWorkspace(workspaceId, 200);
  await request(gateway, SESSIONS, 404, { workspaceId });
  assert.equal(await readFile(originalNote, 'utf8'), 'Original project\n');
  assert.equal(await readFile(replacementNote, 'utf8'), replacementText);
});

test('unregistered headless cwd waits for explicit confirmation in the CLI boot flow', { timeout: 30_000 }, async (t) => {
  const { gateway, workspace, workspaceCatalog } = await startGatewayFixture(t, {
    model: MODEL, startMock: withoutModel, workspaceIds: WORKSPACES, launchWorkspaceId: null,
  });
  const original = await readFile(workspaceCatalog, 'utf8');
  const listing = await gateway.listWorkspaces();
  assert.equal(listing.default_workspace_id, WORKSPACES[0]);
  assert.equal(listing.current_workspace_id ?? null, null);
  assert.ok(listing.workspaces.every((item) => !item.is_current && item.path !== workspace));
  await request(gateway, SESSIONS, 404);
  await request(gateway, SESSIONS, 404, { method: 'POST', body: {} });

  const { renderBootFlow } = await import('./gateway_boot_test_renderer.mjs');
  const terminal = await renderBootFlow(t, workspace);
  await waitFor('CLI workspace creation confirmation', () => terminal.output.includes(workspace)
    && /Create it as a workspace\?|是否将其创建为工作区/.test(terminal.output))
    .catch((error) => { throw new Error(`${error.message}\n${terminal.output}`, { cause: error }); });
  assert.equal(terminal.result, undefined);
  assert.equal(await readFile(workspaceCatalog, 'utf8'), original);
  for (const id of WORKSPACES) {
    assert.deepEqual((await request(gateway, SESSIONS, 200, { workspaceId: id })).conversations, []);
  }

  terminal.confirm();
  const result = await waitFor('CLI to register the cwd and create a session after Enter', () => {
    assert.equal(terminal.exitCode, undefined, terminal.output);
    return terminal.result;
  }).catch((error) => { throw new Error(`${error.message}\n${terminal.output}`, { cause: error }); });
  assert.equal(result.workspace.path, workspace);
  assert.equal(result.gateway.workspaceId, result.workspace.workspace_id);
  assert.ok(!WORKSPACES.includes(result.workspace.workspace_id));
  assert.deepEqual(ids(await result.gateway.listSessions()), [result.conversationId]);
  const registered = JSON.parse(await readFile(workspaceCatalog, 'utf8'));
  assert.equal(registered.default_workspace_id, WORKSPACES[0]);
  assert.deepEqual(registered.workspaces.map((item) => item.workspace_id), [...WORKSPACES, result.workspace.workspace_id]);
  assert.ok(registered.workspaces.some((item) => item.workspace_id === result.workspace.workspace_id && item.path === workspace));
  for (const id of WORKSPACES) {
    assert.deepEqual((await request(gateway, SESSIONS, 200, { workspaceId: id })).conversations, []);
  }
});

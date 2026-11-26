import assert from 'node:assert/strict';
import { access, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import {
  COMPLETED, closeServer, createTask, listenOnLoopback, startGatewayFixture, trackTask, waitFor,
} from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { startAppRuntimeFixture } from './app_runtime_fixture.mjs';

const model = 'isolated-runtime-model';
const alias = 'mcp__fixture__echo';
const childProcesses = process.env.ASTRION_RUNTIME_TEST_CHILD_PROCESSES === '1';
const question = (id, text) => tool(id, 'ask_user', { question: text, options: [{ id: 'yes', label: 'Yes' }] });

function tool(id, name, args) {
  return { id, type: 'function', function: { name, arguments: JSON.stringify(args) } };
}

function complete(response, reply, usage) {
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  const send = (delta, finish_reason) => response.write(`data: ${JSON.stringify({
    id: 'fixture-completion', model, object: 'chat.completion.chunk',
    choices: [{ index: 0, delta, finish_reason }],
  })}\n\n`);
  send({ role: 'assistant' }, null);
  if (Array.isArray(reply)) {
    send({ tool_calls: reply.map((call, index) => ({ ...call, index })) }, null);
    send({}, 'tool_calls');
  } else {
    send({ content: reply }, null);
    send({}, 'stop');
  }
  if (usage) response.write(`data: ${JSON.stringify({id: 'fixture-completion', model, choices: [], usage})}\n\n`);
  response.end('data: [DONE]\n\n');
}

async function startMock() {
  const mcpCalls = [];
  const requests = [];
  const mockErrors = [];
  const replies = new Map();
  const server = createServer(async (request, response) => {
    try {
      if (request.method === 'DELETE') {
        response.writeHead(204).end();
        return;
      }
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks));
      if (request.url === '/mcp') {
        const rpc = (result) => response.writeHead(200, { 'content-type': 'application/json' })
          .end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result }));
        if (body.method === 'initialize') {
          rpc({ protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'fixture', version: '1' } });
        } else if (body.method === 'notifications/initialized') {
          response.writeHead(202).end();
        } else if (body.method === 'tools/call') {
          assert.equal(body.params.name, 'echo');
          mcpCalls.push(body.params.arguments.text);
          rpc({ content: [
            { type: 'text', text: body.params.arguments.text },
            { type: 'image', mimeType: 'image/png', data: 'Zml4dHVyZS1pbWFnZQ==' },
          ], structuredContent: { echo: body.params.arguments.text } });
        } else {
          throw new Error(`Unexpected RPC ${body.method}`);
        }
        return;
      }

      assert.equal(request.url, '/v1/chat/completions');
      const messages = body.messages;
      const names = new Set(body.tools.map((tool) => tool.function.name));
      const latest = messages.at(-1);
      requests.push({ latest, names });
      if (latest.role === 'tool') {
        switch (latest.tool_call_id) {
          case 'question_two':
            assert.deepEqual(messages.slice(-2).map((message) => message.tool_call_id), ['question_one', 'question_two']);
            assert.equal(JSON.parse(latest.content).status, 'dismissed');
            complete(response, 'questions received');
            return;
          case 'plan':
            assert.equal(JSON.parse(latest.content).plan_content, '# Isolated approved plan\n');
            complete(response, 'plan approved');
            return;
          case 'main_mcp':
            assert.equal(JSON.parse(latest.content).structured_content.echo, 'main echo');
            assert(!latest.content.includes('Zml4dHVyZS1pbWFnZQ=='), 'text-only main model must not receive image bytes');
            complete(response, 'main MCP received');
            return;
          case 'monitor_read':
            assert.match(JSON.parse(latest.content).content, /isolated monitor fixture/);
            complete(response, 'file read');
            return;
          case 'approved_command':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            complete(response, 'command approved and executed');
            return;
          case 'external_read':
            assert.match(JSON.parse(latest.content).content, /external file fixture/);
            complete(response, 'authorized external file read');
            return;
          case 'external_write':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            complete(response, 'authorized external file written');
            return;
          case 'blocked_command':
            assert.match(JSON.parse(latest.content).error, /blocked/);
            complete(response, 'command blocked');
            return;
          case 'direct_command':
          case 'full_command':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            assert.match(JSON.parse(latest.content).output, /isolated-direct-test/);
            complete(response, 'approved command finished');
            return;
          case 'child_mcp':
            assert.equal(JSON.parse(latest.content).structured_content.echo, 'child echo');
            assert(!latest.content.includes('Zml4dHVyZS1pbWFnZQ=='), 'text-only child model must not receive image bytes');
            if (childProcesses) {
              const command = body.tools.find((item) => item.function.name === 'run_command');
              assert(command, 'child command schema must be present');
              for (const field of ['run_in_background', 'request_full_access', 'full_access_reason', 'working_dir']) {
                assert(!Object.hasOwn(command.function.parameters.properties, field), field);
              }
              complete(response, [tool('child_command', 'run_command', { timeout: 5,
                command: 'test -f ../../../notes.txt && printf child-owned > child-owned.txt && '
                  + 'if (printf forbidden > ../../../notes.txt); then exit 12; else printf narrow-write-verified; fi',
              })]);
              return;
            }
            complete(response, 'child MCP received');
            return;
          case 'child_command':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            assert.match(JSON.parse(latest.content).output, /narrow-write-verified/);
            complete(response, [tool('child_terminal', 'terminal_session', { action: 'open', session_name: 'fixture-shared' })]);
            return;
          case 'child_terminal':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            complete(response, [tool('child_input', 'terminal_input', { session_name: 'fixture-shared', command: 'printf isolated-child-terminal-output', output_wait: 3 })]);
            return;
          case 'child_input':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            assert.match(JSON.parse(latest.content).output, /isolated-child-terminal-output/);
            complete(response, [tool('child_after_compression','read_file',{path:'child-owned.txt'})], {prompt_tokens:10100,completion_tokens:1,total_tokens:10101});
            return;
          case 'child_after_compression':
            assert.match(latest.content,/child-owned/);
            assert(messages.some(message => message.role === 'system' && message.content.includes('压缩摘要')), 'next child model request must include the durable summary');
            complete(response, 'child MCP received with isolated commands and terminal');
            return;
          case 'create_child':
            assert.equal(JSON.parse(latest.content).success, true, latest.content);
            if (childProcesses) {
              assert.equal(messages.at(-2).tool_call_id, 'parent_terminal');
              assert.equal(JSON.parse(messages.at(-2).content).success, true, messages.at(-2).content);
            }
            complete(response, 'child created');
            return;
        }
      }
      if (typeof latest.content === 'string' && latest.content.includes('child MCP received')) {
        complete(response, 'child completion notice consumed');
        return;
      }
      const task = latest.content;
      if (task === 'ask two questions' || task === 'questions to cancel') {
        assert(names.has('ask_user'));
        complete(response, [question('question_one', 'First fixture question'), question('question_two', 'Second fixture question')]);
      } else if (task === 'submit fixture plan') {
        assert(names.has('submit_plan'));
        complete(response, [tool('plan', 'submit_plan', { plan_file: '.astrion/plan/test.md' })]);
      } else if (task === 'run main MCP') {
        assert(names.has(alias));
        complete(response, [tool('main_mcp', alias, { text: 'main echo', intent: 'test' })]);
      } else if (task === 'read monitor fixture') {
        assert(names.has('read_file'));
        complete(response, [tool('monitor_read', 'read_file', { path: 'notes.txt' })]);
      } else if (task === 'request command approval') {
        assert(names.has('run_command'));
        complete(response, [tool('approved_command', 'run_command', {
          command: 'printf bundled-approved > approved-command.txt', timeout: 5,
        })]);
      } else if (task?.startsWith?.('read authorized external ')) {
        complete(response, [tool('external_read', 'read_file', { path: task.slice('read authorized external '.length) })]);
      } else if (task?.startsWith?.('write authorized external ')) {
        complete(response, [tool('external_write', 'write_file', {
          file_path: task.slice('write authorized external '.length), content: 'authorized fixture write',
        })]);
      } else if (task === 'try blocked fixture command') {
        complete(response, [tool('blocked_command', 'run_command', { command: 'printf block-fixture-command', timeout: 5 })]);
      } else if (task === 'run direct fixture command') {
        complete(response, [tool('direct_command', 'run_command', { command: 'printf isolated-direct-test', timeout: 5 })]);
      } else if (task === 'run full access fixture command') {
        complete(response, [tool('full_command', 'run_command', {
          command: 'printf isolated-direct-test', timeout: 5, request_full_access: true, full_access_reason: 'Isolated runtime test',
        })]);
      } else if (task === 'hold final reply for mode' || task === 'hold final reply for compression') {
        await new Promise(resolve => {
          const timer = setTimeout(resolve, 12_000);
          response.once('close', () => { clearTimeout(timer); resolve(); });
          replies.set(task, () => { clearTimeout(timer); resolve(); });
        });
        replies.delete(task);
        if (!response.destroyed) complete(response, 'final reply with no tools');
      } else if (typeof task === 'string' && task.includes('\n\nchild task fixture\n\n')) {
        assert(names.has(alias));
        assert(names.has('ask_master'));
        assert(!names.has('create_sub_agent'));
        complete(response, [tool('child_mcp', alias, { text: 'child echo', intent: 'test' })]);
      } else if (task === 'create fixture child') {
        assert(names.has('create_sub_agent'));
        const calls = [];
        if (childProcesses) calls.push(tool('parent_terminal', 'terminal_session', { action: 'open', session_name: 'fixture-shared' }));
        calls.push(tool('create_child', 'create_sub_agent', {
          name: 'fixture-child', summary: 'Call an isolated local MCP server',
          task: 'child task fixture', intelligence: 'same', access_level: 'working_dir_write',
        }));
        complete(response, calls);
      } else {
        throw new Error(`Unexpected model message ${JSON.stringify(latest)}`);
      }
    } catch (error) {
      mockErrors.push(error);
      if (!response.headersSent) response.writeHead(500, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ error: { message: error.message } }));
    }
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, mcpUrl: `http://127.0.0.1:${port}/mcp`,
    requests, mcpCalls, mockErrors, replies, close: () => closeServer(server) };
}

test('authenticated Rust tasks batch questions, approve a plan, and run main and child MCP', { timeout: 80_000 }, async (t) => {
  const options = {
    model, startMock, web: true, workspaceIds: ['default', 'another-workspace'],
    async prepare({ workspace, dataDir, mock }) {
      const plan = join(workspace, '.astrion', 'plan');
      await mkdir(plan, { recursive: true });
      await writeFile(join(plan, 'test.md'), '# Isolated approved plan\n');
      await writeFile(join(workspace, 'notes.txt'), 'isolated monitor fixture\n');
      await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({
        default_work_mode: 'plan', default_permission_mode: 'approval', auto_generate_title: false,
      }));
      await writeFile(join(dataDir, 'mcp_servers.json'), JSON.stringify({
        servers: [{ id: 'fixture', transport: 'streamable_http', url: mock.mcpUrl, enabled: true,
          tools_cache_updated_at: 'generated fixture', tools_cache: [{ name: 'echo',
            inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] } }] }],
        access: { defaults: { main: ['fixture'], sub: ['fixture'] } },
      }));
    },
  };
  const app = process.env.ASTRION_RUNTIME_TEST_APP;
  const fixture = app ? await startAppRuntimeFixture(t, app, options) : await startGatewayFixture(t, options);
  const { gateway, mock } = fixture;
  const origin = gateway.baseUrl;
  const token = await gateway.readToken();
  const request = (path, options) => fetch(origin + path, { signal: AbortSignal.timeout(5_000), ...options });
  async function api(path, { method = 'GET', body, workspace = 'default', expected = 200 } = {}) {
    const response = await request(path, {
      method, headers: { authorization: `Bearer ${token}`, 'x-astrion-workspace-id': workspace, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json();
    assert.equal(response.status, expected, JSON.stringify(payload));
    return payload;
  }
  async function browserLogin() {
    const challenge = await request('/api/csrf-token');
    const cookie = challenge.headers.get('set-cookie').split(';', 1)[0];
    const { token } = await challenge.json();
    const response = await request('/host-login', { method: 'POST',
      headers: { cookie, origin, 'content-type': 'application/json', 'x-csrf-token': token }, body: '{}' });
    assert.equal(response.status, 200);
    return response.headers.get('set-cookie').split(';', 1)[0];
  }
  const cookie = await browserLogin();
  const bootstrap = async (id) => {
    const response = await request(`/api/conversations/${id}/bootstrap`, {
      headers: { cookie, 'x-astrion-workspace-id': 'default' },
    });
    assert.equal(response.status, 200);
    return (await response.json()).data;
  };
  const done = (task) => task.until('successful task completion', (poll) => COMPLETED.has(poll.status));

  const asked = await createTask(gateway, null, 'ask two questions');
  await asked.until('batched questions', (poll) => poll.events.some((event) => event.type === 'user_questions_required'));
  const path = `/api/user-questions/pending?conversation_id=${asked.conversationId}`;
  const { items } = await api(path);
  assert.equal(items.length, 2);
  assert.equal((await api(path, { workspace: 'another-workspace' })).items.length, 0);
  const questionSnapshot = await bootstrap(asked.conversationId);
  assert.deepEqual(questionSnapshot.display.state.pending_user_questions.map((row) => row.question_id), items.map((row) => row.question_id));
  await api(`/api/user-questions/${items[0].question_id}/answer`, { method: 'POST', body: { selected_option_id: 'yes' } });
  assert.equal((await api(path)).items.length, 1);
  await api(`/api/user-questions/${items[1].question_id}/answer`, { method: 'POST', body: { dismissed: true } });
  await done(asked);

  const cancelled = await createTask(gateway, null, 'questions to cancel');
  await cancelled.until('pending questions before cancel', (poll) => poll.events.some((event) => event.type === 'user_questions_required'));
  await api(`/api/tasks/${cancelled.taskId}/cancel`, { method: 'POST', body: {} });
  await cancelled.until('stopped task', (poll) => poll.status === 'stopped');
  assert.deepEqual((await api(`/api/user-questions/pending?conversation_id=${cancelled.conversationId}`)).items, []);

  const planning = await createTask(gateway, null, 'submit fixture plan');
  await planning.until('plan approval', (poll) => poll.events.some((event) => event.type === 'plan_approval_required'));
  const approval = (await api(`/api/plan-approvals/pending?conversation_id=${planning.conversationId}`)).items[0];
  assert.equal((await bootstrap(planning.conversationId)).display.state.pending_plan_approvals[0].approval_id, approval.approval_id);
  await api(`/api/plan-approvals/${approval.approval_id}/answer`, { method: 'POST', body: { approved: true, comment: 'approved fixture' } });
  await done(planning);
  const afterApproval = await bootstrap(planning.conversationId);
  assert.equal(afterApproval.meta.work_mode, 'execute');
  assert.equal(afterApproval.meta.permission_mode, 'approval');

  const mcp = await createTask(gateway, null, 'run main MCP');
  await done(mcp);
  const monitor = await createTask(gateway, null, 'read monitor fixture');
  await done(monitor);
  assert.deepEqual((await api(`/api/background_commands?conversation_id=${monitor.conversationId}`)).data, []);
  const terminalRoute = `/api/terminals?conversation_id=${monitor.conversationId}`;
  assert.equal((await request(terminalRoute)).status, 401);
  const terminals = await request(terminalRoute, { headers: { cookie, 'x-astrion-workspace-id': 'default' } });
  assert.equal(terminals.status, 200);
  assert.deepEqual((await terminals.json()).sessions, []);
  const started = monitor.events.find((event) => event.type === 'tool_start').data;
  const finished = monitor.events.find((event) => event.type === 'update_action').data;
  assert.match(started.execution_id, /^tool_[a-f0-9]+$/);
  assert.equal(finished.execution_id, started.execution_id);
  const monitorPath = `/api/gui/monitor_snapshot?executionId=${started.execution_id}&stage=after`;
  assert.equal((await request(monitorPath)).status, 401);
  const snapshot = await request(monitorPath, { headers: { cookie } });
  assert.equal(snapshot.status, 200);
  assert.match((await snapshot.json()).snapshot.content, /isolated monitor fixture/);
  if (childProcesses) await api('/api/personalization', {method:'POST', body:{sub_agent_compress_threshold_tokens:10000}});
  const child = await createTask(gateway, null, 'create fixture child');
  await done(child);
  await waitFor('child completion notice', async () => {
    assert.deepEqual(mock.mockErrors, []);
    const status = (await api(`/api/conversations/${child.conversationId}/running-status`)).data;
    if (status.main_task_id && status.main_task_id !== child.taskId) {
      await done(trackTask(gateway, status.main_task_id, child.conversationId));
    }
    return mock.requests.some(({ latest }) => typeof latest.content === 'string' && latest.content.includes('child MCP received'));
  }, 12_000);
  assert.deepEqual(mock.mockErrors, []);
  assert.deepEqual(mock.mcpCalls, ['main echo', 'child echo']);
  if (childProcesses) {
    assert.equal(await readFile(join(fixture.workspace, '.astrion', 'sub_agent_work', 'agent_1', 'child-owned.txt'), 'utf8'), 'child-owned');
    assert.equal(await readFile(join(fixture.workspace, 'notes.txt'), 'utf8'), 'isolated monitor fixture\n');
    const output = await request(`/api/terminals/fixture-shared/output?conversation_id=${child.conversationId}`,
      { headers: { cookie, 'x-astrion-workspace-id': 'default' } });
    assert.equal(output.status, 200);
    assert(!JSON.stringify(await output.json()).includes('isolated-child-terminal-output'));
  }

  const testPolicies = process.env.ASTRION_RUNTIME_TEST_HOST_POLICY === '1';
  if (app) {
    const views = await Promise.all([fixture.openView('/settings/workspace'), fixture.openView('/settings/workspace')]);
    const [first, stale] = views;
    const store = "document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia._s.get('personalization')";
    try {
      await Promise.all(views.map(view => waitFor('settings window to load the saved default', () =>
        view.evaluate(`${store}?.loaded && ${store}.form.default_work_mode === 'plan'`))));
      await first.evaluate(`(async () => {
        const settings = ${store};
        settings.setDefaultWorkMode('execute');
        const saved = await settings.flushAutoSave();
        return saved?.ok === true;
      })()`).then(saved => assert.equal(saved, true));
      assert.equal(await stale.evaluate(`${store}.form.default_work_mode`), 'plan');
      const writes = await stale.evaluate(`(async () => {
        const settings = ${store};
        const postBodies = [];
        const fetchOriginal = window.fetch;
        window.fetch = (url, options) => {
          if (url === '/api/personalization' && options?.method === 'POST') {
            postBodies.push(JSON.parse(options.body));
          }
          return fetchOriginal(url, options);
        };
        try {
          settings.setDefaultPermissionMode('approval');
          settings.setVersioningBackupMode('full');
          const saved = await settings.flushAutoSave();
          return saved?.ok ? postBodies : null;
        } finally {
          window.fetch = fetchOriginal;
        }
      })()`);
      assert.deepEqual(writes, [{ default_permission_mode: 'approval', versioning_backup_mode: 'full' }]);
      const saved = (await api('/api/personalization')).data;
      assert.equal(saved.default_work_mode, 'execute');
      assert.equal(saved.default_permission_mode, 'approval');
      assert.equal(saved.versioning_backup_mode, 'full');
    } finally {
      await Promise.allSettled(views.map(view => view.close()));
    }
  }
  if ((app || testPolicies) && process.platform === 'darwin') {
    assert.equal((await api('/api/personalization', { method: 'POST',
      body: { default_work_mode: 'execute', default_permission_mode: 'approval', versioning_backup_mode: 'shallow' } })).data.default_work_mode, 'execute');
    const command = await createTask(gateway, null, 'request command approval');
    await command.until('sandbox write approval', (poll) => poll.events.some((event) => event.type === 'tool_approval_required'));
    const dest = join(fixture.workspace, 'approved-command.txt');
    await assert.rejects(access(dest), { code: 'ENOENT' });
    const csrf = await request('/api/csrf-token', { headers: { cookie } }).then(response => response.json());
    const browser = async (path, body, expected = 200) => {
      const response = await request(path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { cookie, origin, 'x-csrf-token': csrf.token, 'x-astrion-workspace-id': 'default', 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const payload = await response.json();
      assert.equal(response.status, expected, JSON.stringify(payload));
      return payload;
    };
    const approve = async (task, type, name = 'run_command') => {
      const pending = (await api(`/api/tool-approvals/pending?conversation_id=${task.conversationId}`)).items;
      assert.equal(pending.length, 1);
      assert.equal(pending[0].tool_name, name);
      assert.equal(pending[0].approval_type, type);
      if (testPolicies) {
        const state = (await bootstrap(task.conversationId)).display.state;
        assert.equal(state.pending_tool_approvals[0].approval_id, pending[0].approval_id);
      }
      await browser(`/api/tool-approvals/${pending[0].approval_id}/decision`, { decision: 'approved' });
    };
    if (testPolicies) {
      const pending = await browser('/api/network-permission', { mode: 'none', conversation_id: command.conversationId });
      assert.equal(pending.pending_mode, 'none');
    }
    await approve(command, 'sandbox_write');
    await done(command);
    assert.equal(await readFile(dest, 'utf8'), 'bundled-approved');
    assert.equal((await api('/api/personalization')).data.default_work_mode, 'execute');
    if (testPolicies) {
      const current = await browser(`/api/network-permission?conversation_id=${command.conversationId}`);
      assert.equal(current.mode, 'none');
      assert.equal(current.pending_mode, null);
      const isolatedExternal = join(fixture.workspace, '..', 'policy-external');
      await mkdir(isolatedExternal);
      const outside = join(isolatedExternal, 'authorized.txt');
      await writeFile(outside, 'external file fixture');
      assert.equal((await api('/api/sandbox/status')).data.sandbox_available, true);
      assert.equal((await api('/api/sandbox/setup/status')).data.phase, 'idle');
      await api('/api/sandbox/setup', { method: 'POST', body: {}, expected: 409 });
      const authorization = await api('/api/path-authorization', { method: 'POST',
        body: { scope: 'workspace', writable_paths: [isolatedExternal], readable_extra_paths: [] },
      });
      assert.deepEqual(authorization.workspace_writable_paths, [isolatedExternal]);
      assert.deepEqual((await api('/api/path-authorization', { workspace: 'another-workspace' })).workspace_writable_paths, []);
      await api('/api/path-authorization', { method: 'POST',
        body: { writable_paths: ['/'], readable_extra_paths: [] }, expected: 400 });
      const authorizedRead = await createTask(gateway, null, `read authorized external ${outside}`);
      await done(authorizedRead);
      assert.equal((await bootstrap(authorizedRead.conversationId)).meta.work_mode, 'execute');
      const writePath = join(isolatedExternal, 'written.txt');
      const write = await createTask(gateway, authorizedRead.conversationId, `write authorized external ${writePath}`);
      await write.until('authorized write approval', (poll) => poll.events.some((event) => event.type === 'tool_approval_required'));
      await approve(write, 'sandbox_write', 'write_file');
      await done(write);
      assert.equal(await readFile(writePath, 'utf8'), 'authorized fixture write');

      const rules = await api('/api/command-blocking', { method: 'POST', body: {
        rules: [' block-fixture-command ', 'BLOCK-FIXTURE-COMMAND'],
      } });
      assert.deepEqual(rules.rules, ['block-fixture-command']);
      await done(await createTask(gateway, null, 'try blocked fixture command'));
      const conversation = command.conversationId;
      const network = await browser('/api/network-permission', { mode: 'none', conversation_id: conversation });
      assert.equal(network.mode, 'none');
      assert.equal((await browser(`/api/network-permission?conversation_id=${conversation}`)).enabled, true);
      const direct = await browser('/api/execution-mode', { mode: 'direct', conversation_id: conversation });
      assert.equal(direct.state.mode, 'direct');
      const directTask = await createTask(gateway, conversation, 'run direct fixture command');
      await directTask.until('direct command approval', (poll) => poll.events.some((event) => event.type === 'tool_approval_required'));
      await approve(directTask, 'direct_execution');
      await done(directTask);
      await browser('/api/execution-mode', { mode: 'sandbox', conversation_id: conversation });
      const onceTask = await createTask(gateway, conversation, 'run full access fixture command');
      await onceTask.until('one-time full access approval', (poll) => poll.events.some((event) => event.type === 'tool_approval_required'));
      await approve(onceTask, 'full_access');
      await done(onceTask);
      assert.equal((await browser(`/api/execution-mode?conversation_id=${conversation}`)).state.mode, 'sandbox');

      const noTool = await createTask(gateway, null, 'hold final reply for mode');
      const releaseFinal = await waitFor('held final response', () => mock.replies.get('hold final reply for mode'));
      const permission = await browser('/api/permission-mode', { mode: 'readonly', conversation_id: noTool.conversationId });
      assert.equal(permission.pending_mode, 'readonly');
      releaseFinal();
      await done(noTool);
      const finishedPermission = await browser(`/api/permission-mode?conversation_id=${noTool.conversationId}`);
      assert.equal(finishedPermission.mode, 'readonly');
      assert.equal(finishedPermission.pending_mode, null);

      const compress = await createTask(gateway, null, 'hold final reply for compression');
      await waitFor('held compression response', () => mock.replies.has('hold final reply for compression'));
      const base = `/api/conversations/${compress.conversationId}`;
      await browser(`${base}/compress`, {}, 202);
      const payload = await bootstrap(compress.conversationId);
      assert.equal(payload.compression.stage, 'waiting_for_tools');
      assert.equal(payload.display.state.compression.job_id, compress.taskId);
      const status = await browser(`/api/status?conversation_id=${compress.conversationId}`);
      assert.equal(status.execution_mode_enabled, true);
      assert.equal(status.network_permission_enabled, true);
      assert.deepEqual(status.conversation.compression, payload.compression);
      await api(`/api/tasks/${compress.taskId}/cancel`, { method: 'POST', body: {} });
      await compress.until('compression fixture stopped', (poll) => poll.status === 'stopped');
      assert.equal((await bootstrap(compress.conversationId)).compression.in_progress, false);
    }
    assert.deepEqual(mock.mockErrors, []);
  }
});

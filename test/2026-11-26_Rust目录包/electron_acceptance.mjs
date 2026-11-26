import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { open, mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { DatabaseSync } from 'node:sqlite';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { closeServer, listenOnLoopback, waitFor } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const app = resolve(process.argv[2] || join(repo, 'desktop-electron/dist-rust/mac-arm64/Astrion.app'));
const model = 'desktop-offline';
const workspaceId = 'python-workspace';
const first = 'Read note.txt with read_file.';
const firstReply = 'I read the workspace fixture.';
const next = 'Confirm this is the same conversation.';
const nextReply = 'This is the same conversation.';
const fixture = 'desktop fixture contents';
const toolId = 'call_desktop_read';
const longHistory = process.env.ASTRION_TEST_LONG_HISTORY === '1';

function syntheticHistory() {
  return Array.from({length:205}, (_, i) => {
    const id = 'old_tool_' + i;
    return [
      {role:'user', content:'Earlier isolated request '+i},
      {role:'assistant', content:'', tool_calls:[{id,type:'function',function:{name:'read_file',arguments:'{"path":"fixture.txt"}'}}]},
      {role:'tool', name:'read_file', tool_call_id:id, content:'Synthetic result '+i, metadata:{fixture_payload:'A'.repeat(128 * 1024)}},
    ].map((message, part) => ({...message, message_id:'old_'+i+'_'+part}));
  }).flat();
}

function parentPid(pid) {
  return Number(execFileSync('ps', ['-o', 'ppid=', '-p', String(pid)], {encoding: 'utf8'}).trim());
}

function sse(response, delta, reason = null) {
  response.write(`data: ${JSON.stringify({
    id: 'chatcmpl-desktop-acceptance', object: 'chat.completion.chunk', model,
    choices: [{ index: 0, delta, finish_reason: reason }]
  })}\n\n`);
}

async function startModel() {
  const calls = [];
  const errors = [];
  const timing = {};
  const server = createServer(async (request, response) => {
    const started = performance.now();
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.url, '/v1/chat/completions');
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString());
      const { messages } = body;
      const last = messages.at(-1);
      assert.equal(body.model, model);
      assert.equal(body.stream, true);
      const step = messages.some(message => message.content === next) ? 'next' :
        last.role === 'tool' ? 'tool-result' : 'initial';
      assert.ok(messages.some(message => message.content === 'From Python'));
      assert.ok(messages.some(message => message.content === 'Preserved Python response'));
      assert.equal(messages[0].content, 'Persisted Python Agent system prompt for the offline fixture.');
      if (step === 'initial') {
        assert.equal(last.content, first);
        assert.ok(body.tools?.some(tool => tool.function?.name === 'read_file'));
      } else if (step === 'tool-result') {
        assert.equal(last.tool_call_id, toolId);
        const result = JSON.parse(last.content);
        assert.equal(result.success, true);
        assert.equal(result.path, 'note.txt');
        assert.ok(result.content.includes(fixture));
      } else {
        assert.equal(last.content, next);
        assert.ok(messages.some(message => message.content === firstReply));
        assert.ok(messages.some(message => message.tool_call_id === toolId));
      }
      if (longHistory) assert.ok(messages.some(message=>message.tool_call_id === 'old_tool_204'));
      timing[step] = {requestStart:started, replyAt:performance.now()};
      calls.push(step);
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      sse(response, { role: 'assistant' });
      if (step === 'initial') {
        sse(response, { tool_calls: [{ index: 0, id: toolId, type: 'function',
          function: { name: 'read_file', arguments: JSON.stringify({ path: 'note.txt', type: 'read' }) }
        }] });
        sse(response, {}, 'tool_calls');
      } else {
        sse(response, { content: step === 'next' ? nextReply : firstReply });
        sse(response, {}, 'stop');
      }
      response.end('data: [DONE]\n\n');
    } catch (error) {
      errors.push(error.message);
      response.writeHead(500).end(error.message);
    }
  });
  const port = await listenOnLoopback(server);
  return { url: `http://127.0.0.1:${port}/v1`, calls, errors, timing, close: () => closeServer(server) };
}

const electron = "process.getBuiltinModule('node:module').createRequire(process.resourcesPath + '/app.asar/package.json')('electron')";
async function inspector(url) {
  const ws = new WebSocket(url);
  await new Promise((ready, reject) => {
    ws.addEventListener('open', ready, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  const pending = new Map();
  let id = 0;
  ws.addEventListener('message', ({ data }) => {
    const reply = JSON.parse(data);
    pending.get(reply.id)?.(reply);
  });
  return {
    close() { ws.close(); },
    async run(expression) {
      const requestId = ++id;
      const reply = await Promise.race([
        new Promise(resolveReply => {
          pending.set(requestId, resolveReply);
          ws.send(JSON.stringify({ id: requestId, method: 'Runtime.evaluate',
            params: { expression, awaitPromise: true, returnByValue: true } }));
        }),
        delay(3500, null, { ref: false }),
      ]).finally(() => pending.delete(requestId));
      assert.ok(reply, 'Electron DevTools did not answer');
      assert.ok(!reply.error && !reply.result?.exceptionDetails, JSON.stringify(reply));
      return reply.result?.result.value;
    }
  };
}

async function load(info, attempt) {
  const lease = createServer();
  const debugPort = await listenOnLoopback(lease);
  await closeServer(lease);
  const logPath = join(info.root, `electron-${attempt}.log`);
  const log = await open(logPath, 'w');
  const child = spawn(join(app, 'Contents/MacOS/Astrion'), [`--inspect=${debugPort}`], {
    env: info.env,
    stdio: ['ignore', log.fd, log.fd],
  });
  await log.close();
  const exited = new Promise(done => child.once('exit', code => done(code)));
  let devtools;
  let backendPid;
  let origin;
  async function stop() {
    const closing = child.exitCode === null && child.signalCode === null;
    if (closing && devtools) {
      await devtools.run(`(() => {
        const app = ${electron}.app;
        app.once('before-quit', () => console.info('[desktop-acceptance] before-quit'));
        app.once('will-quit', () => console.info('[desktop-acceptance] will-quit'));
        app.quit();
        return true;
      })()`).catch(() => {});
    }
    devtools?.close();
    if (!closing) return child.exitCode;
    const code = await Promise.race([exited, delay(10000, false, { ref: false })]);
    if (code === false) {
      child.kill('SIGTERM');
      await Promise.race([exited, delay(3000, false, { ref: false })]);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      if (backendPid) {
        try {
          if (parentPid(backendPid) === child.pid) process.kill(backendPid, 'SIGTERM');
        } catch {}
      }
      throw new Error(`Electron failed to quit normally. See ${logPath}`);
    }
    if (origin) await waitFor('bundled backend to stop', async () => {
      try {
        await fetch(`${origin}/api/host-mode-enabled`, { signal: AbortSignal.timeout(500) });
        return false;
      } catch { return true; }
    });
    return code;
  }
  try {
    const debugUrl = await waitFor('Electron DevTools', async () => {
      if (child.exitCode !== null) throw new Error(`Electron exited ${child.exitCode}. See ${logPath}`);
      return (await readFile(logPath, 'utf8')).match(/Debugger listening on (ws:\/\/127\.0\.0\.1:\d+\/[0-9a-f-]+)/)?.[1];
    }, 20000);
    assert.equal(new URL(debugUrl).port, String(debugPort));
    devtools = await inspector(debugUrl);
    const status = await waitFor('the packaged Rust listener and Electron renderer', async () => {
      const config = await readFile(join(info.profile, 'backend-port.json'), 'utf8').catch(() => '');
      if (!config) return false;
      const { port } = JSON.parse(config);
      const base = `http://127.0.0.1:${port}`;
      const status = await fetch(`${base}/api/host-mode-enabled`, { signal: AbortSignal.timeout(1000) })
        .then(response => response.ok && response.json()).catch(() => false);
      if (!status) return false;
      assert.equal(status.backend, 'rust');
      assert.equal(parentPid(status.pid), child.pid);
      backendPid = status.pid;
      origin = base;
      const renderer = await devtools.run(`${electron}.webContents.getAllWebContents().some(
        contents => contents.getURL().startsWith(${JSON.stringify(base + '/chrome')}) && !contents.isLoading())`);
      return renderer && status;
    }, 30000);
    return {
      status, origin, pid: child.pid, stop,
      diagnostics() {
        return devtools.run(`Promise.all(${electron}.webContents.getAllWebContents().map(async contents => ({
          url: contents.getURL(),
          state: await contents.executeJavaScript(${JSON.stringify(`({
            ready: document.readyState,
            visible: document.visibilityState,
            body: document.body?.innerText.slice(0, 1200)
          })`)}, true).catch(error => String(error)),
        })))`);
      },
      async view(path, expression) {
        const originLiteral = JSON.stringify(origin);
        return devtools.run(`(async () => {
          for (const contents of ${electron}.webContents.getAllWebContents()) {
            const address = contents.getURL();
            if (!address.startsWith(${originLiteral})) continue;
            const location = new URL(address);
            if (location.origin !== ${originLiteral} || !location.pathname.startsWith(${JSON.stringify(path)})) continue;
            const value = await contents.executeJavaScript(${JSON.stringify(expression)}, true).catch(() => null);
            if (value) return value;
          }
          return null;
        })()`);
      },
    };
  } catch (error) {
    await stop().catch(() => {});
    throw error;
  }
}

const migrationDialog = `[...document.querySelectorAll('.ui-dialog')].find(dialog =>
  /迁移旧版对话记录|Migrate older conversations/.test(dialog.querySelector('h2')?.textContent || ''))`;
const accepted = `document.querySelector('main')?.innerText.includes(${JSON.stringify(firstReply)})`;
const persisted = `document.querySelector('main')?.innerText.includes(${JSON.stringify(nextReply)})`;

async function send(runtime, path, message) {
  await waitFor('editable conversation composer', () => runtime.view(path, `(() => {
    const editor = document.querySelector('.stadium-input-rich [contenteditable="true"]');
    const button = document.querySelector('.send-btn');
    if (!editor || !button || button.classList.contains('is-stopping')) return false;
    editor.focus();
    return document.execCommand('insertText', false, ${JSON.stringify(message)});
  })()`), 15000);
  await waitFor('enabled Send button', () => runtime.view(path, `(() => {
    const button = document.querySelector('.send-btn');
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`), 15000);
}

const root = await mkdtemp(join(tmpdir(), 'astrion-desktop-1.0-'));
const data = join(root, 'data');
const profile = join(root, 'profile');
const workspace = join(root, 'workspace');
await Promise.all([data, profile, workspace].map(path => mkdir(path)));
const mock = await startModel();
let runtime;
try {
  const legacySeed = join(repo, 'test/2026-11-24_Rust后端/cli_python_fixture.py');
  const seed = execFileSync('python3.11', ['-B', legacySeed, 'seed', data, workspace], { encoding: 'utf8' });
  const { id, path: source } = JSON.parse(seed.trim().split('\n').at(-1));
  const dataDir = join(data, 'host/data');
  const childId = 'sub_gui_history';
  const childDir = join(dataDir, 'sub_agent_tasks', childId);
  const child = {task_id: childId, conversation_id: id, workspace_root: workspace, working_dir: workspace, name: 'archived', status: 'terminated'};
  const childHistory = [{role: 'system', content: 'fixture child system'}, {role: 'user', content: 'child original', future: [3]}];
  const legacyFiles = new Map([
    [join(dataDir, 'sub_agent_runtime.json'), {version: 1, tasks: {[childId]: child}, states: {}}],
    [join(childDir, 'conversation.json'), {task_id: childId, messages: childHistory, unknown: {round: 7}}]
  ]);
  await mkdir(childDir, {recursive: true});
  for (const [folder, kind] of [['approval_agent','approval'], ['goal_review_agent','goal_review'], ['workflow_review_agent','workflow_review']]) {
    const logs = join(data,'host/logs',folder);
    await mkdir(logs,{recursive:true});
    legacyFiles.set(join(logs, kind + '_1700000000000.json'), {messages: [{role:'user',content:kind}],trace:[],final_result:{decision:'approved'},opaque:[1,null]});
  }
  for (const [file, value] of legacyFiles) await writeFile(file,JSON.stringify(value));
  const saved = JSON.parse(await readFile(source, 'utf8'));
  saved.metadata.model_key = model;
  saved.metadata.run_mode = 'fast';
  saved.metadata.thinking_mode = false;
  saved.metadata.permission_mode = 'readonly';
  saved.metadata.work_mode = 'execute';
  if (longHistory) {
    saved.messages.unshift(...syntheticHistory());
    saved.metadata.total_messages = saved.messages.length;
    saved.metadata.total_tools = 205;
  }
  const original = JSON.stringify(saved);
  await writeFile(source, original);
  await writeFile(join(workspace, 'note.txt'), fixture);
  await writeFile(join(data, 'config/custom_models.json'), JSON.stringify({ models: [{
    model_name: model, model_id: model, url: mock.url, apikey: 'synthetic-local-only',
    reasoning_capability: 'fast', context_window: 32768, max_output_tokens: 2048
  }] }));
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('ASTRION_')));
  Object.assign(env, {
    ASTRION_DESKTOP_DATA_ROOT: data,
    ASTRION_ELECTRON_USER_DATA: profile,
    ASTRION_DESKTOP_INITIAL_WORKSPACE: workspace,
    ASTRION_QUICK_SETTINGS_FILE: join(profile, 'quick-entry.json'),
    DATA_DIR: join(data, 'host/data'),
    LOGS_DIR: join(data, 'host/logs'),
    DEPLOY_CONFIG_DIR: join(data, 'config'),
    HOST_WORKSPACES_FILE: join(data, 'config/host_workspaces.json'),
    SHELL: '/bin/sh',
    ELECTRON_ENABLE_LOGGING: '1',
  });
  delete env.ELECTRON_RUN_AS_NODE;
  runtime = await load({ env, root, profile }, 1);
  const initialPid = runtime.status.pid;
  console.info('Electron boot and bundled Rust PID verified.');

  await waitFor('startup migration dialog', () => runtime.view('/chrome', `Boolean(${migrationDialog})`), 25000);
  await waitFor('defer migration', () => runtime.view('/chrome', `(() => {
    const dialog = ${migrationDialog};
    const button = dialog?.querySelector('.ui-dialog-actions button.is-quiet');
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`));
  await waitFor('desktop sidebar expanded', () => runtime.view('/chrome', `(() => {
    const sidebar = document.querySelector('.conversation-sidebar');
    if (!sidebar) return false;
    if (sidebar.classList.contains('collapsed')) {
      sidebar.querySelector('.conversation-sidebar-toggle')?.click();
      return false;
    }
    return true;
  })()`));
  await waitFor('legacy conversation row', () => runtime.view('/chrome', `(() => {
    const row = document.querySelector('.conversation-migration-label')?.closest('.conversation-item');
    if (!row) return false;
    row.click();
    return true;
  })()`), 20000);
  const conversationPath = '/' + id.replace(/^conv_/, '');
  await waitFor('incompatible conversation in the content renderer', () => runtime.view(conversationPath,
    `/不兼容的对话记录|Incompatible conversation/.test(document.querySelector('.migration-required h2')?.innerText || '')`), 20000);
  console.info('Startup prompt, defer and legacy conversation warning verified.');

  await waitFor('migration entry inside the conversation', () => runtime.view(conversationPath, `(() => {
    const button = document.querySelector('.migration-required button');
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`));
  await waitFor('explicit migration action', () => runtime.view(conversationPath, `(() => {
    const dialog = ${migrationDialog};
    const button = dialog?.querySelector('.ui-dialog-actions button.is-primary');
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`));
  await waitFor('migrated conversation to reopen and restore messages', () => runtime.view(conversationPath,
    `!document.querySelector('.migration-required') && document.body.innerText.includes('Preserved Python response')`), 30000);
  await waitFor('updated desktop sidebar', () => runtime.view('/chrome',
    `!document.querySelector('.conversation-migration-label')`));
  await waitFor('completed migration dialog to close', () => runtime.view(conversationPath, `(() => {
    const dialog = ${migrationDialog};
    if (!dialog) return true;
    const button = dialog.querySelector('.ui-dialog-actions button.is-quiet');
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`));
  assert.equal(await readFile(source, 'utf8'), original);
  for (const [file,value] of legacyFiles) assert.equal(await readFile(file,'utf8'), JSON.stringify(value));
  const children = new DatabaseSync(join(dataDir,'sub_agents.sqlite3'), {readOnly:true});
  try {
    const stored = children.prepare('SELECT seq,body FROM child_message WHERE task_id=? ORDER BY seq').all(childId);
    assert.deepEqual(stored.map(row=>JSON.parse(row.body)),childHistory);
    const header=children.prepare('SELECT context FROM child_task WHERE id=?').get(childId);
    assert.deepEqual(JSON.parse(header.context).unknown, {round:7});
  } finally { children.close(); }
  const reviews = new DatabaseSync(join(dataDir,'review_history.sqlite3'), {readOnly:true});
  try {
    assert.deepEqual(reviews.prepare('SELECT kind FROM reviews ORDER BY kind').all().map(row=>row.kind),
      ['auto_approval','goal_review','workflow_review']);
    for (const row of reviews.prepare('SELECT body FROM review_events').all()) assert.deepEqual(JSON.parse(row.body).transcript.opaque,[1,null]);
  } finally { reviews.close(); }
  console.info('Explicit Agent, child and three reviewer migrations, unchanged sources and refreshed history verified.');

  await send(runtime, conversationPath, first);
  await waitFor('read_file execution and first assistant answer', async () => {
    if (mock.errors.length) throw new Error(mock.errors.join('\n'));
    return runtime.view(conversationPath, accepted);
  }, 30000);
  await send(runtime, conversationPath, next);
  await waitFor('follow-up assistant answer', async () => {
    if (mock.errors.length) throw new Error(mock.errors.join('\n'));
    return runtime.view(conversationPath, persisted);
  }, 30000);
  assert.deepEqual(mock.calls, ['initial', 'tool-result', 'next']);
  console.info('Real read_file, streamed reply and follow-up verified.');

  assert.equal(await runtime.stop(), 0);
  runtime = await load({ env, root, profile }, 2);
  assert.notEqual(runtime.status.pid, initialPid);
  await waitFor('reopened conversation after restart', () => runtime.view(conversationPath,
    `${persisted} && !document.querySelector('.migration-required')`), 20000);
  assert.equal(await runtime.stop(), 0);
  console.info(JSON.stringify({ build: app, firstPid: initialPid, restartedPid: runtime.status.pid,
    migration: true, tool: 'read_file', upstreamCalls: mock.calls, restoredAfterRestart: true,
    legacyMessages:saved.messages.length, legacyBytes:Buffer.byteLength(original),
    toolRoundTripMs:Math.round(mock.timing['tool-result'].requestStart - mock.timing.initial.replyAt),
    testDirectory: root }));
  runtime = undefined;
} catch (error) {
  console.error(`Isolated Electron evidence: ${root}`);
  if (runtime) console.error(JSON.stringify(await runtime.diagnostics().catch(() => []), null, 2));
  throw error;
} finally {
  await runtime?.stop().catch(error => console.error(error.message));
  await mock.close();
}

import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { closeServer, listenOnLoopback, waitFor } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const electron = "process.getBuiltinModule('node:module').createRequire(process.resourcesPath + '/app.asar/package.json')('electron')";

async function quitElectron(debugUrl) {
  const socket = new WebSocket(debugUrl);
  try {
    await new Promise((ready, reject) => {
      socket.addEventListener('open', ready, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: {
      expression: `setTimeout(() => ${electron}.app.quit(), 25); true`,
    } }));
    await delay(100);
  } finally {
    socket.close();
  }
}

export async function startAppRuntimeFixture(t, appPath, { model, startMock, prepare, workspaceIds }) {
  const app = resolve(appPath);
  const root = await mkdtemp(join(tmpdir(), 'astrion-app-runtime-'));
  const data = join(root, 'data');
  const dataDir = join(data, 'host/data');
  const configDir = join(data, 'config');
  const profile = join(root, 'profile');
  await Promise.all([dataDir, configDir, profile].map(path => mkdir(path, { recursive: true })));
  const workspaceCatalog = join(configDir, 'host_workspaces.json');
  const workspaces = await Promise.all(workspaceIds.map(async (id, index) => {
    const path = join(root, `workspace-${index + 1}`);
    await mkdir(path);
    return { workspace_id: id, label: id, path: await realpath(path) };
  }));
  const workspace = workspaces[0].path;
  let mock;
  let child;
  let exited;
  let debugUrl;
  let baseUrl;
  const output = { text: '' };
  t.after(async () => {
    try {
      if (child && child.exitCode === null && child.signalCode === null) {
        if (debugUrl) await quitElectron(debugUrl);
        const exit = await Promise.race([exited, delay(12_000, null, { ref: false })]);
        assert.equal(exit?.code, 0, `Electron did not exit normally\n${output.text}`);
      }
      if (baseUrl) {
        await waitFor('bundled backend to stop', async () => {
          try {
            await fetch(baseUrl + '/api/host-mode-enabled', { signal: AbortSignal.timeout(500) });
            return false;
          } catch {
            return true;
          }
        });
      }
    } finally {
      if (child && child.exitCode === null && child.signalCode === null) {
        child.kill('SIGTERM');
        await Promise.race([exited, delay(3_000, null, { ref: false })]);
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
      }
      await mock?.close();
      await rm(root, { recursive: true, force: true });
    }
  });
  mock = await startMock();
  await writeFile(workspaceCatalog, JSON.stringify({ default_workspace_id: workspaceIds[0], workspaces }));
  await writeFile(join(configDir, 'custom_models.json'), JSON.stringify({
    models: [{ model_name: model, model_id: model, url: new URL('/v1', mock.endpoint).href,
      apikey: 'synthetic-local-only', reasoning_capability: 'fast', context_window: 32768, max_output_tokens: 2048 }],
  }));
  await prepare({ workspace, dataDir, mock });

  const reservation = createServer();
  const debugPort = await listenOnLoopback(reservation);
  await closeServer(reservation);
  const env = {
    PATH: process.env.PATH,
    HOME: root,
    TMPDIR: root,
    SHELL: '/bin/sh',
    ASTRION_DESKTOP_DATA_ROOT: data,
    ASTRION_ELECTRON_USER_DATA: profile,
    ASTRION_DESKTOP_INITIAL_WORKSPACE: workspace,
    ASTRION_QUICK_SETTINGS_FILE: join(profile, 'quick-entry.json'),
    DATA_DIR: dataDir,
    LOGS_DIR: join(data, 'host/logs'),
    DEPLOY_CONFIG_DIR: configDir,
    HOST_WORKSPACES_FILE: workspaceCatalog,
    ELECTRON_ENABLE_LOGGING: '1',
  };
  child = spawn(join(app, 'Contents/MacOS/Astrion'), [`--inspect=${debugPort}`], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  exited = new Promise(done => child.once('exit', (code, signal) => done({ code, signal })));
  let spawnError;
  child.once('error', error => { spawnError = error; });
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', chunk => { output.text = (output.text + chunk).slice(-8_000); });
  }
  debugUrl = await waitFor('isolated Electron inspector', () => {
    if (spawnError) throw spawnError;
    return output.text.match(/Debugger listening on (ws:\/\/127\.0\.0\.1:\d+\/[0-9a-f-]+)/)?.[1];
  }, 20_000);
  assert.equal(new URL(debugUrl).port, String(debugPort));
  const runtime = await waitFor('isolated bundled Rust listener', async () => {
    const port = await readFile(join(profile, 'backend-port.json'), 'utf8')
      .then(contents => JSON.parse(contents).port).catch(() => null);
    if (!port) return false;
    const url = `http://127.0.0.1:${port}`;
    const status = await fetch(url + '/api/host-mode-enabled', { signal: AbortSignal.timeout(500) })
      .then(response => response.ok && response.json()).catch(() => false);
    if (!status) return false;
    assert.equal(status.backend, 'rust');
    assert.equal(Number(execFileSync('ps', ['-p', String(status.pid), '-o', 'ppid='], { encoding: 'utf8' }).trim()), child.pid);
    const command = execFileSync('ps', ['-p', String(status.pid), '-o', 'command='], { encoding: 'utf8' }).trim();
    const backend = join(await realpath(app), 'Contents/Resources/runtime/rust/astrion-headless');
    assert(command.startsWith(backend + ' '), command);
    return { url, pid: status.pid };
  }, 30_000);
  baseUrl = runtime.url;
  const token = (await readFile(join(dataDir, 'host_api_token'), 'utf8')).trim();
  const authorized = async (path, method = 'GET', body) => {
    const response = await fetch(baseUrl + path, { method, signal: AbortSignal.timeout(5_000),
      headers: { authorization: `Bearer ${token}`, 'x-astrion-workspace-id': workspaceIds[0], 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const payload = await response.json();
    assert(response.ok, JSON.stringify(payload));
    return payload;
  };
  return {
    workspace, mock, backendPid: runtime.pid,
    gateway: {
      baseUrl, readToken: async () => token,
      async createTask(body) { return (await authorized('/api/tasks', 'POST', { model_key: model, ...body })).data; },
      async pollTask(id, from) { return (await authorized(`/api/tasks/${id}?from=${from}`)).data; },
    },
  };
}

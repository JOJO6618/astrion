import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { waitFor } from './gateway_harness.mjs';

const fixtures = fileURLToPath(new URL('.', import.meta.url));
const binary = resolve(process.env.ASTRION_HEADLESS_BIN || join(
  fixtures, '../../backend-rust/target/debug', process.platform === 'win32' ? 'astrion-headless.exe' : 'astrion-headless',
));

async function start(t, web) {
  const root = await mkdtemp(join(fixtures, 'auth_gateway_run_'));
  const workspace = join(root, 'workspace');
  const staticRoot = join(root, 'static');
  const data = join(root, 'data');
  let child;
  let exited;
  t.after(async () => {
    if (child?.pid && child.exitCode === null && child.signalCode === null) {
      child.kill();
      if (!(await Promise.race([exited, delay(1_000, false, { ref: false })]))) {
        child.kill('SIGKILL');
        assert.equal(await Promise.race([exited, delay(1_000, false)]), true);
      }
    }
    await rm(root, { recursive: true, force: true });
  });
  await mkdir(join(staticRoot, 'dist/.vite'), { recursive: true });
  await mkdir(workspace);
  await mkdir(data);
  const workspaceCatalog = join(data, 'host_workspaces.json');
  await writeFile(workspaceCatalog, JSON.stringify({
    default_workspace_id: 'default', workspaces: [{ workspace_id: 'default', label: 'Auth fixture', path: workspace }],
  }));
  await writeFile(join(staticRoot, 'login.html'), '<html><title>Synthetic sign in</title></html>');
  await writeFile(join(staticRoot, 'index.html'), '<html><title>Synthetic workspace</title></html>');
  await writeFile(join(staticRoot, 'dist/.vite/manifest.json'), '{}');
  child = spawn(binary, [
    '--workspace', workspace, '--data-dir', data, '--port', '0',
    ...(web ? ['--web', '--static-root', staticRoot] : []),
    '--endpoint', 'http://127.0.0.1:1/v1/chat/completions', '--model', 'synthetic-auth-model',
  ], {
    cwd: root,
    env: {
      PATH: process.env.PATH,
      ...(process.platform === 'win32' && { SystemRoot: process.env.SystemRoot }),
      ASTRION_IGNORE_DOTENV: '1', HOST_WORKSPACES_FILE: workspaceCatalog,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  let startError;
  child.on('error', (error) => { startError = error; });
  exited = new Promise((done) => child.once('exit', () => done(true)));
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', (message) => { output = (output + message).slice(-2_000); });
  }
  const base = await waitFor('Rust Web listener', () => {
    if (startError) throw startError;
    if (child.exitCode !== null) throw new Error(`Rust exited during startup\n${output}`);
    const listening = output.match(/listening on (127\.0\.0\.1:\d+)/);
    return listening && `http://${listening[1]}`;
  });
  const token = (await readFile(join(data, 'host_api_token'), 'utf8')).trim();
  assert.ok(token);
  const request = (path, options = {}) => fetch(`${base}${path}`, { redirect: 'manual', signal: AbortSignal.timeout(3_000), ...options });
  const health = await (await request('/api/host-mode-enabled')).json();
  assert.equal(health.backend, 'rust');
  assert.equal(health.pid, child.pid);
  return { request, base, authorization: `Bearer ${token}` };
}

function cookie(response) {
  const issued = response.headers.get('set-cookie');
  assert.ok(issued, 'expected browser credential cookie');
  return issued.split(';', 1)[0];
}

async function challenge(request, session) {
  const response = await request('/api/csrf-token', { headers: session ? { cookie: session } : {} });
  assert.equal(response.status, 200);
  const csrf = (await response.json()).token;
  assert.ok(csrf);
  return { csrf, session: session || cookie(response) };
}

test('browser sessions and local Bearer credentials stay scoped to declared channels', async (t) => {
  const { request, base, authorization } = await start(t, true);
  const desktopStatus = await request('/api/session-status', { headers: { authorization } });
  assert.equal(desktopStatus.headers.has('set-cookie'), false);
  assert.equal((await desktopStatus.json()).session.logged_in, false);
  assert.equal((await request('/api/health', { headers: { authorization } })).status, 401);
  assert.equal((await request('/api/status', { headers: { authorization } })).status, 401);
  assert.equal((await request('/api/host/workspaces')).status, 401);

  const issued = await challenge(request);
  const login = await request('/host-login', {
    method: 'POST', headers: { cookie: issued.session, origin: base, 'x-csrf-token': issued.csrf },
  });
  assert.equal(login.status, 200);
  const session = cookie(login);
  const csrf = (await challenge(request, session)).csrf;
  assert.equal((await request('/api/health', { headers: { cookie: session } })).status, 200);
  assert.equal((await request('/api/host/workspaces', { headers: { cookie: session } })).status, 200);
  assert.equal((await request('/api/host/workspaces', { headers: { cookie: session, authorization: 'Bearer invalid' } })).status, 401);
  assert.equal((await request('/api/host/workspaces', { headers: { cookie: session, origin: 'http://different.invalid' } })).status, 403);

  const post = (headers) => request('/api/runtime/sessions', {
    method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: '{}',
  });
  assert.equal((await post({ cookie: session })).status, 403);
  assert.equal((await post({ cookie: session, origin: base, 'x-csrf-token': csrf })).status, 200);
  const local = await post({ authorization });
  assert.equal(local.status, 200);
  assert.equal(local.headers.has('set-cookie'), false);
  const after = await request('/api/session-status', { headers: { cookie: session } });
  assert.equal((await after.json()).session.logged_in, true);
});

test('headless Gateway requires its Bearer credential independently of browser paths', async (t) => {
  const { request, authorization } = await start(t, false);
  assert.equal((await request('/api/host/workspaces', { headers: { cookie: 'agents_session_0=r1.synthetic' } })).status, 401);
  const accepted = await request('/api/host/workspaces', { headers: { authorization } });
  assert.equal(accepted.status, 200);
  assert.equal(accepted.headers.has('set-cookie'), false);
  assert.equal((await request('/api/health')).status, 401);
  assert.equal((await request('/api/health', { headers: { authorization } })).status, 401);
  assert.equal((await request('/api/csrf-token')).status, 404);
});

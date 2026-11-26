import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { closeServer, listenOnLoopback, waitFor } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const first = 'conv_preview_original';
const second = 'conv_preview_other';
const tokenFromPython = 'python-token-compatible-preview';
const filePath = 'site/index.html';

function cookie(response) {
  const value = response.headers.get('set-cookie');
  assert.ok(value, 'authentication must issue a session cookie');
  return value.split(';', 1)[0];
}

async function login(request, origin) {
  const challenge = await request('/api/csrf-token');
  const { token } = await challenge.clone().json();
  const response = await request('/host-login', {
    method: 'POST',
    headers: { cookie: cookie(challenge), origin, 'content-type': 'application/json', 'x-csrf-token': token },
    body: '{}',
  });
  assert.equal(response.status, 200);
  // WebAuth rotates both the session cookie and its CSRF token after sign-in.
  const session = cookie(response);
  await response.arrayBuffer();
  const csrf = await request('/api/csrf-token', { headers: { cookie: session } });
  return { session, csrf: (await csrf.json()).token };
}

async function prepare({ workspace, dataDir, mock }) {
  await mkdir(join(workspace, 'site'));
  await writeFile(join(workspace, filePath), '<html><head></head><img src="asset.png"></html>');
  await writeFile(join(workspace, 'site', 'asset.png'), Buffer.from([1, 2, 3, 4]));
  const stored = join(dataDir, 'conversations', 'default');
  await mkdir(stored, { recursive: true });
  for (const [id, previewToken, targets] of [
    [first, tokenFromPython, [
      { type: 'file', path: filePath, label: 'index.html', source: 'file_edit' },
      { type: 'server', url: `http://localhost:${mock.port}/page`, source: 'model_output' },
    ]],
    [second, undefined, []],
  ]) {
    await writeFile(join(stored, `${id}.json`), JSON.stringify({
      id, title: 'Saved Python Host conversation', messages: [], token_statistics: {},
      created_at: '2026-11-24T08:00:00', updated_at: '2026-11-24T08:00:00',
      metadata: { project_path: workspace, edited_files: [], preview_token: previewToken, preview_targets: targets },
    }));
  }
}

async function mockUpstream() {
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    response.writeHead(202, { 'Content-Type': 'application/json', 'Set-Cookie': 'fixture=must-not-pass' });
    response.end(JSON.stringify({
      method: request.method, url: request.url, body: Buffer.concat(chunks).toString(),
      hasCredentials: Boolean(request.headers.cookie || request.headers.authorization || request.headers['x-csrf-token']),
    }));
  });
  const port = await listenOnLoopback(server);
  return { port, close: () => closeServer(server) };
}

async function startFixture(t) {
  const root = await realpath(await mkdtemp(fileURLToPath(new URL('./.web-fixture-', import.meta.url))));
  const workspace = join(root, 'workspace');
  const dataDir = join(root, 'data');
  const isolatedHome = join(root, 'home');
  const workspaceFile = join(dataDir, 'host_workspaces.json');
  let child, exited, mock;
  t.after(async () => {
    if (child && child.exitCode === null && child.signalCode === null) {
      child.stdin.end();
      const [code] = await exited;
      assert.equal(code, 0, 'fixture backend shutdown by stdin');
    }
    await mock?.close();
    await rm(root, { recursive: true, force: true });
  });
  await Promise.all([workspace, dataDir, isolatedHome].map((path) => mkdir(path)));
  await writeFile(workspaceFile, JSON.stringify({ default_workspace_id: 'default', workspaces: [
    { workspace_id: 'default', label: 'Preview fixture', path: workspace },
  ] }));
  mock = await mockUpstream();
  await prepare({ workspace, dataDir, mock });
  const defaultBinary = fileURLToPath(new URL('../../backend-rust/target/debug/astrion-headless', import.meta.url));
  const binary = resolve(process.env.ASTRION_HEADLESS_BIN || defaultBinary + (process.platform === 'win32' ? '.exe' : ''));
  child = spawn(binary, ['--port', '0', '--workspace', workspace, '--data-dir', dataDir, '--web', '--static-root', workspace], {
    cwd: workspace,
    env: {
      PATH: process.env.PATH, HOME: isolatedHome, USERPROFILE: isolatedHome, XDG_CONFIG_HOME: isolatedHome,
      ...(process.platform === 'win32' && { SystemRoot: process.env.SystemRoot }),
      ASTRION_IGNORE_DOTENV: '1', ASTRION_DESKTOP_STOP_STDIN: '1', HOST_WORKSPACES_FILE: workspaceFile,
    },
    stdio: ['pipe', 'ignore', 'pipe'],
  });
  exited = once(child, 'exit');
  let output = '';
  child.stderr.setEncoding('utf8').on('data', (chunk) => { output += chunk; });
  const origin = await waitFor('preview fixture to bind its own port', () => {
    assert.equal(child.exitCode, null, output);
    return output.match(/Rust server listening on (127\.0\.0\.1:\d+)/)?.[1];
  });
  return { origin: `http://${origin}`, dataDir, mock };
}

const requestAt = (base) => (path, options = {}) => fetch(base + path, {
  signal: AbortSignal.timeout(5_000), redirect: 'manual', ...options,
});

async function asJson(response, status = 200) {
  assert.equal(response.status, status, response.url);
  return response.json();
}

test('the authenticated Web binary publishes one isolated preview origin from targets, bootstrap and messages', { timeout: 30_000 }, async (t) => {
  const { origin, dataDir, mock } = await startFixture(t);
  const request = requestAt(origin);
  const targets = `/api/preview/targets?workspace_id=default&conversation_id=${first}`;
  const bootstrap = `/api/conversations/${first}/bootstrap?workspace_id=default`;
  const messages = `/api/conversations/${first}/messages?workspace_id=default`;
  for (const path of [targets, bootstrap, messages, `/api/preview/file/${filePath}`]) {
    await asJson(await request(path), 401);
  }
  const { session, csrf } = await login(request, origin);
  const headers = { cookie: session };
  const targetData = await asJson(await request(targets, { headers }));
  const bootstrapData = (await asJson(await request(bootstrap, { headers }))).data;
  const messagesData = (await asJson(await request(messages, { headers }))).data;
  assert.match(targetData.preview_base, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.notEqual(targetData.preview_base, origin);
  for (const data of [targetData, bootstrapData, messagesData]) {
    assert.equal(data.preview_base, targetData.preview_base);
    assert.equal(data.preview_token, tokenFromPython);
    assert.deepEqual(data.preview_targets.map((target) => target.type), ['file', 'server']);
  }
  const isolated = requestAt(targetData.preview_base);
  const tokenPath = `/${tokenFromPython}`;
  const file = await isolated(`${tokenPath}/file/${filePath}`);
  assert.equal(file.status, 200);
  assert.equal(file.headers.get('referrer-policy'), 'no-referrer');
  assert.match(await file.text(), new RegExp(`<head><base href="/${tokenFromPython}/file/site/">`));
  const asset = await isolated(`${tokenPath}/file/site/asset.png`);
  assert.equal(asset.headers.get('content-type'), 'image/png');
  assert.deepEqual(new Uint8Array(await asset.arrayBuffer()), new Uint8Array([1, 2, 3, 4]));
  assert.equal((await isolated(`/incorrect-preview-token/file/${filePath}`)).status, 404);
  assert.equal((await isolated(`${tokenPath}/file/${filePath}`, { headers: { host: 'forged.invalid' } })).status, 403);
  assert.equal((await isolated('/api/preview/targets')).status, 404);

  const proxy = `${tokenPath}/proxy/${mock.port}/page?x=%26`;
  const proxied = await isolated(proxy, {
    method: 'PATCH', body: 'fixture patch',
    headers: { cookie: session, 'x-csrf-token': csrf, authorization: 'Bearer fixture' },
  });
  assert.equal(proxied.headers.get('set-cookie'), null);
  assert.deepEqual(await asJson(proxied, 202), {
    method: 'PATCH', url: '/page?x=%26', body: 'fixture patch', hasCredentials: false,
  });
  const secondTargets = await asJson(await request(`/api/preview/targets?conversation_id=${second}`, { headers }));
  assert.equal(secondTargets.preview_base, targetData.preview_base);
  assert.notEqual(secondTargets.preview_token, tokenFromPython);
  const secondDoc = JSON.parse(await readFile(join(dataDir, 'conversations', 'default', `${second}.json`), 'utf8'));
  assert.equal(secondDoc.metadata.preview_token, secondTargets.preview_token);
  assert.equal((await isolated(`/${secondTargets.preview_token}/proxy/${mock.port}/page?conversation_id=${first}`)).status, 403);

  const fallback = await request(`/api/preview/file/${filePath}?workspace_id=default&conversation_id=${first}`, { headers });
  assert.equal(fallback.status, 200);
  assert.equal(fallback.headers.get('content-security-policy'), 'sandbox allow-scripts allow-forms');
  await fallback.arrayBuffer();
  const removal = {
    method: 'POST', body: JSON.stringify({ conversation_id: first, key: `server:http://localhost:${mock.port}/page` }),
    headers: { cookie: session, origin, 'content-type': 'application/json' },
  };
  await asJson(await request('/api/preview/targets/remove', removal), 403);
  removal.headers['x-csrf-token'] = csrf;
  const remaining = await asJson(await request('/api/preview/targets/remove', removal));
  assert.deepEqual(remaining.preview_targets.map((target) => target.type), ['file']);
  assert.equal((await isolated(proxy)).status, 403);
});

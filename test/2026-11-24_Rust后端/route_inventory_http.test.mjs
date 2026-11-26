// Exercised method/path contracts listed by route_inventory.py, against the actual Rust listener.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, realpath, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { startGatewayFixture, WORKSPACE_ID } from './gateway_harness.mjs';

const MODEL = 'inventory-offline-model';
const sharedPaths = ['/api/personalization', '/api/schedules', '/api/schedules/options', '/api/workflows'];
const webPaths = ['/api/conversations', '/api/workspace-view', '/api/permission-mode', '/api/workflows/synthetic-absent', '/api/memory'];
const workspaceReads = ['/api/status', '/api/skills', '/api/git-worktree'];
const worktreeWrites = [
  ['/api/git-worktree', { conversation_id: 'conv_inventory', action: 'exit' }],
  ['/api/git-worktree/inspect', { path: '/inventory-fixture' }],
];

async function checkWorkspaceSessionRejections(request, headers) {
  for (const path of workspaceReads) {
    const response = await request(path, 401, { headers });
    assert.equal(typeof JSON.parse(response.text).error, 'string');
  }
  // Python's CSRF hook rejects these browser writes before their session check.
  for (const [path, body] of worktreeWrites) {
    const response = await request(path, 403, { method: 'POST', headers, body });
    assert.equal(JSON.parse(response.text).error, 'CSRF validation failed');
  }
}

async function prepareWorktree({ workspace, dataDir }) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key)));
  env.GIT_CONFIG_NOSYSTEM = '1';
  env.GIT_CONFIG_GLOBAL = process.platform === 'win32' ? 'NUL' : '/dev/null';
  env.XDG_CONFIG_HOME = dataDir;
  const git = (...args) => execFileSync('git', args, { cwd: workspace, env, stdio: ['ignore', 'pipe', 'pipe'] });
  git('init', '-b', 'main');
  git('-c', 'user.name=Inventory', '-c', 'user.email=inventory@fixture.invalid', 'commit', '--allow-empty', '-m', 'Initial fixture');
  git('worktree', 'add', '-b', 'inventory-linked', 'inventory-linked');
  await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({ git_worktree_enabled: true }));
}

async function start(t, web) {
  const fixture = await startGatewayFixture(t, {
    model: MODEL, web,
    startMock: () => ({ endpoint: 'http://127.0.0.1:1/v1/chat/completions', close() {} }),
    prepare: web ? prepareWorktree : undefined,
  });
  const { gateway, dataDir } = fixture;
  const base = gateway.baseUrl;
  const bearer = { authorization: `Bearer ${await gateway.readToken()}` };
  const config = join(dataDir, 'config');
  await mkdir(config, { recursive: true });
  await writeFile(join(config, 'custom_models.json'), JSON.stringify({ models: [{
    model_name: MODEL, model_id: MODEL, url: 'http://127.0.0.1:1/v1', apikey: 'synthetic-inventory-key',
  }] }));
  async function request(path, expected, { method = 'GET', headers = {}, body } = {}) {
    const requestHeaders = { ...headers };
    if (body !== undefined) requestHeaders['content-type'] = 'application/json';
    const response = await fetch(base + path, {
      method, headers: requestHeaders, body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'manual', signal: AbortSignal.timeout(3_000),
    });
    const text = await response.text();
    assert.equal(response.status, expected, `${method} ${path}: ${text}`);
    return { headers: response.headers, text };
  }
  return { ...fixture, bearer, base, request };
}

async function checkShared(request, headers) {
  const preferences = JSON.parse((await request('/api/personalization', 200, { headers })).text);
  assert.equal(preferences.success, true);
  assert.equal(typeof preferences.data.theme, 'string');
  const schedules = JSON.parse((await request('/api/schedules', 200, { headers })).text);
  assert.equal(schedules.success, true);
  assert.deepEqual(schedules.data.plans, []);
  const options = JSON.parse((await request('/api/schedules/options', 200, { headers })).text);
  assert.equal(options.success, true);
  assert.ok(options.data.workspaces.some((workspace) => workspace.id === WORKSPACE_ID));
  assert.ok(options.data.models.some((model) => model.value === MODEL));
  const workflows = JSON.parse((await request('/api/workflows', 200, { headers })).text);
  assert.ok(Array.isArray(workflows.workflows));
}

function cookie(response) {
  const value = response.headers.get('set-cookie');
  assert.ok(value, 'A browser authentication step must issue the expected session cookie');
  return value.split(';', 1)[0];
}

async function browserSession(request, origin) {
  const challenge = await request('/api/csrf-token', 200);
  const login = await request('/host-login', 200, {
    method: 'POST', body: {}, headers: { cookie: cookie(challenge), origin, 'x-csrf-token': JSON.parse(challenge.text).token },
  });
  const session = { cookie: cookie(login), origin };
  const after = await request('/api/csrf-token', 200, { headers: session });
  return { session, write: { ...session, 'x-csrf-token': JSON.parse(after.text).token } };
}

test('headless accepts Bearer for shared routes and requires a browser session for workspace routes', { timeout: 20_000 }, async (t) => {
  const { request, bearer, base: origin } = await start(t, false);
  for (const path of sharedPaths) await request(path, 401);
  for (const path of webPaths) await request(path, 404, { headers: bearer });
  await checkShared(request, bearer);
  await checkWorkspaceSessionRejections(request, { origin });
  await checkWorkspaceSessionRejections(request, { origin, ...bearer });
  const options = await request('/api/schedules', 200, { method: 'OPTIONS', headers: bearer });
  assert.deepEqual(options.headers.get('allow').split(', '), ['GET', 'HEAD', 'POST', 'OPTIONS']);
  const head = await request('/api/schedules', 200, { method: 'HEAD', headers: bearer });
  assert.equal(head.text, '');
  const invalid = await request('/api/schedules/validate', 400, { method: 'POST', headers: bearer, body: {} });
  assert.equal(JSON.parse(invalid.text).success, false);
});

test('Web serves conversation and workspace routes with cookie, CSRF and workspace scope', { timeout: 20_000 }, async (t) => {
  const { request, bearer, base, workspace } = await start(t, true);
  for (const path of [...sharedPaths, ...webPaths]) await request(path, 401);
  for (const path of webPaths) await request(path, 401, { headers: bearer });
  await checkWorkspaceSessionRejections(request, { origin: base });
  await checkWorkspaceSessionRejections(request, { origin: base, ...bearer });
  await checkShared(request, bearer);
  const browser = await browserSession(request, base);
  await checkShared(request, browser.session);
  const allowed = await request('/api/status', 200, { method: 'OPTIONS', headers: browser.session });
  assert.deepEqual(new Set(allowed.headers.get('allow').split(',').map(v => v.trim())), new Set(['GET', 'HEAD', 'OPTIONS']));
  assert.equal(allowed.text, '');
  await request('/api/synthetic-absent-options', 404, { method: 'OPTIONS', headers: browser.session });
  const skill = join(workspace, '.astrion/skills/local');
  await mkdir(skill, { recursive: true });
  await writeFile(join(skill, 'SKILL.md'), '---\nname: Local fixture\ndescription: example\n---\nBody\n');
  const menu = JSON.parse((await request('/api/skills', 200, { headers: browser.session })).text);
  assert.ok(menu.skills.some(entry => entry.name === 'Local fixture'));
  const status = JSON.parse((await request('/api/status', 200, { headers: browser.session })).text);
  assert.equal(status.workspace_id, WORKSPACE_ID);
  assert.equal(status.project_path, workspace);
  const memory = JSON.parse((await request('/api/memory', 200, { headers: browser.session })).text);
  assert.deepEqual(memory.entries, []);
  await request('/api/skills?workspace_id=other', 404, { headers: browser.session });
  await request('/api/memory', 401);
  await request('/api/workflows', 401, { headers: { ...browser.session, authorization: 'Bearer invalid' } });
  await request('/api/workflows/synthetic-absent', 404, { headers: browser.session });
  const view = JSON.parse((await request('/api/workspace-view', 200, { headers: browser.session })).text);
  assert.deepEqual(view.data.workspace, { workspace_id: WORKSPACE_ID, project_path: workspace });
  await request('/api/conversations?workspace_id=other', 404, { headers: browser.session });
  const initial = JSON.parse((await request('/api/conversations', 200, { headers: browser.session })).text);
  assert.deepEqual(initial.data.conversations, []);
  await request('/api/conversations', 403, { method: 'POST', headers: browser.session, body: {} });
  await request('/api/conversations', 400, { method: 'POST', headers: browser.write, body: {} });
  const defaults = JSON.parse((await request('/api/new-conversation/settings', 200, {
    method: 'POST', headers: browser.write, body: {},
  })).text);
  assert.equal(defaults.data.workspace.workspace_id, WORKSPACE_ID);
  const created = JSON.parse((await request('/api/conversations', 201, {
    method: 'POST', headers: browser.write, body: { settings: { ...defaults.data.settings, model_key: MODEL } },
  })).text);
  assert.equal(created.success, true);
  assert.match(created.conversation_id, /^conv_/);
  const final = JSON.parse((await request('/api/conversations', 200, { headers: browser.session })).text);
  assert.deepEqual(final.data.conversations.map((row) => row.id), [created.conversation_id]);

  const directory = await realpath(join(workspace, 'inventory-linked'));
  const inspect = JSON.parse((await request('/api/git-worktree/inspect', 200, {
    method: 'POST', headers: browser.write, body: { path: directory },
  })).text);
  assert.equal(inspect.success, true);
  assert.equal(inspect.data.valid, true);
  assert.equal(inspect.data.branch, 'inventory-linked');
  const changeBody = { conversation_id: created.conversation_id, action: 'enter', target: { action: 'enter', directory } };
  await request('/api/git-worktree', 403, { method: 'POST', headers: browser.session, body: changeBody });
  const changed = JSON.parse((await request('/api/git-worktree', 200, {
    method: 'POST', headers: browser.write, body: changeBody,
  })).text);
  assert.equal(changed.success, true);
  assert.equal(changed.state.directory, directory);
  const tree = JSON.parse((await request(`/api/git-worktree?conversation_id=${created.conversation_id}`, 200, {
    headers: browser.session,
  })).text);
  assert.equal(tree.success, true);
  assert.equal(tree.data.active, true);
  assert.equal(tree.data.directory, directory);
});

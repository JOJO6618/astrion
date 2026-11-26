import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { registerHooks } from 'node:module';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { stageRustBackend, stageRustResources } from '../../desktop/scripts/prepare_rust_backend.mjs';
import { isRustBackendReady, resolveDesktopBackend, rustDesktopLaunch } from '../../desktop-electron/src/rust-backend.js';
import { GatewayClient } from '../../cli/src/gateway.ts';
import { pythonFixture } from './cli_in_place_fixture.mjs';
import { closeServer, listenOnLoopback, waitFor } from './gateway_harness.mjs';

const dir = fileURLToPath(new URL('.', import.meta.url));
const binary = resolve(process.env.ASTRION_HEADLESS_BIN || join(dir, '../../backend-rust/target/debug/astrion-headless'));
const staticRoot = resolve(process.env.ASTRION_RUST_STATIC_ROOT || join(dir, '../../static'));

async function fixture(t) {
  const root = await realpath(await mkdtemp(join(dir, 'desktop_startup-')));
  t.after(() => rm(root, { recursive: true, force: true }));
  const profile = join(root, 'electron-profile');
  const data = join(root, 'isolated-data');
  const workspace = join(root, '测试项目');
  const resourcesPath = join(root, 'resources');
  await Promise.all([mkdir(profile), mkdir(data), mkdir(workspace), mkdir(join(resourcesPath, 'runtime'), { recursive: true })]);
  const env = {
    ASTRION_DESKTOP_BACKEND: 'rust', ASTRION_RUST_EXPERIMENT: '1', ASTRION_DESKTOP_DATA_ROOT: data,
    ASTRION_DESKTOP_INITIAL_WORKSPACE: workspace, ASTRION_ELECTRON_USER_DATA: profile
  };
  return { root, resourcesPath, profile, data, workspace, env };
}

function installShellMocks(shell) {
  globalThis.astrionTestShell = shell;
  const skipped = (...names) => names.map(name => `export const ${name} = () => { throw new Error('unexpected ${name}'); };`).join('\n');
  const normal = (name, expression = 'undefined') => `export const ${name} = () => { const shell = globalThis.astrionTestShell; if (!shell.normalRoot) throw new Error('unexpected ${name}'); return ${expression}; };`;
  const sources = {
    electron: 'export const { app, session } = globalThis.astrionTestShell; export const dialog = {};',
    './bridge.js': normal('startBridge'),
    './rundata.js': [normal('getMigrationProgress', '({phase: "idle"})'), normal('hasPendingMigration', 'false'), normal('resolveRunDataRoot', 'shell.normalRoot'), skipped('markMigrationBackendReady', 'runPendingMigration')].join('\n'),
    './shell_env.js': normal('loadLoginShellPath', '({ status: "unsupported-platform" })'),
    './migration-window.js': skipped('createMigrationWindow'),
    './quick/controller.js': normal('startQuickEntry'),
    './schedule-notifications.js': normal('startScheduleNotifications'),
    './conversation-notifications.js': normal('startConversationNotifications'),
    './update-policy-window.js': [normal('enforceStartupUpdatePolicy', 'true'), normal('finishUpdatePolicyTransition')].join('\n'),
    './update-policy.js': 'export const desktopUpdateIdentity = () => globalThis.astrionTestShell.identity;',
    './browser/index.js': 'export const setBrowserDataRoot = root => globalThis.astrionTestShell.browserRoot = root;',
    './window.js': 'export const { setMainWindowBackend, focusMainWindow } = globalThis.astrionTestShell; export const isManagedView = () => false; export const conversationNotificationWindows = () => [];'
  };
  return registerHooks({ resolve(specifier, context, next) {
    const source = sources[specifier];
    if (source && context.parentURL?.includes('/desktop-electron/src/')) {
      return { shortCircuit: true, url: `data:text/javascript,${encodeURIComponent(source)}` };
    }
    return next(specifier, context);
  } });
}

test('Rust-only staged sidecar primes a session before Electron binds the main workspace', async t => {
  const f = await fixture(t);
  const runtime = join(f.resourcesPath, 'runtime/rust');
  stageRustBackend({ binary, staticRoot, destination: runtime });
  assert.deepEqual((await readdir(runtime)).sort(), ['agentskills', 'astrion-headless', 'config', 'prompts', 'static', 'workflows']);
  for (const artifact of ['config/providers_catalog.json', 'agentskills/docx/SKILL.md', 'workflows/feature-development/WORKFLOW.md', 'static/dist/.vite/entry-styles.json']) {
    assert.ok((await readFile(join(runtime, artifact))).length > 0, artifact);
  }
  await assert.rejects(readFile(join(runtime, 'config/custom_models.json')), { code: 'ENOENT' });
  await assert.rejects(readFile(join(runtime, 'config/custom_models.json.example')), { code: 'ENOENT' });
  const experiment = resolveDesktopBackend({ env: f.env, platform: 'darwin', resourcesPath: f.resourcesPath });
  assert.equal(experiment.binary, join(runtime, 'astrion-headless'));
  assert.equal(experiment.dataDir, join(f.data, 'host/data'));
  await mkdir(join(f.data, 'config'));
  await writeFile(join(f.data, 'config/host_workspaces.json'), JSON.stringify({
    workspaces: [{ workspace_id: 'isolated-workspace', label: 'Offline fixture', path: f.workspace }]
  }));
  const normalDefault = resolveDesktopBackend({ env: {}, platform: 'darwin', resourcesPath: f.resourcesPath });
  assert.equal(normalDefault.binary, join(runtime, 'astrion-headless'));
  assert.equal(normalDefault.experimental, false);
  assert.throws(() => resolveDesktopBackend({ env: { ASTRION_DESKTOP_BACKEND: 'python' }, platform: 'darwin', resourcesPath: f.resourcesPath }), /ASTRION_DESKTOP_BACKEND=rust/);

  const identity = { build_id: 'fixture-build', platform: 'darwin', arch: 'arm64' };
  const fakeHome = join(f.profile, 'unused-home');
  const launch = rustDesktopLaunch(experiment, { port: 8765, version: 'test', identity, env: {
    ASTRION_DESKTOP_BUILD_ID: 'old', ASTRION_DESKTOP_PLATFORM: 'old',
    ASTRION_DESKTOP_ARCH: 'old', ASTRION_DESKTOP_POLICY_PROTOCOL: '0',
    ASTRION_DATA_ROOT: fakeHome, ASTRION_DESKTOP_VERSION: 'injected', LOGS_DIR: fakeHome,
    DEPLOY_CONFIG_DIR: fakeHome, HOST_WORKSPACES_FILE: fakeHome, ASTRION_DESKTOP_BRIDGE_PORT: '1'
  } });
  assert.equal(launch.env.HOME, f.data);
  assert.equal(launch.env.ASTRION_RESOURCE_ROOT, runtime);
  assert.equal(launch.env.ASTRION_DESKTOP_VERSION, 'test');
  assert.equal(launch.env.ASTRION_DESKTOP_BUILD_ID, identity.build_id);
  assert.equal(launch.env.ASTRION_DESKTOP_PLATFORM, identity.platform);
  assert.equal(launch.env.ASTRION_DESKTOP_ARCH, identity.arch);
  assert.equal(launch.env.ASTRION_DESKTOP_POLICY_PROTOCOL, '1');
  assert.equal(launch.env.DEPLOY_CONFIG_DIR, join(f.data, 'config'));
  assert.equal(launch.env.LOGS_DIR, join(f.data, 'host/logs'));
  assert.equal(launch.env.ASTRION_DESKTOP_BRIDGE_PORT, undefined);

  const events = [];
  const cookies = new Map();
  const ses = { async fetch(input, init) {
    events.push(new URL(input).pathname);
    const headers = { ...init.headers, Cookie: [...cookies].map(([name, value]) => `${name}=${value}`).join('; ') };
    const response = await fetch(input, { ...init, headers });
    for (const header of response.headers.getSetCookie()) {
      const [name, value] = header.split(';')[0].split('=');
      if (value) cookies.set(name, value);
      else cookies.delete(name);
    }
    return response;
  } };
  const shell = {
    identity,
    app: { getPath: () => f.profile, getVersion: () => 'test', setName() {} }, session: { defaultSession: ses },
    setMainWindowBackend(port) { shell.port = port; events.push('bind'); },
    focusMainWindow() { events.push('focus'); }
  };
  const hooks = installShellMocks(shell);
  let lifecycle;
  try {
    lifecycle = await import('../../desktop-electron/src/lifecycle.js');
    await lifecycle.startBackendAndCreateWindow(experiment);
    assert.deepEqual(events, ['/api/session-status', '/api/csrf-token', '/host-login', 'bind', 'focus']);
    assert.equal(shell.browserRoot, f.data);
    const base = `http://127.0.0.1:${shell.port}`;
    const status = await (await fetch(`${base}/api/host-mode-enabled`)).json();
    assert.equal(status.backend, 'rust');
    assert.equal(await isRustBackendReady(shell.port, status.pid), true);
    assert.equal(await isRustBackendReady(shell.port, process.pid), false);
    const signedIn = await ses.fetch(`${base}/api/session-status`, {});
    assert.equal((await signedIn.json()).logged_in, true);
    for (const page of ['/', '/chrome']) {
      const response = await ses.fetch(`${base}${page}`, {});
      assert.equal(response.status, 200, page);
      assert.match(await response.text(), /\/static\/dist\/assets\//);
    }
    const token = (await readFile(join(experiment.dataDir, 'host_api_token'), 'utf8')).trim();
    const headers = { Authorization: `Bearer ${token}`, 'X-Astrion-Workspace-Id': 'isolated-workspace' };
    const workspaces = await fetch(`${base}/api/host/workspaces`, { headers });
    const catalog = (await workspaces.json()).data;
    assert.equal(catalog.workspaces[0].path, f.workspace);
    assert.equal(catalog.current_workspace_id, 'isolated-workspace');
    const workflows = await fetch(`${base}/api/workflows`, { headers });
    assert.ok((await workflows.json()).workflows.some(workflow => workflow.name === 'feature-development'));
    const personalization = await ses.fetch(`${base}/api/personalization`, {});
    assert.ok((await personalization.json()).skills_catalog.some(skill => skill.id === 'docx'));
    assert.equal(JSON.parse(await readFile(join(f.profile, 'backend-port.json'), 'utf8')).port, shell.port);

    const created = await fetch(`${base}/api/runtime/sessions`, {
      method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: '{}'
    });
    assert.ok(created.ok);
    const { conversation_id: id } = await created.json();
    assert.ok(id);
    assert.deepEqual(await lifecycle.shutdownBackend(), { code: 0, signal: null });
    assert.equal(await isRustBackendReady(shell.port, status.pid), false);

    await lifecycle.startBackendAndCreateWindow(experiment);
    const resumed = await fetch(`http://127.0.0.1:${shell.port}/api/runtime/sessions/${id}/history`, { headers });
    assert.equal(resumed.status, 200);
    const restored = (await resumed.json()).conversation;
    assert.equal(restored.id ?? restored.conversation_id, id);
    assert.deepEqual(await lifecycle.shutdownBackend(), { code: 0, signal: null });

    await t.test('normal desktop migrates an older session explicitly and restores it after restart', async subtest => {
      shell.normalRoot = join(f.root, 'python-data');
      const legacy = await pythonFixture(subtest, shell.normalRoot, f.workspace);
      const original = await readFile(legacy.path);
      const tokenPath = join(shell.normalRoot, 'host/data/host_api_token');
      const before = await readFile(tokenPath, 'utf8');
      const saved = new Map();
      const set = (key, value) => {
        if (!saved.has(key)) saved.set(key, process.env[key]);
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      };
      subtest.after(() => { for (const [key, value] of saved) set(key, value); });
      for (const key of ['ASTRION_DESKTOP_INITIAL_WORKSPACE', 'DATA_DIR', 'DEPLOY_CONFIG_DIR', 'LOGS_DIR', 'HOST_WORKSPACES_FILE', 'ASTRION_MODEL_API_KEY', 'ASTRION_CLI_BACKEND']) set(key, undefined);
      set('ASTRION_RUST_MODEL_ENDPOINT', legacy.endpoint);
      set('ASTRION_RUST_MODEL', legacy.model);
      const normal = resolveDesktopBackend({ env: {}, platform: 'darwin', resourcesPath: f.resourcesPath });
      assert.equal(normal.experimental, false);
      await lifecycle.startBackendAndCreateWindow(normal);
      set('ASTRION_API_BASE', `http://127.0.0.1:${shell.port}`);
      set('ASTRION_HOST_TOKEN_FILE', tokenPath);
      const gateway = new GatewayClient();
      await gateway.ensureRunning(f.workspace);
      assert.equal((await gateway.listWorkspaces()).current_workspace_id, 'python-workspace');
      assert.equal(await readFile(tokenPath, 'utf8'), before);
      const origin = `http://127.0.0.1:${shell.port}`;
      const migration = `${origin}/api/conversation-storage/migration`;
      const details = `${origin}/api/conversations/${legacy.id}/bootstrap?workspace_id=python-workspace`;
      const conflict = await ses.fetch(details, {});
      assert.equal(conflict.status, 409);
      assert.equal((await conflict.json()).code, 'conversation_migration_required');
      const pending = await ses.fetch(migration, {});
      assert.equal((await pending.json()).pending, 1);
      const csrf = (await (await ses.fetch(`${origin}/api/csrf-token`, {})).json()).token;
      const accepted = await ses.fetch(migration, { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
      assert.equal(accepted.status, 200);
      await waitFor('conversation storage migration to finish', async () => {
        const status = await (await ses.fetch(migration, {})).json();
        if (status.running) return false;
        assert.equal(status.pending, 0);
        assert.equal(status.failed, 0);
        assert.equal(status.migrated, 1);
        return true;
      });
      assert.equal((await ses.fetch(details, {})).status, 200);
      await legacy.append(gateway);
      assert.deepEqual(await lifecycle.shutdownBackend(), { code: 0, signal: null });
      await lifecycle.startBackendAndCreateWindow(normal);
      set('ASTRION_API_BASE', `http://127.0.0.1:${shell.port}`);
      const restarted = new GatewayClient();
      await restarted.ensureRunning(f.workspace);
      await legacy.verify(restarted);
      assert.deepEqual(await lifecycle.shutdownBackend(), { code: 0, signal: null });
      assert.deepEqual(await readFile(legacy.path), original);
      await assert.rejects(readFile(join(shell.normalRoot, '.astrion-rust-data')), { code: 'ENOENT' });
    });
  } finally {
    try { await lifecycle?.shutdownBackend(); }
    finally { hooks.deregister(); delete globalThis.astrionTestShell; }
  }
});

test('Desktop probe rejects an unrelated occupant, and isolated directories reject real state', async t => {
  const f = await fixture(t);
  const resources = join(f.resourcesPath, 'product');
  stageRustResources(resources);
  const env = { ...f.env, ASTRION_RUST_BACKEND_BIN: binary, ASTRION_RUST_STATIC_ROOT: staticRoot, ASTRION_RUST_RESOURCE_ROOT: resources };
  await writeFile(join(f.data, 'synthetic-existing-state'), 'keep');
  assert.throws(() => resolveDesktopBackend({ env, platform: 'darwin' }), /initially empty data directory/);
  assert.deepEqual(await readdir(f.data), ['synthetic-existing-state']);
  assert.deepEqual(await readdir(f.profile), []);
  assert.equal(resolveDesktopBackend({ env: { ASTRION_DESKTOP_BACKEND: 'python' }, platform: 'win32' }), null);
  for (const platform of ['darwin', 'win32']) {
    const source = resolveDesktopBackend({
      env: { ASTRION_RUST_BACKEND_BIN: binary, ASTRION_RUST_STATIC_ROOT: staticRoot, ASTRION_RUST_RESOURCE_ROOT: resources },
      platform,
    });
    assert.equal(source.binary, binary);
    assert.equal(source.experimental, false);
  }
  const server = createServer((_req, res) => res.end(JSON.stringify({ backend: 'rust', pid: process.pid + 1 })));
  const port = await listenOnLoopback(server);
  t.after(() => closeServer(server));
  assert.equal(await isRustBackendReady(port, process.pid), false);
});

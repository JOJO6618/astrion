import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { registerHooks } from 'node:module';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { closeServer, listenOnLoopback, waitFor } from './gateway_harness.mjs';
import { GatewayClient } from '../../cli/src/gateway.ts';
import { stageRustResources } from '../../desktop/scripts/prepare_rust_backend.mjs';
import { pythonFixture } from './cli_in_place_fixture.mjs';

const hooks = registerHooks({ resolve(specifier, context, next) {
  return next(specifier === './gateway' && context.parentURL?.endsWith('/localconfig.ts') ? './gateway.ts' : specifier, context);
} });
const { loadCustomModels, loadPersonalizationDefaults } = await import('../../cli/src/localconfig.ts');
hooks.deregister();
const testDir = fileURLToPath(new URL('.', import.meta.url));
const binary = resolve(process.env.ASTRION_HEADLESS_BIN || join(testDir, '../../backend-rust/target/debug', process.platform === 'win32' ? 'astrion-headless.exe' : 'astrion-headless'));

function environment(t, values) {
  const saved = new Map(Object.keys(values).map(key => [key, process.env[key]]));
  function apply(entries) {
    for (const [key, value] of entries) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
  apply(Object.entries(values));
  t.after(() => apply(saved));
}

async function fixture(t) {
  const temp = await realpath(await mkdtemp(join(testDir, 'cli_startup-')));
  const root = join(temp, 'isolated-data');
  const workspace = join(temp, '工作区');
  const resources = join(temp, 'resources');
  stageRustResources(resources);
  const source = join(temp, 'synthetic-existing-data');
  await Promise.all([mkdir(root), mkdir(workspace), mkdir(join(source, 'host/data'), { recursive: true }), mkdir(join(source, 'config'), { recursive: true })]);
  await writeFile(join(source, 'host/data/personalization.json'), '{"default_model":"real-config-should-not-load"}');
  await writeFile(join(source, 'config/custom_models.json'), '{"models":[{"model_name":"real-config-should-not-load"}]}');
  let ownedPid;
  let port;
  t.after(async () => {
    if (ownedPid) {
      process.kill(ownedPid, 'SIGTERM');
      await waitFor('owned Rust process to release its listener', async () => {
        try { await fetch(`http://127.0.0.1:${port}/api/host-mode-enabled`); return false; }
        catch { return true; }
      });
    }
    await rm(temp, { recursive: true, force: true });
  });
  const reservation = createServer();
  port = await listenOnLoopback(reservation);
  await closeServer(reservation);
  environment(t, {
    ASTRION_CLI_BACKEND: 'rust', ASTRION_RUST_EXPERIMENT: '1', ASTRION_RUST_DATA_ROOT: root, ASTRION_RUST_BACKEND_BIN: binary,
    ASTRION_RUST_RESOURCE_ROOT: resources, ASTRION_API_PORT: String(port), ASTRION_API_BASE: undefined,
    ASTRION_HOST_TOKEN_FILE: undefined, ASTRION_RUST_MODEL_ENDPOINT: undefined, ASTRION_RUST_MODEL: undefined,
    ASTRION_DATA_ROOT: source, DEPLOY_CONFIG_DIR: join(source, 'config'),
    LOGS_DIR: join(source, 'logs'), HOST_WORKSPACES_FILE: join(source, 'missing.json')
  });
  return { temp, root, workspace, source, port, track(pid) { ownedPid = pid; } };
}

test('CLI starts one real Rust Gateway and reuses it with the same workspace and token', async t => {
  const f = await fixture(t);
  assert.deepEqual(loadCustomModels(), []);
  assert.equal(loadPersonalizationDefaults().model, '');
  await mkdir(join(f.root, 'config'));
  await writeFile(join(f.root, '.astrion-rust-data'), '');
  await writeFile(join(f.root, 'config/host_workspaces.json'), JSON.stringify({
    workspaces: [{ workspace_id: 'isolated-workspace', label: 'Offline fixture', path: f.workspace }]
  }));
  const phases = [];
  const gateway = new GatewayClient();
  await gateway.ensureRunning(f.workspace, phase => phases.push(phase));
  const response = await fetch(`${gateway.baseUrl}/api/host-mode-enabled`);
  const status = await response.json();
  f.track(status.pid);
  assert.equal(status.backend, 'rust');
  assert.deepEqual(phases, ['connecting', 'starting']);
  assert.notEqual(status.pid, process.pid);
  assert.ok((await readFile(join(f.root, 'host/data/host_api_token'), 'utf8')).trim());
  assert.match(await readFile(join(f.root, 'host/logs/cli_spawned_server.log'), 'utf8'), /Rust server listening/);
  await assert.rejects(readFile(join(f.source, 'host/data/host_api_token')), { code: 'ENOENT' });
  const { workspaces, current_workspace_id } = await gateway.listWorkspaces();
  assert.equal(workspaces[0].path, f.workspace);
  assert.equal(current_workspace_id, 'isolated-workspace');
  gateway.workspaceId = current_workspace_id;
  assert.ok((await gateway.createSession()).conversation_id);
  assert.ok((await gateway.listWorkflows()).some(workflow => workflow.name === 'feature-development'));

  phases.length = 0;
  const second = new GatewayClient();
  await second.ensureRunning(f.workspace, phase => phases.push(phase));
  assert.deepEqual(phases, ['connecting']);
  assert.equal((await (await fetch(`${second.baseUrl}/api/host-mode-enabled`)).json()).pid, status.pid);
  await second.ensureRunning(f.temp);
  assert.ok((await second.listWorkspaces()).workspaces.every(workspace => workspace.path !== f.temp));

  delete process.env.ASTRION_CLI_BACKEND;
  process.env.ASTRION_API_BASE = gateway.baseUrl;
  process.env.ASTRION_HOST_TOKEN_FILE = join(f.root, 'host/data/host_api_token');
  const paired = new GatewayClient();
  await paired.ensureRunning(f.workspace);
  assert.equal((await paired.listWorkspaces()).workspaces[0].path, f.workspace);
  assert.deepEqual(loadCustomModels(), []);
  assert.equal(loadPersonalizationDefaults().model, '');
});

test('CLI refuses a pre-existing service and a non-experiment data root before starting', async t => {
  const f = await fixture(t);
  const received = [];
  const occupied = createServer((request, response) => { received.push(request.headers.authorization); response.end('{}'); });
  process.env.ASTRION_API_PORT = String(await listenOnLoopback(occupied));
  t.after(() => closeServer(occupied));
  const phases = [];
  await assert.rejects(new GatewayClient().ensureRunning(f.workspace, phase => phases.push(phase)), /Another service/);
  assert.deepEqual(phases, ['connecting']);
  assert.deepEqual(received, [undefined]);
  await assert.rejects(readFile(join(f.root, 'host/data/host_api_token')), { code: 'ENOENT' });

  process.env.ASTRION_RUST_DATA_ROOT = f.source;
  await assert.rejects(new GatewayClient().ensureRunning(f.workspace), /must be initially empty/);
  await assert.rejects(readFile(join(f.source, '.astrion-rust-data')), { code: 'ENOENT' });
});

test('normal CLI defaults to Rust and preserves a legacy row pending explicit migration', async t => {
  const f = await fixture(t);
  const legacy = await pythonFixture(t, f.root, f.workspace);
  const original = await readFile(legacy.path);
  const tokenPath = join(f.root, 'host/data/host_api_token');
  const token = await readFile(tokenPath, 'utf8');
  environment(t, {
    ASTRION_CLI_BACKEND: undefined, ASTRION_RUST_EXPERIMENT: undefined, ASTRION_RUST_DATA_ROOT: undefined,
    ASTRION_DATA_ROOT: f.root, DATA_DIR: undefined, DEPLOY_CONFIG_DIR: undefined,
    LOGS_DIR: undefined, HOST_WORKSPACES_FILE: undefined,
    ASTRION_MODEL_API_KEY: undefined,
  });
  const gateway = new GatewayClient();
  await gateway.ensureRunning(f.workspace);
  const status = await (await fetch(`${gateway.baseUrl}/api/host-mode-enabled`)).json();
  f.track(status.pid);
  assert.equal(status.backend, 'rust');
  assert.equal((await gateway.listWorkspaces()).current_workspace_id, 'python-workspace');
  assert.equal(await readFile(tokenPath, 'utf8'), token);
  gateway.workspaceId = 'python-workspace';
  const row = (await gateway.listSessions()).find(item => item.id === legacy.id);
  assert.equal(row?.incompatible, true);
  await assert.rejects(gateway.getSessionHistory(legacy.id), /不兼容的对话记录/);
  const { conversation_id } = await gateway.createSession();
  assert.ok(conversation_id);
  assert.notEqual(conversation_id, legacy.id);
  assert.deepEqual(await readFile(legacy.path), original);
  await assert.rejects(readFile(join(f.root, '.astrion-rust-data')), { code: 'ENOENT' });
});

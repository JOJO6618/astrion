// node test/2026-11-27_CLI部署配置/cli_startup.mjs /absolute/path/astrion-headless
// Run only the production Gateway (no TUI); the model catalog uses a synthetic source seed.
import assert from 'node:assert/strict';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const binary = path.resolve(process.argv[2] || '');
assert(process.argv[2] && existsSync(binary), 'Pass the built Rust sidecar as the first argument');
const source = mkdtempSync(path.join(tmpdir(), 'astrion-cli-source-'));
let pid;
let origin;
const hook = registerHooks({
  resolve(specifier, context, next) {
    return next(specifier === './gateway' && context.parentURL?.endsWith('/cli/src/localconfig.ts')
      ? './gateway.ts' : specifier, context);
  },
});

async function freePort() {
  const socket = createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const { port } = socket.address();
  await new Promise((resolve, reject) => socket.close(error => error ? reject(error) : resolve()));
  return port;
}

try {
  for (const file of [
    'cli/src/gateway.ts', 'cli/src/localconfig.ts', 'scripts/host-config.mjs',
    'config/providers_catalog.json',
  ]) {
    const target = path.resolve(source, file);
    mkdirSync(path.dirname(target), { recursive: true });
    copyFileSync(path.resolve(repo, file), target);
  }
  for (const name of Object.keys(process.env)) {
    if (/^(ASTRION_|WEB_|HOST_|AGENT_|(?:DATA|LOGS|DEPLOY_CONFIG)_DIR$|LINUX_SAFETY$|TERMINAL_SANDBOX_MODE$)/.test(name)) {
      delete process.env[name];
    }
  }
  const home = path.join(source, 'synthetic-home');
  const project = path.join(source, 'project');
  const configRoot = path.join(source, 'deployment');
  const selectedData = path.join(source, 'data override');
  const logs = path.join(source, 'server logs');
  const registry = path.join(configRoot, 'workspace-registry.json');
  for (const directory of [home, project, configRoot, path.join(source, 'workflows')]) mkdirSync(directory);
  Object.assign(process.env, {
    HOME: home, USERPROFILE: home, XDG_CONFIG_HOME: home, XDG_DATA_HOME: home,
    XDG_CACHE_HOME: home, APPDATA: home, LOCALAPPDATA: home, DATA_DIR: 'data override',
    ASTRION_RUST_BACKEND_BIN: binary,
  });
  const port = await freePort();
  origin = `http://127.0.0.1:${port}`;
  writeFileSync(path.join(source, '.env'), [
    'ASTRION_DATA_ROOT=deployment', 'DATA_DIR=must-not-overwrite-shell',
    'HOST_WORKSPACES_FILE="deployment/workspace-registry.json"', '',
  ].join('\n'));
  writeFileSync(path.join(configRoot, 'settings.json'), JSON.stringify({
    server: { port },
    env_vars: { LOGS_DIR: 'server logs' },
    search: { tavily_api_key: 'placeholder-test-value' },
  }));
  writeFileSync(registry, JSON.stringify({
    default_workspace_id: 'fixture',
    workspaces: [{ workspace_id: 'fixture', label: 'Fixture', path: project }],
  }));
  writeFileSync(path.join(source, 'config/custom_models.json'), JSON.stringify({
    models: [{
      model_name: 'source-seed', model_id: 'fake-model', url: 'http://127.0.0.1:9/v1',
      apikey: 'fixture-key', visible: true,
    }],
  }));

  const entry = pathToFileURL(path.join(source, 'cli/src/gateway.ts')).href;
  const { GatewayClient, cliConfigRoot, cliDataDir, cliResourceDir } = await import(entry);
  const { loadCustomModels } = await import(pathToFileURL(path.join(source, 'cli/src/localconfig.ts')));
  assert.equal(cliConfigRoot(), configRoot);
  assert.equal(cliDataDir(), selectedData);
  assert.equal(cliResourceDir(), source);
  assert.equal(loadCustomModels()[0]?.name, 'source-seed');
  const client = new GatewayClient();
  const phases = [];
  await client.ensureRunning(project, phase => phases.push(phase));
  const health = await fetch(`${origin}/api/host-mode-enabled`).then(response => response.json());
  pid = health.pid;
  assert.equal(health.backend, 'rust');
  assert.deepEqual(phases, ['connecting', 'starting']);
  const tokenFile = path.join(selectedData, 'host_api_token');
  assert(readFileSync(tokenFile, 'utf8').trim());
  assert(existsSync(path.join(logs, 'cli_spawned_server.log')));
  assert(!existsSync(path.join(source, 'must-not-overwrite-shell')));
  assert.equal((await client.listWorkspaces()).current_workspace_id, 'fixture');
  const catalog = await fetch(`${origin}/api/v1/models`).then(response => response.json());
  assert(JSON.stringify(catalog).includes('"source-seed"'), JSON.stringify(catalog).slice(0, 200));

  // An explicitly paired invocation must not use the source deployment to
  // override its token or start a second backend.
  process.env.ASTRION_API_BASE = origin;
  process.env.ASTRION_HOST_TOKEN_FILE = tokenFile;
  assert.equal(cliConfigRoot(), null);
  const paired = new GatewayClient();
  await paired.ensureRunning(project, () => {});
  assert.equal((await paired.listWorkspaces()).current_workspace_id, 'fixture');
  console.log('CLI dotenv/settings, source-model fallback, and paired Gateway passed');
} finally {
  hook.deregister();
  if (Number.isSafeInteger(pid) && pid > 0) {
    process.kill(pid, 'SIGTERM');
    for (let retry = 0; retry < 100; retry++) {
      try { await fetch(`${origin}/api/host-mode-enabled`); } catch { break; }
      await delay(25);
    }
  }
  rmSync(source, { recursive: true, force: true });
}

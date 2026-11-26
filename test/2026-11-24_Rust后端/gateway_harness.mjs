import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { constants } from 'node:fs';
import { access, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const TEST_DIR = fileURLToPath(new URL('.', import.meta.url));
export const WORKSPACE_ID = 'default';
export const COMPLETED = new Set(['succeeded', 'completed', 'done']);
export const CANCELED = new Set(['stopped', 'canceled']);
const TERMINAL = new Set([...COMPLETED, ...CANCELED, 'failed', 'error']);

export async function listenOnLoopback(server) {
  const listening = once(server, 'listening');
  server.listen(0, '127.0.0.1');
  await listening;
  return server.address().port;
}

export function closeServer(server) {
  return new Promise((resolveClose, rejectClose) => {
    const timer = setTimeout(() => rejectClose(new Error('Timed out closing the loopback HTTP mock')), 1_000);
    server.close((error) => {
      clearTimeout(timer);
      if (error) rejectClose(error);
      else resolveClose();
    });
    // An assertion can fail while the provider has a deliberately open SSE response.
    server.closeAllConnections();
  });
}

export async function waitFor(description, check, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  do {
    const result = await check();
    if (result) return result;
    await delay(20);
  } while (Date.now() < deadline);
  throw new Error(`Timed out waiting for ${description}`);
}

async function exitsWithin(exit, milliseconds) {
  const timer = new AbortController();
  try {
    return await Promise.race([exit, delay(milliseconds, false, { signal: timer.signal })]);
  } finally {
    timer.abort();
  }
}

async function startRust(options, backends) {
  const { binary, workspace, dataDir, workspaceCatalog, endpoint, model, apiKey } = options;
  const reservation = createServer();
  const port = await listenOnLoopback(reservation);
  // Close the temporary reservation so the test-owned binary can bind this port.
  await new Promise((resolveClose, rejectClose) => {
    reservation.close((error) => error ? rejectClose(error) : resolveClose());
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  const child = spawn(binary, [
    '--port', String(port), '--workspace', workspace, '--data-dir', dataDir,
    ...(model === undefined ? [] : ['--endpoint', endpoint, '--model', model]),
    ...(options.web ? ['--web', '--static-root', workspace] : []),
  ], {
    cwd: workspace,
    env: {
      PATH: process.env.PATH,
      HOME: dataDir,
      USERPROFILE: dataDir,
      ...(process.platform === 'win32' && { SystemRoot: process.env.SystemRoot }),
      ASTRION_IGNORE_DOTENV: '1',
      ASTRION_DESKTOP_STOP_STDIN: '1',
      HOST_WORKSPACES_FILE: workspaceCatalog,
      ...(apiKey && { ASTRION_MODEL_API_KEY: apiKey }),
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let output = '';
  let childError;
  child.on('error', (error) => { childError = error; });
  child.stdin.on('error', (error) => { childError = error; });
  const exited = new Promise((resolveExit) => child.once('exit', () => resolveExit(true)));
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => { output = (output + chunk).slice(-4_000); });
  }
  const isRunning = () => Boolean(child.pid) && child.exitCode === null && child.signalCode === null;
  const processLabel = `test Rust backend PID ${child.pid} with data directory ${dataDir}`;
  async function terminate() {
    try {
      if (!isRunning()) return;
      child.stdin.end();
      if (!(await exitsWithin(exited, 5_000))) {
        throw new Error(`Timed out waiting for ${processLabel} to stop after stdin EOF. Build with ASTRION_DESKTOP_STOP_STDIN support.\n${output}`, { cause: childError });
      }
      assert.equal(child.exitCode, 0, `${processLabel} did not exit cleanly after stdin EOF.\n${output}`);
    } finally {
      child.stdin.destroy();
      child.stdout.destroy();
      child.stderr.destroy();
      child.unref();
    }
  }
  let stopping;
  const workspacesUrl = `${baseUrl}/api/host/workspaces`;
  const backend = { baseUrl, workspacesUrl, isRunning, stop: () => (stopping ??= terminate()) };
  backends.push(backend);
  try {
    await waitFor('the Rust workspace route to accept HTTP connections', async () => {
      if (childError) throw childError;
      assert.ok(isRunning(), `Rust backend exited during startup (${child.exitCode ?? child.signalCode})`);
      let response;
      try {
        response = await fetch(workspacesUrl, { signal: AbortSignal.timeout(500) });
      } catch {
        return false;
      }
      await response.text();
      assert.equal(response.status, 401, 'The workspace route must require the host Bearer token');
      return true;
    });
  } catch (error) {
    throw new Error(`${error.message}\nRust output: ${output || '(empty)'}`, { cause: error });
  }
  return backend;
}

async function connectGateway(backend, tokenPath, workspace, workspaceId) {
  const token = (await readFile(tokenPath, 'utf8')).trim();
  assert.ok(token, 'The Rust backend must generate a host token in --data-dir');
  const rejected = await fetch(backend.workspacesUrl, {
    headers: { Authorization: `Bearer ${token}-invalid` },
    signal: AbortSignal.timeout(1_000),
  });
  await rejected.text();
  assert.equal(rejected.status, 401, 'The workspace route must reject an invalid host token');

  process.env.ASTRION_API_BASE = backend.baseUrl;
  process.env.ASTRION_HOST_TOKEN_FILE = tokenPath;
  // GatewayClient resolves the token file at module load time. Give each test data directory its own import.
  const gatewaySource = new URL('../../cli/src/gateway.ts', import.meta.url);
  gatewaySource.searchParams.set('fixture', tokenPath);
  const { GatewayClient } = await import(gatewaySource.href);
  const gateway = new GatewayClient();
  assert.ok((await gateway.readToken()) === token, 'GatewayClient must read the isolated token file');
  const phases = [];
  await gateway.ensureRunning(workspace, (phase) => phases.push(phase));
  assert.deepEqual(phases, ['connecting']);
  const result = await gateway.listWorkspaces();
  assert.equal(result.current_workspace_id ?? null, workspaceId);
  const match = result.workspaces.find((item) => item.path.replace(/\/+$/, '') === workspace);
  assert.equal(match?.workspace_id ?? null, workspaceId, 'The isolated catalog must match the launch path');
  if (match) gateway.workspaceId = match.workspace_id;
  return gateway;
}

// Use one fixture at a time per Node test process. Independently run test files use separate processes.
// If provided, startMock returns an idempotent close function and any scenario-specific controls.
// Set model to pin the backend to the mock's endpoint. Otherwise Rust loads the on-disk catalog.
export async function startGatewayFixture(t, {
  model, startMock, prepare, apiKey, web = false, workspaceIds = [WORKSPACE_ID],
  launchWorkspaceId = workspaceIds[0] ?? null,
} = {}) {
  const binarySuffix = process.platform === 'win32' ? '.exe' : '';
  const binary = resolve(process.env.ASTRION_HEADLESS_BIN || join(TEST_DIR, '../../backend-rust/target/debug', `astrion-headless${binarySuffix}`));
  await access(binary, constants.X_OK).catch((error) => {
    throw new Error(`Build astrion-headless or set ASTRION_HEADLESS_BIN. Executable unavailable: ${binary}`, { cause: error });
  });

  const root = await mkdtemp(join(TEST_DIR, 'gateway-run-'));
  const dataDir = join(root, 'data');
  const workspaceCatalog = join(dataDir, 'host_workspaces.json');
  const tokenPath = join(dataDir, 'host_api_token');
  const savedEnv = new Map(['ASTRION_API_BASE', 'ASTRION_HOST_TOKEN_FILE', 'ASTRION_CLI_BACKEND'].map((key) => [key, process.env[key]]));
  const backends = [];
  let mock;
  t.after(async () => {
    for (const [key, value] of savedEnv) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    const cleanup = await Promise.allSettled([
      Promise.resolve().then(() => mock?.close()),
      ...backends.map((backend) => backend.stop()),
    ]);
    if (backends.every((backend) => !backend.isRunning())) {
      await rm(root, { force: true, recursive: true });
    }
    const failures = cleanup.filter((item) => item.status === 'rejected').map((item) => item.reason);
    if (failures.length) {
      throw new AggregateError(failures, failures.map((error) => error.message).join('\n'));
    }
  });

  const workspaces = [];
  for (const [index, workspaceId] of workspaceIds.entries()) {
    const path = join(root, index === 0 ? 'workspace' : `workspace-${index + 1}`);
    await mkdir(path);
    workspaces.push({ workspace_id: workspaceId, label: workspaceId, path: await realpath(path) });
  }
  const selected = workspaces.find((item) => item.workspace_id === launchWorkspaceId);
  assert.ok(selected || launchWorkspaceId === null, 'An explicit launch workspace ID must be registered');
  const launchDirectory = selected?.path ?? join(root, 'unregistered-workspace');
  if (!selected) await mkdir(launchDirectory);
  const workspace = await realpath(launchDirectory);
  await mkdir(dataDir);
  await writeFile(workspaceCatalog, JSON.stringify({
    default_workspace_id: workspaces[0]?.workspace_id ?? null, workspaces,
  }));
  delete process.env.ASTRION_CLI_BACKEND;
  mock = await startMock?.();
  const endpoint = mock?.endpoint;
  if (model !== undefined) assert.ok(endpoint, 'A fixed model requires a mock endpoint');
  await prepare?.({ workspace, dataDir, mock });
  const options = { binary, workspace, dataDir, workspaceCatalog, endpoint, model, apiKey, web };
  let backend;
  async function connect() {
    backend = await startRust(options, backends);
    return connectGateway(backend, tokenPath, workspace, launchWorkspaceId);
  }
  return {
    gateway: await connect(),
    mock,
    workspace,
    workspaces,
    workspaceCatalog,
    tokenPath,
    dataDir,
    async restart({ apiKey: nextApiKey = options.apiKey } = {}) {
      await backend.stop();
      options.apiKey = nextApiKey;
      return connect();
    },
  };
}

export function trackTask(gateway, taskId, conversationId) {
  let offset = 0;
  const events = [];
  return {
    taskId,
    conversationId,
    events,
    get offset() { return offset; },
    until(description, matches) {
      return waitFor(description, async () => {
        const poll = await gateway.pollTask(taskId, offset);
        assert.equal(poll.task_id, taskId);
        assert.equal(poll.conversation_id, conversationId);
        assert.ok((poll.window_start ?? 0) <= offset, 'The fixture must stay inside the event window');
        for (const [index, event] of poll.events.entries()) {
          assert.equal(event.idx, offset + index, 'A cursor poll must return each event once and in order');
        }
        assert.equal(poll.next_offset, offset + poll.events.length);
        offset = poll.next_offset;
        events.push(...poll.events);
        if (matches(poll)) return poll;
        if (TERMINAL.has(poll.status)) {
          throw new Error(`Task ended as ${poll.status} while waiting for ${description}: ${poll.error ?? ''}`);
        }
        return undefined;
      });
    },
  };
}

export function streamedText(events) {
  return events.filter((event) => event.type === 'text_chunk').map((event) => event.data.content).join('');
}

export async function createTask(gateway, conversationId, message, settings = {}) {
  const task = await gateway.createTask({
    thinking_mode: false, run_mode: 'fast', ...settings, conversation_id: conversationId, message,
  });
  assert.ok(task.task_id);
  assert.ok(task.conversation_id);
  if (conversationId) assert.equal(task.conversation_id, conversationId);
  return trackTask(gateway, task.task_id, task.conversation_id);
}

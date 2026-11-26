// Python 后端子进程管理（移植自 desktop/src-tauri/src/backend.rs，行为逐条对齐）。
//
// 开发期：探测系统 python（项目 .venv → homebrew 3.13/3.12/3.11 → PATH python3），
//   要求 import yaml/flask/httpx/openai 可用；
// 生产期：使用打包进来的 python-build-standalone 解释器（resources/runtime/python）
//   + 内嵌源码（resources/runtime/backend），目标机器无 Python 也能运行。

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { app } from 'electron';
import { credentialTransport } from './backend-transport.js';
import { backendLaunch } from './windows/backend-guardian.js';
import { windowsHelper } from './windows/native-helper.js';
import { rustGuardianLaunch, watchGuardedPid } from './windows/rust-guardian.js';
import { desktopUpdateIdentity } from './update-policy.js';
import { isRustBackendReady, rustDesktopLaunch, RUST_PROBE_PATH } from './rust-backend.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** 后端启动就绪超时（首次启动需写日志/同步角色，给足余量） */
const BACKEND_READY_TIMEOUT_MS = 60_000;
/** 就绪轮询间隔 */
const READY_POLL_INTERVAL_MS = 200;
const RUST_STOP_GRACE_MS = 5_000;
const RUST_STOP_FORCE_MS = 1_000;

/** @type {import('node:child_process').ChildProcess | null} */
let backendChild = null;

/** 上次成功使用的后端端口（userData 内落盘）。 */
const PORT_FILE_NAME = 'backend-port.json';

function portFile() {
  return path.join(app.getPath('userData'), PORT_FILE_NAME);
}

/**
 * 读取上次成功使用的端口；无记录或值非法返回 0。
 *
 * 端口稳定很重要：session cookie 名（agents_session_<port>）与 localStorage
 * 都按 origin（含端口）隔离，每次启动换端口等于丢掉登录态、用户主题和前端本地缓存。
 */
export function loadPreferredPort() {
  try {
    const saved = JSON.parse(fs.readFileSync(portFile(), 'utf8')).port;
    return Number.isInteger(saved) && saved > 1024 && saved < 65536 ? saved : 0;
  } catch {
    return 0;
  }
}

/** 记录本次成功使用的端口；写盘失败不影响运行。 */
export function savePreferredPort(port) {
  if (!Number.isInteger(port) || port <= 0) return;
  try {
    fs.writeFileSync(portFile(), JSON.stringify({ port }), { mode: 0o600 });
  } catch {
    /* 只读或异常磁盘：下次启动回落到随机端口 */
  }
}

/** 端口此刻能否 bind（探测后立即释放；到 spawn 之间仍有极小竞态，调用方负责回退）。 */
export function isPortAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
  });
}

/** 优先复用上次端口；不可用则随机。 */
export async function resolveBackendPort() {
  const preferred = loadPreferredPort();
  if (preferred && (await isPortAvailable(preferred))) return preferred;
  return pickFreePort();
}

/** 选取空闲端口：listen(0) 由系统分配后立即释放（理论竞态同 Tauri 版，本地可接受）。 */
export function pickFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

/**
 * 内嵌运行时解析（生产形态）：resources/runtime/python + resources/backend。
 * @returns {{python: string, backendDir: string} | null}
 */
function resolveEmbeddedRuntime() {
  if (!process.resourcesPath) return null;
  const python =
    process.platform === 'win32'
      ? firstExisting([
          path.join(process.resourcesPath, 'runtime/python/astrion-backend.exe'),
          path.join(process.resourcesPath, 'runtime/python/python.exe')
        ])
      : path.join(process.resourcesPath, 'runtime/python/bin/python3.12');
  const backend = path.join(process.resourcesPath, 'runtime/backend');
  if (python && fs.existsSync(python) && fs.existsSync(path.join(backend, 'server/app.py'))) {
    return { python, backendDir: backend };
  }
  return null;
}

function firstExisting(paths) {
  return paths.find((p) => fs.existsSync(p)) ?? null;
}

/** 项目根目录：ASTRION_DESKTOP_REPO 显式覆盖 > desktop-electron 上一级。 */
function resolveRepoRoot() {
  const explicit = process.env.ASTRION_DESKTOP_REPO;
  if (explicit) {
    if (fs.existsSync(path.join(explicit, 'server/app.py'))) return explicit;
    throw new Error(`ASTRION_DESKTOP_REPO 指向的目录不含 server/app.py: ${explicit}`);
  }
  const root = path.resolve(__dirname, '..', '..');
  if (!fs.existsSync(path.join(root, 'server/app.py'))) {
    throw new Error(`推导的仓库根不含 server/app.py: ${root}`);
  }
  return root;
}

/** python 候选解释器（按优先级，对齐 CLI gateway.ts 与 Tauri 壳的探测顺序）。 */
function pythonCandidates(repoRoot) {
  const candidates = [];
  if (process.platform === 'win32') {
    candidates.push(path.join(repoRoot, '.venv/Scripts/python.exe'));
  } else {
    candidates.push(path.join(repoRoot, '.venv/bin/python'));
    for (const v of ['python3.13', 'python3.12', 'python3.11', 'python3']) {
      candidates.push(`/opt/homebrew/bin/${v}`);
    }
  }
  const pathVar = process.env.PATH || '';
  for (const dir of pathVar.split(path.delimiter)) {
    if (dir) candidates.push(path.join(dir, process.platform === 'win32' ? 'python.exe' : 'python3'));
  }
  return candidates;
}

function pythonHasDeps(python) {
  if (!fs.existsSync(python)) return false;
  const result = spawnSync(python, ['-c', 'import yaml, flask, httpx, openai'], {
    stdio: 'ignore'
  });
  return result.status === 0;
}

function detectPython(repoRoot) {
  return pythonCandidates(repoRoot).find((c) => pythonHasDeps(c)) ?? null;
}

/**
 * 运行时解析：生产优先（.app 内嵌 python-build-standalone + 源码）；
 * 不存在则回退开发模式（系统 python + 仓库源码树）。
 * @returns {{python: string, backendDir: string}}
 */
export function resolveRuntime() {
  const embedded = resolveEmbeddedRuntime();
  if (embedded) return embedded;
  const repoRoot = resolveRepoRoot();
  const python = detectPython(repoRoot);
  if (!python) {
    throw new Error('未找到可用 Python（需要 import yaml/flask 可用）');
  }
  return { python, backendDir: repoRoot };
}

/** 子进程环境清洗前缀（数据越权事故修复，对齐 Tauri 版注释与语义）。 */
const CHILD_ENV_SCRUB_PREFIXES = ['ASTRION_', 'AGENT_'];

/**
 * spawn 后端：环境清洗 → 显式指定数据根/桌面身份/控制桥端口。
 * @param {{python: string, backendDir: string, port: number, bridgePort: number | null, version: string, shellPath?: string | null}} opts
 */
export function spawnBackend({ python, backendDir, port, bridgePort, browserToken, version, shellPath, desktopDataRoot }) {
  // --path 语义为「兜底默认工作区」，桌面首启由用户在引导流程中自行创建
  const defaultWs = process.env.ASTRION_DESKTOP_INITIAL_WORKSPACE || os.homedir() || path.join(backendDir, 'project');

  /** @type {NodeJS.ProcessEnv} */
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (CHILD_ENV_SCRUB_PREFIXES.some((p) => key.startsWith(p))) continue;
    env[key] = value;
  }
  if (shellPath) {
    const pathEntries = [...shellPath.split(path.delimiter), ...(env.PATH || '').split(path.delimiter)]
      .map((entry) => entry.trim())
      .filter(Boolean);
    env.PATH = [...new Set(pathEntries)].join(path.delimiter);
  }
  // .app 内为只读目录：禁写 __pycache__；Windows GBK 代码页下 print 崩溃用 UTF-8 模式根治
  env.PYTHONDONTWRITEBYTECODE = '1';
  env.PYTHONUTF8 = '1';
  // 禁用仓库根 .env 加载：config/__init__.py 会让 .env 的 ASTRION_DATA_ROOT
  // 强制覆盖环境变量（防外部 shell 误指到 clone），开发形态下后端跑在源码树，
  // 不禁用会读穿到共享数据根 ~/.astrion/astrion（桌面数据隔离被破坏）。
  // 生产形态后端在 .app 内无 .env，此变量无害；桌面配置一律走自己的 settings.json。
  env.ASTRION_IGNORE_DOTENV = '1';
  // Desktop Host is password-exempt and must remain loopback-only.
  env.TERMINAL_SANDBOX_MODE = 'host';
  env.WEB_SERVER_HOST = '127.0.0.1';
  // 桌面版数据根由壳在启动前从固定运行数据设置文件解析，绝不与 server 实例共享
  env.ASTRION_DATA_ROOT = desktopDataRoot || path.join(os.homedir(), '.astrion', 'astrion-desktop');
  // 桌面应用身份与控制桥地址：后端据此判定「自己是桌面壳内嵌实例」并代理更新接口
  env.ASTRION_DESKTOP_VERSION = version;
  const updateIdentity = desktopUpdateIdentity();
  env.ASTRION_DESKTOP_BUILD_ID = updateIdentity.build_id;
  env.ASTRION_DESKTOP_PLATFORM = updateIdentity.platform;
  env.ASTRION_DESKTOP_ARCH = updateIdentity.arch;
  env.ASTRION_DESKTOP_POLICY_PROTOCOL = '1';
  if (bridgePort) env.ASTRION_DESKTOP_BRIDGE_PORT = String(bridgePort);
  // AI 浏览器端点凭据经 fd 3 管道交给后端（读完即关），不放环境变量：
  // macOS 上同用户进程可用 ps -E 读到启动环境，模型执行的命令不能拿到它。
  delete env.ASTRION_DESKTOP_BROWSER_TOKEN;
  delete env.ASTRION_DESKTOP_BROWSER_TOKEN_FD;
  const transport = credentialTransport({ bridgePort, browserToken });
  Object.assign(env, transport.env);
  const launch = backendLaunch({
    python,
    args: ['-m', 'server.app', '--backend', 'python', '--port', String(port), '--path', defaultWs],
    helperPath: path.join(__dirname.replace(/app\.asar(?=[\\/])/, 'app.asar.unpacked'), 'windows/backend-guardian.py')
  });
  const child = spawnTracked(launch, { cwd: backendDir, env, stdio: transport.stdio });
  transport.write(child);
  return child;
}

export function spawnRustBackend(runtime, options) {
  const launch = rustDesktopLaunch(runtime, { ...options, identity: desktopUpdateIdentity() });
  if (process.platform === 'win32') {
    const guarded = rustGuardianLaunch({ ...launch, guardian: windowsHelper() });
    const child = spawnTracked(guarded, { cwd: launch.cwd, env: guarded.env, stdio: guarded.stdio }, 'rust');
    child.guarded = true;
    watchGuardedPid(child);
    return child;
  }
  const child = spawnTracked(launch, { cwd: launch.cwd, env: launch.env, stdio: ['ignore', 'pipe', 'pipe'] }, 'rust');
  child.backendPid = child.pid;
  return child;
}

function spawnTracked(launch, options, backend = 'python') {
  if (backendChild) throw new Error('后端进程仍在运行');
  const child = spawn(launch.command, launch.args, { ...options, windowsHide: true });
  child.backend = backend;

  // 后端 stdout/stderr 转发（dev 终端可见；release 无控制台，后端日志走自身 logs/ 文件）
  child.stdout?.on('data', (chunk) => {
    process.stdout.write(`[astrion-backend] ${chunk}`);
  });
  child.stderr?.on('data', (chunk) => {
    process.stderr.write(`[astrion-backend] ${chunk}`);
  });
  child.on('error', error => {
    console.error('[astrion-desktop] 后端进程启动失败:', error.message);
    child.startupError = error;
  });
  child.stopped = new Promise(resolve => child.once('close', (code, signal) => {
    console.info(`[astrion-desktop] 后端进程退出: code=${code} signal=${signal}`);
    if (backendChild === child) backendChild = null;
    resolve({ code, signal });
  }));

  backendChild = child;
  return child;
}

async function probePython(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}${RUST_PROBE_PATH}`, {
      redirect: 'error', signal: AbortSignal.timeout(800)
    });
    if (response.status === 204) return true;
    if (response.status !== 200) return false;
    const payload = await response.json().catch(() => null);
    return payload?.backend !== 'rust';
  } catch {
    return false;
  }
}

/** 轮询无鉴权端点直到后端就绪。 */
export async function waitBackendReady(port, child = backendChild, backend = 'python') {
  const deadline = Date.now() + BACKEND_READY_TIMEOUT_MS;
  while (Date.now() <= deadline) {
    if (child?.startupError) throw child.startupError;
    if (child && (child.exitCode !== null || child.signalCode !== null)) {
      throw new Error(`后端在就绪前退出 (${child.exitCode ?? child.signalCode})`);
    }
    const ready = backend === 'rust' ? await isRustBackendReady(port, child?.backendPid) : await probePython(port);
    if (ready) return;
    await delay(READY_POLL_INTERVAL_MS);
  }
  throw new Error(`后端 ${BACKEND_READY_TIMEOUT_MS / 1000}s 内未就绪（127.0.0.1:${port}）`);
}

function signalBackend(child, signal) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return true;
  try { return child.kill(signal); }
  catch (error) {
    console.error('[astrion-desktop] 无法停止后端:', error.message);
    return false;
  }
}

async function waitForStop(child, timeoutMs) {
  const timer = new AbortController();
  try { return await Promise.race([child.stopped, delay(timeoutMs, false, { signal: timer.signal })]); }
  finally { timer.abort(); }
}

async function stopRust(child) {
  if (child.guarded) child.stdin.end();
  else signalBackend(child, 'SIGTERM');
  const graceful = await waitForStop(child, RUST_STOP_GRACE_MS);
  if (graceful) return graceful;
  console.warn('[astrion-desktop] Rust 后端退出超时，强制停止');
  if (!signalBackend(child, 'SIGKILL')) return false;
  return waitForStop(child, RUST_STOP_FORCE_MS);
}

/** Stop the managed child before starting a replacement. */
export function shutdownBackend() {
  const child = backendChild;
  if (!child) return;
  if (child.backend === 'rust') return child.stopping ??= stopRust(child);
  if (!child.killed && !signalBackend(child, 'SIGKILL')) return false;
  return child.stopped;
}

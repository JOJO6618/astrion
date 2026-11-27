import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { app } from 'electron';
import { credentialTransport } from './backend-transport.js';
import { windowsHelper } from './windows/native-helper.js';
import { rustGuardianLaunch, watchGuardedPid } from './windows/rust-guardian.js';
import { desktopUpdateIdentity } from './update-policy.js';
import { isRustBackendReady, rustDesktopLaunch } from './rust-backend.js';


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
export async function resolveBackendPort(preferred = loadPreferredPort()) {
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

export function spawnRustBackend(runtime, options) {
  const launch = rustDesktopLaunch(runtime, { ...options, identity: desktopUpdateIdentity() });
  const transport = credentialTransport({ bridgePort: runtime.bridgePort, browserToken: options.browserToken });
  delete launch.env.ASTRION_DESKTOP_BROWSER_TOKEN;
  delete launch.env.ASTRION_DESKTOP_BROWSER_TOKEN_FD;
  Object.assign(launch.env, transport.env);
  if (process.platform === 'win32') {
    const guarded = rustGuardianLaunch({ ...launch, guardian: windowsHelper() });
    const child = spawnTracked(guarded, { cwd: launch.cwd, env: guarded.env, stdio: guarded.stdio });
    child.guarded = true;
    watchGuardedPid(child);
    if (transport.env.ASTRION_DESKTOP_BROWSER_TOKEN_FD === '0') {
      child.stdin.write(`${options.browserToken}\n`);
    }
    return child;
  }
  const child = spawnTracked(launch, { cwd: launch.cwd, env: launch.env, stdio: transport.stdio });
  transport.write(child);
  child.backendPid = child.pid;
  return child;
}

function spawnTracked(launch, options) {
  if (backendChild) throw new Error('后端进程仍在运行');
  const child = spawn(launch.command, launch.args, { ...options, windowsHide: true });

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

/** 轮询无鉴权端点直到后端就绪。 */
export async function waitBackendReady(port, child = backendChild) {
  const deadline = Date.now() + BACKEND_READY_TIMEOUT_MS;
  while (Date.now() <= deadline) {
    if (child?.startupError) throw child.startupError;
    if (child && (child.exitCode !== null || child.signalCode !== null)) {
      throw new Error(`后端在就绪前退出 (${child.exitCode ?? child.signalCode})`);
    }
    const ready = await isRustBackendReady(port, child?.backendPid);
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
  return child.stopping ??= stopRust(child);
}

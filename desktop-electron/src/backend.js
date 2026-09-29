// Python 后端子进程管理（移植自 desktop/src-tauri/src/backend.rs，行为逐条对齐）。
//
// 开发期：探测系统 python（项目 .venv → homebrew 3.13/3.12/3.11 → PATH python3），
//   要求 import yaml/flask/httpx/openai 可用；
// 生产期：使用打包进来的 python-build-standalone 解释器（resources/runtime/python）
//   + 内嵌源码（resources/runtime/backend），目标机器无 Python 也能运行。

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** 后端启动就绪超时（首次启动需写日志/同步角色，给足余量） */
const BACKEND_READY_TIMEOUT_MS = 60_000;
/** 就绪轮询间隔 */
const READY_POLL_INTERVAL_MS = 200;

/** @type {import('node:child_process').ChildProcess | null} */
let backendChild = null;

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
export function spawnBackend({ python, backendDir, port, bridgePort, version, shellPath }) {
  // --path 语义为「兜底默认工作区」，桌面首启由用户在引导流程中自行创建
  const defaultWs = os.homedir() || path.join(backendDir, 'project');

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
  // 桌面版数据根：固定独立目录，绝不与任何 server 实例（8091 等）共享
  env.ASTRION_DATA_ROOT =
    process.env.ASTRION_DESKTOP_DATA_ROOT || path.join(os.homedir(), '.astrion', 'astrion-desktop');
  // 桌面应用身份与控制桥地址：后端据此判定「自己是桌面壳内嵌实例」并代理更新接口
  env.ASTRION_DESKTOP_VERSION = version;
  if (bridgePort) env.ASTRION_DESKTOP_BRIDGE_PORT = String(bridgePort);

  const child = spawn(
    python,
    ['-m', 'server.app', '--port', String(port), '--path', defaultWs],
    {
      cwd: backendDir,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    }
  );

  // 后端 stdout/stderr 转发（dev 终端可见；release 无控制台，后端日志走自身 logs/ 文件）
  child.stdout?.on('data', (chunk) => {
    process.stdout.write(`[astrion-backend] ${chunk}`);
  });
  child.stderr?.on('data', (chunk) => {
    process.stderr.write(`[astrion-backend] ${chunk}`);
  });
  child.on('exit', (code, signal) => {
    console.error(`[astrion-desktop] 后端进程退出: code=${code} signal=${signal}`);
    if (backendChild === child) backendChild = null;
  });

  backendChild = child;
  return child;
}

/** 轮询无鉴权端点直到后端就绪。 */
export function waitBackendReady(port) {
  const deadline = Date.now() + BACKEND_READY_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const poll = () => {
      const req = http.get(
        { host: '127.0.0.1', port, path: '/api/host-mode-enabled', timeout: 800 },
        (res) => {
          res.resume();
          if (res.statusCode === 200 || res.statusCode === 204) {
            resolve();
          } else {
            scheduleNext();
          }
        }
      );
      req.on('timeout', () => {
        req.destroy();
        scheduleNext();
      });
      req.on('error', scheduleNext);
    };
    const scheduleNext = () => {
      if (Date.now() > deadline) {
        reject(new Error(`后端 ${BACKEND_READY_TIMEOUT_MS / 1000}s 内未就绪（127.0.0.1:${port}）`));
      } else {
        setTimeout(poll, READY_POLL_INTERVAL_MS);
      }
    };
    poll();
  });
}

/** 退出时 kill 后端子进程（SIGKILL 兜底，防止孤儿进程残留）。 */
export function shutdownBackend() {
  if (backendChild && !backendChild.killed) {
    try {
      backendChild.kill('SIGKILL');
    } catch {
      /* 进程可能已退出 */
    }
  }
  backendChild = null;
}

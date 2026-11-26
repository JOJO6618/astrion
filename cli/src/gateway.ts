// Gateway 客户端：CLI ↔ Astrion Gateway（host Bearer 通道，docs/runtime_protocol.md §6）
// - token：~/.astrion/astrion/host/data/host_api_token（0600，服务端 host 模式惰性生成）
// - 认证：Authorization: Bearer <token>；仅 host 模式 + 回环地址生效
// - 工作区：X-Astrion-Workspace-Id 头绑定（CLI 语义 = 启动目录即工作区，server/gateway_auth.py 解析）
// 不依赖 Web 会话（Cookie/CSRF/host-login 全部不走）。
import { homedir } from 'node:os';
import { readFile } from 'node:fs/promises';
import { closeSync, existsSync, lstatSync, openSync, mkdirSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import { basename, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';
import { createConnection } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import type { ToolApprovalItem } from './approval';

const DEFAULT_PORT = 8091;
const RUST_PORT = 8092;
const REPO_ROOT = fileURLToPath(new URL('../../', import.meta.url));
const DEFAULT_DATA_ROOT = resolve(homedir(), '.astrion/astrion');

export function cliExperiment(): boolean {
  return cliBackend() === 'rust' && process.env.ASTRION_RUST_EXPERIMENT === '1';
}

function cliBackend(): string {
  if (process.env.ASTRION_CLI_BACKEND) return process.env.ASTRION_CLI_BACKEND;
  return process.env.ASTRION_API_BASE || process.env.ASTRION_HOST_TOKEN_FILE ? 'python' : 'rust';
}

export function cliConfigRoot(): string | null {
  if (cliExperiment()) {
    const root = process.env.ASTRION_RUST_DATA_ROOT;
    return root && isAbsolute(root) ? root : null;
  }
  if (process.env.ASTRION_HOST_TOKEN_FILE) return null;
  return configuredPath('ASTRION_DATA_ROOT') || DEFAULT_DATA_ROOT;
}

function configuredPath(name: string): string | undefined {
  const value = process.env[name]?.trim();
  if (!value) return;
  if (value === '~') return homedir();
  if (/^~[\\/]/.test(value)) return resolve(homedir(), value.slice(2));
  return resolve(REPO_ROOT, value);
}

export function cliDataDir(): string | null {
  const root = cliConfigRoot();
  if (!root) return null;
  return (!cliExperiment() && configuredPath('DATA_DIR')) || resolve(root, 'host/data');
}

export function cliDeployDir(): string | null {
  const root = cliConfigRoot();
  if (!root) return null;
  return (!cliExperiment() && configuredPath('DEPLOY_CONFIG_DIR')) || resolve(root, 'config');
}

function rustExperimentRoot(): string {
  const configured = cliConfigRoot();
  if (!configured) throw new GatewayError('ASTRION_RUST_DATA_ROOT must be an absolute path to a dedicated directory');
  const root = realpathSync(configured);
  if ([DEFAULT_DATA_ROOT, resolve(homedir(), '.astrion/astrion-desktop')].some(production => containsPath(production, root))) {
    throw new GatewayError('ASTRION_RUST_DATA_ROOT must be outside the production data directories');
  }
  const marker = resolve(root, '.astrion-rust-data');
  if (!existsSync(marker)) {
    if (readdirSync(root).some(name => name !== basename(marker))) throw new GatewayError('ASTRION_RUST_DATA_ROOT must be initially empty');
    try { writeFileSync(marker, '', { flag: 'wx', mode: 0o600 }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error; }
  }
  if (!lstatSync(marker).isFile()) throw new GatewayError('Invalid Rust experiment marker');
  for (const name of ['host', 'host/data', 'host/logs', 'config']) {
    const directory = resolve(root, name);
    if (existsSync(directory) && !lstatSync(directory).isDirectory()) {
      throw new GatewayError(`Invalid Rust experiment directory: ${directory}`);
    }
  }
  return root;
}

function containsPath(root: string, candidate: string): boolean {
  const part = relative(root, candidate);
  return part === '' || (!isAbsolute(part) && part !== '..' && !part.startsWith(`..${sep}`));
}

function rustResourceRoot(dataRoot: string): string {
  const configured = process.env.ASTRION_RUST_RESOURCE_ROOT || resolve(REPO_ROOT, 'desktop-electron/.cache/rust-experiment/resources');
  if (!isAbsolute(configured) || !existsSync(resolve(configured, 'config/providers_catalog.json'))) {
    throw new GatewayError('Prepare the Rust resources with node desktop/scripts/prepare_rust_backend.mjs, or set ASTRION_RUST_RESOURCE_ROOT');
  }
  const root = realpathSync(configured);
  if (containsPath(dataRoot, root) || existsSync(resolve(root, 'config/custom_models.json'))) {
    throw new GatewayError('Use staged Rust product resources, separate from experiment data');
  }
  return root;
}

function portIsUnbound(port: number): Promise<boolean> {
  return new Promise(resolvePort => {
    const socket = createConnection({ host: '127.0.0.1', port, timeout: 800 });
    socket.once('connect', () => { socket.destroy(); resolvePort(false); });
    socket.once('timeout', () => { socket.destroy(); resolvePort(false); });
    socket.once('error', (error: NodeJS.ErrnoException) => resolvePort(error.code === 'ECONNREFUSED'));
  });
}

/** 探测能跑后端的 python 解释器：依次试候选，能 import 服务端关键依赖（yaml/flask）才用；
 *  全部失败时退回第一个存在的候选（让启动日志留下真实报错，而不是 CLI 侧静默）。 */
function pickPython(repoRoot: string): string {
  const candidates = [
    resolve(repoRoot, '.venv/bin/python'),
    '/opt/homebrew/bin/python3.12',
    '/opt/homebrew/bin/python3.11',
    '/usr/local/bin/python3.12',
    '/usr/local/bin/python3.11',
    'python3',
  ];
  let firstExisting = '';
  for (const bin of candidates) {
    if (bin.startsWith('/') && !existsSync(bin)) continue;
    if (!firstExisting) firstExisting = bin;
    try {
      execFileSync(bin, ['-c', 'import yaml, flask'], { stdio: 'ignore' });
      return bin;
    } catch {
      // 缺依赖，试下一个
    }
  }
  return firstExisting || 'python3';
}

export interface WorkspaceItem {
  workspace_id: string;
  label: string;
  path: string;
  is_current?: boolean;
}

export interface TaskPollResult {
  task_id: string;
  status: string;
  error?: string;
  conversation_id?: string;
  events: Array<{ idx: number; type: string; data: any; ts?: number }>;
  next_offset: number;
  /** 事件窗口当前最小 idx：offset 落后于此值说明中间事件已被裁剪（协议 §5.2） */
  window_start?: number;
}

export class GatewayError extends Error {}

export class GatewayClient {
  readonly baseUrl: string;
  private token = '';
  private tokenPath: string | null;
  private readonly backend = cliBackend();
  private readonly port: number;
  private readonly explicitToken = process.env.ASTRION_HOST_TOKEN_FILE;
  /** cwd 绑定的工作区 id（boot 确定后写入，后续请求自动携带） */
  workspaceId = '';

  constructor() {
    const rust = this.backend === 'rust';
    const defaultPort = cliExperiment() ? RUST_PORT : process.env.WEB_SERVER_PORT || DEFAULT_PORT;
    this.port = Number(process.env.ASTRION_API_PORT || defaultPort);
    this.baseUrl = (!rust && process.env.ASTRION_API_BASE) || `http://127.0.0.1:${this.port}`;
    this.tokenPath = rust ? null : (this.explicitToken || resolve(cliDataDir() || resolve(DEFAULT_DATA_ROOT, 'host/data'), 'host_api_token'));
  }

  /** 读取 host Bearer token；文件不存在 = 服务未启动或非 host 模式 */
  async readToken(): Promise<string> {
    try {
      this.token = this.tokenPath ? (await readFile(this.tokenPath, 'utf8')).trim() : '';
    } catch {
      this.token = '';
    }
    return this.token;
  }

  get ready(): boolean {
    return this.token.length > 0;
  }

  /**
   * 确保 Gateway 可用：已运行则直连；未运行则 CLI 自启动后端服务（用户拍板的交互：
   * CLI 不与 Web 端启动绑定）。服务在运行但 token 始终不存在 = 旧版本服务（不支持
   * CLI 接入），抛 stale_server 提示重启；自启动超时抛 start_timeout。
   * 注意边界：仅在「探测无服务」时 spawn 新实例，绝不触碰已在运行的进程。
   */
  async ensureRunning(cwd: string, onPhase?: (phase: 'connecting' | 'starting') => void): Promise<void> {
    onPhase?.('connecting');
    if (this.backend === 'rust') return this.ensureRustRunning(cwd, onPhase);
    if (this.backend !== 'python') throw new GatewayError('ASTRION_CLI_BACKEND must be python or rust');
    if (await this.readToken()) {
      try {
        await this.listWorkspaces();
        return;
      } catch {
        // token 失效或服务未就绪，进入后续流程
      }
    }
    const alive = await this.probeAlive();
    if (!alive) {
      // An explicitly paired instance owns its credential and lifecycle.
      if (this.explicitToken || process.env.ASTRION_API_BASE)
        throw new GatewayError(`connect_failed: ${this.baseUrl}`);
      onPhase?.('starting');
      this.spawnServer(cwd);
    }
    const deadline = Date.now() + (alive ? 4_000 : 30_000);
    while (Date.now() < deadline) {
      if (await this.readToken()) {
        try {
          await this.listWorkspaces();
          return;
        } catch {
          // token 已就绪但服务尚在初始化，继续等
        }
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new GatewayError(alive ? 'stale_server' : 'start_timeout');
  }

  private async probeRust(starting = false): Promise<number | null> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}/api/host-mode-enabled`, {
        redirect: 'manual', signal: AbortSignal.timeout(800),
      });
    } catch {
      if (starting || await portIsUnbound(this.port)) return null;
      throw new GatewayError(`Cannot verify Gateway at ${this.baseUrl}`);
    }
    const info = await response.json().catch(() => null);
    if (response.ok && info?.backend === 'rust' && Number.isSafeInteger(info.pid) && info.pid > 0) return info.pid;
    throw new GatewayError(`Another service is listening at ${this.baseUrl}`);
  }

  private async connectRust(): Promise<void> {
    if (!await this.readToken()) throw new GatewayError('Rust Gateway token is missing from the selected data directory');
    const catalog = await this.listWorkspaces();
    if (!Array.isArray(catalog?.workspaces)) throw new GatewayError('Invalid Rust Gateway workspace list');
  }

  private async ensureRustRunning(cwd: string, onPhase?: (phase: 'starting') => void): Promise<void> {
    if (process.env.ASTRION_API_BASE || this.explicitToken) {
      throw new GatewayError('Use ASTRION_API_PORT for self-started Rust, or unset ASTRION_CLI_BACKEND and pair an existing server with ASTRION_API_BASE and ASTRION_HOST_TOKEN_FILE');
    }
    if (!Number.isInteger(this.port) || this.port < 1024 || this.port > 65535) throw new GatewayError('Invalid ASTRION_API_PORT');
    const experimental = cliExperiment();
    const root = experimental ? rustExperimentRoot() : cliConfigRoot()!;
    const dataDir = experimental ? resolve(root, 'host/data') : cliDataDir()!;
    this.tokenPath = resolve(dataDir, 'host_api_token');
    if (await this.probeRust() !== null) return this.connectRust();

    const endpoint = process.env.ASTRION_RUST_MODEL_ENDPOINT;
    const model = process.env.ASTRION_RUST_MODEL;
    if (Boolean(endpoint) !== Boolean(model)) throw new GatewayError('Set ASTRION_RUST_MODEL_ENDPOINT and ASTRION_RUST_MODEL together');
    const executable = process.platform === 'win32' ? 'astrion-headless.exe' : 'astrion-headless';
    const binary = process.env.ASTRION_RUST_BACKEND_BIN ||
      ['release', 'debug'].map(profile => resolve(REPO_ROOT, 'backend-rust/target', profile, executable))
        .find(existsSync);
    if (!binary || !isAbsolute(binary) || !existsSync(binary))
      throw new GatewayError('Build the Rust backend with cargo build --release, or set ASTRION_RUST_BACKEND_BIN to its absolute path');
    const resourceRoot = rustResourceRoot(root);
    const logDir = (experimental ? undefined : configuredPath('LOGS_DIR')) || resolve(root, 'host/logs');
    const deployDir = experimental ? resolve(root, 'config') : cliDeployDir()!;
    const registry = (experimental ? undefined : configuredPath('HOST_WORKSPACES_FILE')) || resolve(deployDir, 'host_workspaces.json');
    const logPath = resolve(logDir, 'cli_spawned_server.log');
    const args = ['--workspace', cwd, '--data-dir', dataDir, '--port', String(this.port)];
    if (endpoint && model) args.push('--endpoint', endpoint, '--model', model);
    const env = { ...process.env };
    if (experimental) {
      for (const key of Object.keys(env)) if (/^(ASTRION_|AGENT_|WEB_)/i.test(key)) delete env[key];
      Object.assign(env, {
        HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root,
        XDG_CONFIG_HOME: root, XDG_DATA_HOME: root, XDG_CACHE_HOME: root,
      });
    }
    delete env.ASTRION_DESKTOP_VERSION;
    delete env.ASTRION_DESKTOP_STOP_STDIN;
    Object.assign(env, {
      ASTRION_DATA_ROOT: root, ASTRION_RESOURCE_ROOT: resourceRoot, ASTRION_IGNORE_DOTENV: '1',
      DEPLOY_CONFIG_DIR: deployDir, LOGS_DIR: logDir, HOST_WORKSPACES_FILE: registry,
      ...(process.env.ASTRION_MODEL_API_KEY && { ASTRION_MODEL_API_KEY: process.env.ASTRION_MODEL_API_KEY }),
    });
    onPhase?.('starting');
    mkdirSync(logDir, { recursive: true, mode: 0o700 });
    const log = openSync(logPath, 'a', 0o600);
    const child = (() => {
      try {
        return spawn(binary, args, { cwd, env, detached: true, stdio: ['ignore', log, log], windowsHide: true });
      } finally {
        closeSync(log);
      }
    })();
    let spawnError: Error | undefined;
    child.once('error', error => { spawnError = error; });
    try {
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        if (spawnError || child.exitCode !== null || child.signalCode !== null) {
          throw new GatewayError(`Rust Gateway exited before readiness. See ${logPath}`, { cause: spawnError });
        }
        const pid = await this.probeRust(true);
        if (pid !== null) {
          if (pid !== child.pid) throw new GatewayError(`Another Rust process acquired ${this.baseUrl}`);
          await this.connectRust();
          child.unref();
          return;
        }
        await delay(100);
      }
      throw new GatewayError(`Rust Gateway startup timed out. See ${logPath}`);
    } catch (error) {
      child.kill('SIGKILL');
      throw error;
    }
  }

  /** 探测服务是否在运行：任何 HTTP 响应（含 401）都算在跑；连接拒绝才算不在跑 */
  private async probeAlive(): Promise<boolean> {
    try {
      await fetch(`${this.baseUrl}/api/host/workspaces`, { method: 'GET', signal: AbortSignal.timeout(800) });
      return true;
    } catch {
      return false;
    }
  }

  /** CLI 自启动后端（detached + unref；走 headless 入口，只挂 Gateway/任务/审批等运行时蓝图，
   *  不拉起 web 站点路由面；显式 --port 避免默认端口歧义）
   *  python 解释器做依赖探测（import yaml/flask 通过才用）——本机 .venv 缺 yaml 会启动即崩；
   *  输出落日志文件便于诊断启动失败。 */
  private spawnServer(cwd: string): void {
    // cli/src → 项目根；fileURLToPath 正确解码中文路径（new URL().pathname 会留下
    // 百分号编码，导致 existsSync 永远 false、spawn cwd 无效——中文路径必用此函数）
    const repoRoot = REPO_ROOT;
    const port = this.port;
    const pythonBin = pickPython(repoRoot);
    const logDir = configuredPath('LOGS_DIR') || resolve(cliConfigRoot() || DEFAULT_DATA_ROOT, 'host/logs');
    try {
      mkdirSync(logDir, { recursive: true });
      const logFd = openSync(resolve(logDir, 'cli_spawned_server.log'), 'a');
      const child = spawn(
        pythonBin,
        ['-m', 'server.headless_app', '--path', cwd, '--port', String(port), '--thinking-mode'],
        {
          cwd: repoRoot,
          env: {
            ...process.env,
            TERMINAL_SANDBOX_MODE: 'host',
            HOST_PROJECT_PATH: cwd,
            WEB_SERVER_PORT: String(port),
            AGENTS_CLI_STARTED_SERVER: '1',
          },
          detached: true,
          stdio: ['ignore', logFd, logFd],
        },
      );
      child.on('error', () => {}); // spawn 失败由后续轮询超时统一报错
      child.unref();
    } catch {
      // 同上
    }
  }

  // ── 工作区 ──
  async listWorkspaces(): Promise<{ workspaces: WorkspaceItem[]; current_workspace_id: string }> {
    const res = await this.request<{ data: any }>('/api/host/workspaces', { method: 'GET' });
    return res.data;
  }

  async createWorkspace(path: string, label = basename(path)): Promise<{ workspace: WorkspaceItem }> {
    const res = await this.request<{ data: any }>('/api/host/workspaces/create', {
      method: 'POST',
      body: { path, label },
    });
    return res.data;
  }

  // ── 会话（Gateway 公共协议面 /api/runtime/sessions；响应为顶层展开风格，无 data 包装） ──
  async createSession(): Promise<{ conversation_id: string }> {
    const res = await this.request<any>('/api/runtime/sessions', { method: 'POST', body: {} });
    const conversationId = res.data?.conversation_id ?? res.conversation_id;
    if (!conversationId) throw new GatewayError(`create_session_bad_payload: ${JSON.stringify(res).slice(0, 200)}`);
    return { conversation_id: conversationId };
  }

  async listSessions(limit = 20): Promise<any[]> {
    const res = await this.request<any>(`/api/runtime/sessions?limit=${limit}`, { method: 'GET' });
    // 顶层展开风格的实际键是 conversations（8093 实测），兼容 sessions/items 包装
    return res.data?.sessions ?? res.sessions ?? res.conversations ?? res.data?.items ?? res.items ?? [];
  }

  async getSessionHistory(conversationId: string): Promise<any> {
    const res = await this.request<any>(`/api/runtime/sessions/${encodeURIComponent(conversationId)}/history`, {
      method: 'GET',
    });
    return res.data ?? res.conversation ?? res;
  }

  /** 会话 token 统计（/context 面板；顶层展开风格 res.stats） */
  async getTokenStats(conversationId: string): Promise<any> {
    const res = await this.request<any>(`/api/runtime/sessions/${encodeURIComponent(conversationId)}/token-stats`, {
      method: 'GET',
    });
    return res.stats ?? res.data?.stats ?? res.data ?? {};
  }

  // ── host 全局设置（写操作；端点为 chat_bp 共享视图函数，装饰器双通道化后 Bearer 可达） ──

  /** 保存个性化默认（patch 语义：服务端 sanitize fallback=existing，未传字段不动） */
  async savePersonalization(patch: Record<string, unknown>): Promise<void> {
    await this.request<any>('/api/personalization', { method: 'POST', body: patch });
  }

  /** 路径授权全量保存（端点语义为两组全量提交；返回服务端落定后的两组列表） */
  async savePathAuths(writable: string[], readableExtra: string[]): Promise<{ writable: string[]; readableExtra: string[] }> {
    const res = await this.request<any>('/api/path-authorization', {
      method: 'POST',
      body: { writable_paths: writable, readable_extra_paths: readableExtra },
    });
    return {
      writable: res.writable_paths ?? res.data?.writable_paths ?? writable,
      readableExtra: res.readable_extra_paths ?? res.data?.readable_extra_paths ?? readableExtra,
    };
  }

  /** 子智能体列表（/agents 面板；conversation_id 经 query 显式指定） */
  async listSubAgents(conversationId: string): Promise<any[]> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const res = await this.request<any>(`/api/sub_agents${q}`, { method: 'GET' });
    return res.data ?? [];
  }

  /** 后台指令列表（/tasks 面板；conversation_id 经 query 显式指定） */
  async listBackgroundCommands(conversationId: string): Promise<any[]> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const res = await this.request<any>(`/api/background_commands${q}`, { method: 'GET' });
    return res.data ?? [];
  }

  /** 工作流库列表（/workflow 面板；注意该端点顶层展开为 {workflows}，无 success 包装） */
  async listWorkflows(): Promise<any[]> {
    const res = await this.request<any>('/api/workflows', { method: 'GET' });
    return res.workflows ?? res.data?.workflows ?? [];
  }

  /** 版本回溯检查点（/rewind 面板；返回 data.items，含 enabled 标记） */
  async listCheckpoints(conversationId: string): Promise<{ items: any[]; enabled: boolean }> {
    const res = await this.request<any>(
      `/api/conversations/${encodeURIComponent(conversationId)}/versioning/checkpoints`,
      { method: 'GET' },
    );
    const data = res.data ?? {};
    return { items: Array.isArray(data.items) ? data.items : [], enabled: data.enabled !== false };
  }

  // ── Run（任务） ──
  async createTask(payload: {
    message: string;
    conversation_id?: string;
    /** 会话级覆盖（/model 面板生效值）；不传则服务端用对话/个性化默认 */
    model_key?: string;
    run_mode?: string;
    thinking_mode?: boolean;
  }): Promise<{ task_id: string; conversation_id?: string }> {
    const res = await this.request<{ data: any }>('/api/tasks', { method: 'POST', body: payload });
    return res.data;
  }

  async pollTask(taskId: string, offset: number): Promise<TaskPollResult> {
    const res = await this.request<{ data: TaskPollResult }>(`/api/tasks/${encodeURIComponent(taskId)}?from=${offset}`, {
      method: 'GET',
    });
    return res.data;
  }

  async cancelTask(taskId: string): Promise<void> {
    await this.request(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST', body: {} });
  }

  async sendGuidance(taskId: string, message: string): Promise<void> {
    await this.request(`/api/tasks/${encodeURIComponent(taskId)}/runtime_guidance`, {
      method: 'POST',
      body: { message },
    });
  }

  // ── 审批 ──
  async decideApproval(approvalId: string, decision: 'approved' | 'rejected'): Promise<ToolApprovalItem> {
    const response = await this.request<{ success: boolean; item: ToolApprovalItem }>(
      `/api/tool-approvals/${encodeURIComponent(approvalId)}/decision`, {
        method: 'POST',
        body: { decision },
      },
    );
    return response.item;
  }

  private async request<T = any>(path: string, options: { method: string; body?: any; _retried?: boolean }): Promise<T> {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      Authorization: `Bearer ${this.token}`,
    };
    if (this.workspaceId) headers['X-Astrion-Workspace-Id'] = this.workspaceId;
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: options.method,
        headers,
        ...(this.backend === 'rust' && { redirect: 'error' }),
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
    } catch (err) {
      throw new GatewayError(`connect_failed: ${String(err)}`);
    }

    const text = await response.text();
    // 401 自愈：token 可能刚首次生成（服务端轮）或被轮换，重读后重试一次
    if (response.status === 401 && !options._retried) {
      await this.readToken();
      if (this.token) {
        return this.request(path, { ...options, _retried: true });
      }
    }
    let data: any = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { success: false, error: text };
      }
    }
    if (!response.ok || data?.success === false) {
      throw new GatewayError(data?.error || data?.message || `HTTP ${response.status}`);
    }
    return data as T;
  }
}

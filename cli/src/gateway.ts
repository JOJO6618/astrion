// Gateway 客户端：CLI ↔ Astrion Gateway（host Bearer 通道，docs/runtime_protocol.md §6）
// - token：~/.astrion/astrion/host/data/host_api_token（0600，服务端 host 模式惰性生成）
// - 认证：Authorization: Bearer <token>；仅 host 模式 + 回环地址生效
// - 工作区：X-Astrion-Workspace-Id 头绑定（CLI 语义 = 启动目录即工作区，server/gateway_auth.py 解析）
// 不依赖 Web 会话（Cookie/CSRF/host-login 全部不走）。
import { homedir } from 'node:os';
import { readFile, mkdir } from 'node:fs/promises';
import { existsSync, openSync, mkdirSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFileSync } from 'node:child_process';

const DEFAULT_PORT = 8091;
const TOKEN_PATH = `${homedir()}/.astrion/astrion/host/data/host_api_token`;

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
  /** cwd 绑定的工作区 id（boot 确定后写入，后续请求自动携带） */
  workspaceId = '';

  constructor() {
    const port = Number(process.env.ASTRION_API_PORT || process.env.WEB_SERVER_PORT || DEFAULT_PORT);
    this.baseUrl = process.env.ASTRION_API_BASE || `http://127.0.0.1:${port}`;
  }

  /** 读取 host Bearer token；文件不存在 = 服务未启动或非 host 模式 */
  async readToken(): Promise<string> {
    try {
      this.token = (await readFile(TOKEN_PATH, 'utf8')).trim();
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

  /** 探测服务是否在运行：任何 HTTP 响应（含 401）都算在跑；连接拒绝才算不在跑 */
  private async probeAlive(): Promise<boolean> {
    try {
      await fetch(`${this.baseUrl}/api/host/workspaces`, { method: 'GET' });
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
    const repoRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
    const port = Number(process.env.ASTRION_API_PORT || process.env.WEB_SERVER_PORT || DEFAULT_PORT);
    const pythonBin = pickPython(repoRoot);
    const logDir = `${homedir()}/.astrion/astrion/host/logs`;
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
    return res.data?.sessions ?? res.sessions ?? res.data?.items ?? res.items ?? [];
  }

  async getSessionHistory(conversationId: string): Promise<any> {
    const res = await this.request<any>(`/api/runtime/sessions/${encodeURIComponent(conversationId)}/history`, {
      method: 'GET',
    });
    return res.data ?? res.conversation ?? res;
  }

  // ── Run（任务） ──
  async createTask(payload: { message: string; conversation_id?: string }): Promise<{ task_id: string; conversation_id?: string }> {
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
  async decideApproval(approvalId: string, decision: 'approved' | 'rejected'): Promise<void> {
    await this.request(`/api/tool-approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: 'POST',
      body: { decision },
    });
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

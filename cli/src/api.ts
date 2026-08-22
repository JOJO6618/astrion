import { spawn, type ChildProcess } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { basename } from 'node:path';
import type { ExecutionMode, ModelDefinition, PermissionMode, RunMode, Workspace } from './types.js';

export type ApiConfig = {
  baseUrl: string;
  repoRoot: string;
  cwd: string;
  port: number;
};

export type TaskPollResult = {
  task_id: string;
  status: string;
  error?: string;
  conversation_id?: string;
  events: Array<{ idx: number; type: string; data: any; ts?: number }>;
  next_offset: number;
};

export class ApiClient {
  private cookie = '';
  private csrfToken = '';
  private serverProcess: ChildProcess | null = null;

  constructor(private readonly config: ApiConfig) {}

  get baseUrl(): string {
    return this.config.baseUrl;
  }

  async ensureConnected(): Promise<void> {
    const ok = await this.tryFetchCsrf();
    if (!ok) {
      this.startServer();
      await this.waitForServer();
      await this.fetchCsrf();
    }
    await this.hostLogin();
    await this.fetchCsrf();
  }

  private startServer(): void {
    if (this.serverProcess) return;
    const env = {
      ...process.env,
      TERMINAL_SANDBOX_MODE: 'host',
      HOST_PROJECT_PATH: this.config.cwd,
      WEB_SERVER_PORT: String(this.config.port),
      AGENTS_CLI_STARTED_SERVER: '1',
    };
    this.serverProcess = spawn(
      process.execPath.includes('node') ? 'python3' : 'python3',
      ['-m', 'server.app', '--path', this.config.cwd, '--port', String(this.config.port), '--thinking-mode'],
      {
        cwd: this.config.repoRoot,
        env,
        detached: true,
        stdio: 'ignore',
      },
    );
    this.serverProcess.unref();
  }

  private async waitForServer(): Promise<void> {
    const deadline = Date.now() + 20_000;
    let lastError: unknown;
    while (Date.now() < deadline) {
      const ok = await this.tryFetchCsrf().catch((err) => {
        lastError = err;
        return false;
      });
      if (ok) return;
      await sleep(500);
    }
    throw new Error(`无法连接本地 Web 服务：${String(lastError || 'timeout')}`);
  }

  private async tryFetchCsrf(): Promise<boolean> {
    try {
      await this.fetchCsrf();
      return true;
    } catch {
      return false;
    }
  }

  async fetchCsrf(): Promise<string> {
    const data = await this.request<{ token: string }>('/api/csrf-token', { method: 'GET', skipCsrf: true, allowUnauthorized: true });
    this.csrfToken = data.token;
    return this.csrfToken;
  }

  async hostLogin(): Promise<void> {
    await this.request('/host-login', { method: 'POST' });
  }

  async getStatus(): Promise<any> {
    return this.request('/api/status', { method: 'GET' });
  }

  async listModels(): Promise<ModelDefinition[]> {
    const res = await this.request<{ items?: ModelDefinition[]; data?: any }>('/api/v1/models', { method: 'GET' });
    return res.items || res.data?.items || [];
  }

  async getPersonalization(): Promise<any> {
    return this.request('/api/personalization', { method: 'GET' });
  }

  async listWorkspaces(): Promise<{ current_workspace_id: string; default_workspace_id: string; workspaces: Workspace[] }> {
    const res = await this.request<{ data: any }>('/api/host/workspaces', { method: 'GET' });
    return res.data;
  }

  async createWorkspace(path: string, label = basename(path)): Promise<{ workspace: Workspace; created: boolean }> {
    const res = await this.request<{ data: any }>('/api/host/workspaces/create', {
      method: 'POST',
      body: { path, label },
    });
    return res.data;
  }

  async selectWorkspace(workspaceId: string): Promise<void> {
    await this.request('/api/host/workspaces/select', {
      method: 'POST',
      body: { workspace_id: workspaceId },
    });
  }

  async createConversation(): Promise<any> {
    return this.request('/api/conversations', { method: 'POST', body: {} });
  }

  async listConversations(limit = 20): Promise<any[]> {
    const res = await this.request<{ data: any }>(`/api/conversations?limit=${limit}`, { method: 'GET' });
    return res.data?.conversations || res.data || [];
  }

  async loadConversation(conversationId: string): Promise<any> {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/load`, { method: 'PUT', body: {} });
  }

  async getCurrentConversation(): Promise<any> {
    const res = await this.request<{ data: any }>('/api/conversations/current', { method: 'GET' });
    return res.data;
  }

  async compressConversation(conversationId: string): Promise<any> {
    return this.request(`/api/conversations/${encodeURIComponent(conversationId)}/compress`, { method: 'POST', body: {} });
  }

  async setModel(modelKey: string): Promise<any> {
    return this.request('/api/model', { method: 'POST', body: { model_key: modelKey } });
  }

  async setRunMode(mode: RunMode): Promise<any> {
    return this.request('/api/thinking-mode', { method: 'POST', body: { mode } });
  }

  async getPermissionMode(): Promise<any> {
    return this.request('/api/permission-mode', { method: 'GET' });
  }

  async setPermissionMode(mode: PermissionMode): Promise<any> {
    return this.request('/api/permission-mode', { method: 'POST', body: { mode } });
  }

  async getExecutionMode(): Promise<any> {
    return this.request('/api/execution-mode', { method: 'GET' });
  }

  async getConversationVersioning(conversationId: string): Promise<any> {
    const res = await this.request<{ data: any }>(`/api/conversations/${encodeURIComponent(conversationId)}/versioning`, { method: 'GET' });
    return res.data;
  }

  async setConversationVersioning(conversationId: string, enabled: boolean, trackingMode?: string): Promise<any> {
    const res = await this.request<{ data: any }>(`/api/conversations/${encodeURIComponent(conversationId)}/versioning`, {
      method: 'POST',
      body: { enabled, ...(trackingMode ? { tracking_mode: trackingMode } : {}) },
    });
    return res.data;
  }

  async getConversationTokens(conversationId: string): Promise<any> {
    const res = await this.request<{ data: any }>(`/api/conversations/${encodeURIComponent(conversationId)}/tokens`, { method: 'GET' });
    return res.data;
  }

  async listSubAgents(): Promise<any[]> {
    const res = await this.request<{ data: any[] }>('/api/sub_agents', { method: 'GET' });
    return Array.isArray(res.data) ? res.data : [];
  }

  async listBackgroundCommands(): Promise<any[]> {
    const res = await this.request<{ data: any[] }>('/api/background_commands', { method: 'GET' });
    return Array.isArray(res.data) ? res.data : [];
  }

  async setExecutionMode(mode: ExecutionMode): Promise<any> {
    return this.request('/api/execution-mode', { method: 'POST', body: { mode } });
  }

  async getToolSettings(): Promise<any> {
    return this.request('/api/tool-settings', { method: 'GET' });
  }

  async setToolCategory(category: string, enabled: boolean): Promise<any> {
    return this.request('/api/tool-settings', { method: 'POST', body: { category, enabled } });
  }

  async getPathAuthorization(): Promise<any> {
    return this.request('/api/path-authorization', { method: 'GET' });
  }

  async setPathAuthorization(writablePaths: string[], readableExtraPaths: string[]): Promise<any> {
    return this.request('/api/path-authorization', {
      method: 'POST',
      body: { writable_paths: writablePaths, readable_extra_paths: readableExtraPaths },
    });
  }

  async createTask(payload: { message: string; conversation_id?: string; model_key?: string; run_mode?: RunMode }): Promise<any> {
    const res = await this.request<{ data: any }>('/api/tasks', {
      method: 'POST',
      body: payload,
    });
    return res.data;
  }

  async pollTask(taskId: string, offset: number): Promise<TaskPollResult> {
    const res = await this.request<{ data: TaskPollResult }>(`/api/tasks/${encodeURIComponent(taskId)}?from=${offset}`, { method: 'GET' });
    return res.data;
  }

  async cancelTask(taskId: string): Promise<void> {
    await this.request(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST', body: {} });
  }

  async decideApproval(approvalId: string, decision: 'approved' | 'rejected'): Promise<any> {
    return this.request(`/api/tool-approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: 'POST',
      body: { decision },
    });
  }

  private async request<T = any>(path: string, options: { method: string; body?: any; skipCsrf?: boolean; allowUnauthorized?: boolean }): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (this.cookie) headers.Cookie = this.cookie;
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (!options.skipCsrf && options.method !== 'GET' && this.csrfToken) headers['X-CSRF-Token'] = this.csrfToken;

    const response = await fetch(`${this.config.baseUrl}${path}`, {
      method: options.method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      redirect: 'manual',
    });
    this.captureCookies(response);

    if (response.status === 401 && !options.allowUnauthorized) {
      await this.fetchCsrf();
      await this.hostLogin();
      return this.request(path, { ...options, allowUnauthorized: true });
    }

    const text = await response.text();
    let data: any = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { success: false, error: text };
      }
    }
    if (!response.ok || data?.success === false) {
      throw new Error(data?.error || data?.message || `HTTP ${response.status}`);
    }
    return data as T;
  }

  private captureCookies(response: Response): void {
    const anyHeaders = response.headers as any;
    const rawCookies: string[] = typeof anyHeaders.getSetCookie === 'function'
      ? anyHeaders.getSetCookie()
      : [];
    const fallback = response.headers.get('set-cookie');
    if (fallback) rawCookies.push(fallback);
    if (!rawCookies.length) return;

    const current = new Map<string, string>();
    for (const part of this.cookie.split(';')) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const eq = trimmed.indexOf('=');
      if (eq > 0) current.set(trimmed.slice(0, eq), trimmed.slice(eq + 1));
    }
    for (const raw of rawCookies) {
      const cookiePair = raw.split(';', 1)[0];
      const eq = cookiePair.indexOf('=');
      if (eq > 0) current.set(cookiePair.slice(0, eq), cookiePair.slice(eq + 1));
    }
    this.cookie = Array.from(current.entries()).map(([key, value]) => `${key}=${value}`).join('; ');
  }
}

export function createDefaultApiClient(cwd: string, repoRoot: string): ApiClient {
  const port = Number(process.env.AGENTS_API_PORT || process.env.WEB_SERVER_PORT || 8091);
  const baseUrl = process.env.AGENTS_API_BASE || `http://127.0.0.1:${port}`;
  return new ApiClient({ baseUrl, repoRoot, cwd, port });
}

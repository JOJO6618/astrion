import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { resolveRuntime } from '../backend.js';
import { productionRustPaths, resolveDesktopBackend } from '../rust-backend.js';

export class QuickGateway {
  constructor({ port = 8091, dataRoot = path.join(os.homedir(), '.astrion/astrion'),
    dataDir, workspacePath = process.cwd(), managed = false } = {}) {
    this.port = port;
    this.managed = managed;
    this.root = dataRoot;
    this.dataDir = dataDir || productionRustPaths(dataRoot).dataDir;
    this.workspacePath = workspacePath;
    this.base = `http://127.0.0.1:${port}`;
    this.tokenPath = path.join(this.dataDir, 'host_api_token');
  }
  async request(route, method = 'GET', body, workspace = '') {
    if (!allowedRoute(route, method)) throw new Error('Unsupported Gateway request');
    const token = (await fs.readFile(this.tokenPath, 'utf8')).trim();
    const response = await fetch(this.base + route, {
      method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json',
        ...(workspace ? { 'X-Astrion-Workspace-Id': workspace } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(15000)
    });
    const result = await response.json();
    if (!response.ok || result.success === false) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }
  async ensureRunning() {
    let alive = false;
    try { await fetch(`${this.base}/api/host/workspaces`, { signal: AbortSignal.timeout(1500) }); alive = true; } catch {}
    if (!alive) {
      if (this.managed) throw new Error('Desktop backend is unavailable; restart the application.');
      const launch = this.localBackend();
      const logDir = productionRustPaths(this.root).logsDir;
      await fs.mkdir(logDir, { recursive: true });
      const log = await fs.open(path.join(logDir, 'quick-entry-server.log'), 'a');
      const child = spawn(launch.command, launch.args, {
        cwd: launch.cwd, detached: true, stdio: ['ignore', log.fd, log.fd], env: launch.env,
      });
      child.on('error', (error) => { this.startError = error; });
      child.unref();
      await log.close();
    }
    const deadline = Date.now() + (alive ? 5000 : 60000);
    while (Date.now() < deadline) {
      if (this.startError) throw this.startError;
      try { return await this.request('/api/host/workspaces'); } catch {}
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    throw new Error(alive ? 'Gateway is running but its host token is unavailable or incompatible' : 'Headless Gateway did not become ready');
  }

  localBackend() {
    const paths = productionRustPaths(this.root);
    const env = {
      ...process.env, TERMINAL_SANDBOX_MODE: 'host', ASTRION_DATA_ROOT: this.root,
      ASTRION_IGNORE_DOTENV: '1', HOST_PROJECT_PATH: this.workspacePath,
      WEB_SERVER_PORT: String(this.port), DATA_DIR: this.dataDir,
      DEPLOY_CONFIG_DIR: paths.deployDir, LOGS_DIR: paths.logsDir, HOST_WORKSPACES_FILE: paths.registry,
    };
    const rust = resolveDesktopBackend();
    if (!rust) {
      const { python, backendDir } = resolveRuntime();
      return {
        command: python, cwd: backendDir, env,
        args: ['-m', 'server.headless_app', '--port', String(this.port), '--path', this.workspacePath, '--thinking-mode'],
      };
    }
    const args = ['--workspace', this.workspacePath, '--data-dir', this.dataDir, '--port', String(this.port)];
    if (env.ASTRION_RUST_MODEL_ENDPOINT) {
      args.push('--endpoint', env.ASTRION_RUST_MODEL_ENDPOINT, '--model', env.ASTRION_RUST_MODEL);
    }
    env.ASTRION_RESOURCE_ROOT = rust.resourceRoot;
    return { command: rust.binary, args, cwd: this.workspacePath, env };
  }
}

export function allowedRoute(route, method) {
  if (typeof route !== 'string' || !route.startsWith('/api/') || route.includes('..') || route.includes('#')) return false;
  const pathname = route.split('?')[0];
  const patterns = method === 'GET' ? [
    /^\/api\/(host\/workspaces|v1\/models|personalization|tasks|status)$/,
    /^\/api\/runtime\/sessions(?:\/[^/]+\/history)?$/,
    /^\/api\/tasks\/[^/]+$/,
    /^\/api\/conversations\/[^/]+\/running-status$/,
    /^\/api\/(tool-approvals|plan-approvals|user-questions)\/pending$/
  ] : method === 'POST' ? [
    /^\/api\/(tasks|runtime\/sessions|personalization)$/,
    /^\/api\/tasks\/[^/]+\/(cancel|runtime_guidance)$/,
    /^\/api\/tool-approvals\/[^/]+\/decision$/,
    /^\/api\/(plan-approvals|user-questions)\/[^/]+\/answer$/
  ] : [];
  return patterns.some((pattern) => pattern.test(pathname));
}

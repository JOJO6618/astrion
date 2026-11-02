type WorkspaceHost = { currentHostWorkspaceId?: string };
let host: WorkspaceHost | undefined;
let applicationFetch: typeof fetch | undefined;

function dispatch(fetcher: typeof fetch, input: RequestInfo | URL, init?: RequestInit) {
  const original = typeof Request !== 'undefined' && input instanceof Request ? input : null;
  const value = original ? original.url : String(input);
  const url = new URL(value, globalThis.location?.href || 'http://localhost/');
  const workspace = String(host?.currentHostWorkspaceId || '');
  if (workspace && url.origin === globalThis.location?.origin && url.pathname.startsWith('/api/')) {
    const headers = new Headers(init?.headers || original?.headers);
    if (!headers.has('X-Astrion-Workspace-Id')) headers.set('X-Astrion-Workspace-Id', workspace);
    return fetcher(input, { ...init, headers });
  }
  return fetcher(input, init);
}

export function bindWorkspaceRequestHost(value: WorkspaceHost) {
  host = value;
  if (applicationFetch) return;
  // Wrap the existing CSRF transport once. Direct fetch callers and diagnostic
  // aliases then capture the same visible workspace at request dispatch.
  applicationFetch = globalThis.fetch.bind(globalThis);
  const fetcher = applicationFetch;
  globalThis.fetch = (input, init) => dispatch(fetcher, input, init);
}

export function workspaceScopedFetch(input: RequestInfo | URL, init?: RequestInit) {
  return globalThis.fetch(input, init);
}

/** 当前可见工作区（与 fetch 头使用同一来源）。 */
export function currentWorkspaceScopeId(): string {
  return String(host?.currentHostWorkspaceId || '');
}

/**
 * 给同源相对资源地址补 `workspace_id`。
 *
 * `<img>` / `<video>` / `<iframe>` 等浏览器原生请求无法携带
 * `X-Astrion-Workspace-Id` 头，后端只能按 session 选中工作区解析；多窗口或
 * 并发响应下 session 可能与当前可见工作区不一致，导致图片/文件去错目录找文件
 * （表现为「图片加载失败」「文件不存在」）。资源地址因此必须自带工作区身份。
 */
export function withWorkspaceScope(url: string): string {
  if (!url || !url.startsWith('/') || url.startsWith('//')) return url;
  const workspace = currentWorkspaceScopeId();
  if (!workspace) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}workspace_id=${encodeURIComponent(workspace)}`;
}

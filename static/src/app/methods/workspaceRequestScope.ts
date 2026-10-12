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

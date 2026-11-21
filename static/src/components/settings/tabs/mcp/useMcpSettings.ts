import { onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { createSaveQueue } from '@/utils/saveQueue';

export const MCP_ACTORS = ['main', 'sub'] as const;
export type McpActor = (typeof MCP_ACTORS)[number];
export type McpTransport = 'stdio' | 'streamable_http';

export interface McpServerSummary {
  id: string;
  name: string;
  description: string;
  transport: McpTransport;
  enabled: boolean;
  tools_cache_count: number;
  tools_cache_updated_at: string;
  last_error: string;
}

export interface McpServerConfig {
  id: string;
  name: string;
  description: string;
  transport: McpTransport;
  enabled: boolean;
  command: string;
  args: string[];
  cwd: string;
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  timeout_seconds: number;
  include_tools: string[];
  exclude_tools: string[];
}

interface WorkspaceSelection {
  inherit: boolean;
  server_ids: string[];
}

interface McpWorkspace {
  id: string;
  label: string;
  access: Record<McpActor, WorkspaceSelection | null>;
}

interface McpSettings {
  bridge_enabled: boolean;
  servers: McpServerSummary[];
  defaults: Record<McpActor, string[]>;
  workspaces: McpWorkspace[];
  default_workspace_id: string;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/mcp${path}`, options);
  const result = await response.json().catch(() => null);
  if (!response.ok || !result?.success) {
    throw new Error(result?.error || t('settings.mcp.requestFailed'));
  }
  return result.data as T;
}

function jsonRequest(method: string, body: unknown, keepalive = false): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    keepalive
  };
}

export function useMcpSettings() {
  const settings = ref<McpSettings>();
  const workspaceId = ref('');
  const loading = ref(false);
  const loadError = ref('');
  const accessError = ref('');
  const pending = new Map<string, { server_ids: string[]; inherit?: boolean }>();
  const saveQueue = createSaveQueue({
    async run({ keepalive }) {
      accessError.value = '';
      const changes = [...pending];
      pending.clear();
      for (const [path, payload] of changes) {
        try {
          await request<void>(path, jsonRequest('PUT', payload, keepalive));
        } catch (error) {
          if (!pending.has(path)) pending.set(path, payload);
          accessError.value = (error as Error).message;
        }
      }
    }
  });

  async function load() {
    loading.value = true;
    loadError.value = '';
    try {
      const next = await request<McpSettings>('/settings');
      settings.value = next;
      workspaceId.value = next.default_workspace_id || next.workspaces[0]?.id || '';
    } catch (error) {
      loadError.value = (error as Error).message;
    } finally {
      loading.value = false;
    }
  }

  function persistAccess(path: string, payload: { server_ids: string[]; inherit?: boolean }) {
    pending.set(path, payload);
    retryAccess();
  }

  function retryAccess() {
    if (!pending.size) return;
    saveQueue.schedule();
    void saveQueue.flush();
  }

  function setDefault(actor: McpActor, ids: string[]) {
    if (!settings.value) return;
    settings.value.defaults[actor] = ids;
    persistAccess(`/access/${actor}`, { server_ids: ids });
  }

  function setWorkspace(actor: McpActor, selection: WorkspaceSelection) {
    const workspace = settings.value?.workspaces.find((entry) => entry.id === workspaceId.value);
    if (!workspace) return;
    workspace.access[actor] = selection;
    persistAccess(`/access/${actor}/${encodeURIComponent(workspace.id)}`, selection);
  }

  async function refreshServices() {
    const next = await request<McpSettings>('/settings');
    if (!settings.value) return;
    const previousIds = new Set(settings.value.servers.map((server) => server.id));
    const knownIds = new Set(next.servers.map((server) => server.id));
    settings.value.servers = next.servers;
    for (const actor of MCP_ACTORS) {
      settings.value.defaults[actor] = [
        ...settings.value.defaults[actor].filter((id) => knownIds.has(id)),
        ...next.defaults[actor].filter((id) => !previousIds.has(id))
      ];
      for (const workspace of settings.value.workspaces) {
        const selection = workspace.access[actor];
        if (selection) selection.server_ids = selection.server_ids.filter((id) => knownIds.has(id));
      }
    }
  }

  async function saveServer(config: Partial<McpServerConfig>, id?: string) {
    const path = id ? `/servers/${encodeURIComponent(id)}` : '/servers';
    return request<McpServerSummary>(path, jsonRequest(id ? 'PUT' : 'POST', config));
  }

  function flushOnExit() {
    if (pending.size && !saveQueue.hasPending()) saveQueue.schedule();
    void saveQueue.flush({ keepalive: true });
  }

  onMounted(() => {
    window.addEventListener('pagehide', flushOnExit);
    void load();
  });
  onBeforeUnmount(() => {
    window.removeEventListener('pagehide', flushOnExit);
    flushOnExit();
  });

  return {
    settings,
    workspaceId,
    loading,
    loadError,
    accessError,
    load,
    retryAccess,
    setDefault,
    setWorkspace,
    refreshServices,
    saveServer,
    getServer: (id: string) => request<McpServerConfig>(`/servers/${encodeURIComponent(id)}`),
    deleteServer: (id: string) =>
      request<void>(`/servers/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    syncServers: (id?: string) => request<void>('/sync', jsonRequest('POST', id ? { id } : {}))
  };
}

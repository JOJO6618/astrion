import { defineStore } from 'pinia';

const endpoint = '/api/conversation-storage/migration';
const topic = 'astrion:conversation-storage-migrated';

export function requiresConversationMigration(status: number, code?: string) {
  return status === 409 && code === 'conversation_migration_required';
}

export function onConversationStorageMigrated(refresh: () => void) {
  const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(topic);
  window.addEventListener(topic, refresh);
  if (channel) channel.onmessage = refresh;
  return () => {
    window.removeEventListener(topic, refresh);
    channel?.close();
  };
}

function notifyConversationStorageMigrated() {
  window.dispatchEvent(new Event(topic));
  if (typeof BroadcastChannel === 'undefined') return;
  const channel = new BroadcastChannel(topic);
  channel.postMessage(null);
  channel.close();
}

interface MigrationStatus {
  pending: number;
  migrated: number;
  failed: number;
  errors: string[];
  running: boolean;
}

async function requestStatus(method: 'GET' | 'POST'): Promise<MigrationStatus | null> {
  const response = await fetch(endpoint, { method, credentials: 'same-origin', cache: 'no-store' });
  if (response.status === 404 && method === 'GET') return null;
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || payload.message || '');
  return payload;
}

export const useConversationStorageMigrationStore = defineStore('conversationStorageMigration', {
  state: () => ({
    blockedAgent: null as { id: string; workspaceId: string } | null,
    open: false,
    loading: false,
    startupChecked: false,
    status: null as MigrationStatus | null,
    error: null as string | null
  }),
  getters: {
    running: (state) => state.status?.running === true && state.error === null,
    busy(): boolean {
      return this.loading || this.running;
    }
  },
  actions: {
    async checkStartup() {
      if (this.startupChecked || this.open) return;
      this.startupChecked = true;
      if (!(await this.load('GET', true))) this.startupChecked = false;
    },
    show() {
      if (this.open) return;
      this.open = true;
      this.status = null;
      if (!this.loading) void this.load();
    },
    dismiss() {
      if (!this.busy) this.open = false;
    },
    async load(method: 'GET' | 'POST' = 'GET', startup = false) {
      if (this.busy) return false;
      this.loading = true;
      this.error = null;
      try {
        let status = await requestStatus(method);
        const wasRunning = status?.running === true;
        if (startup && (status?.pending || wasRunning)) this.open = true;
        this.status = status;
        while (status?.running) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, 500));
          status = await requestStatus('GET');
          this.status = status;
        }
        if ((method === 'POST' || wasRunning) && status) notifyConversationStorageMigrated();
        return true;
      } catch (error) {
        if (this.open) this.error = error instanceof Error ? error.message : '';
        return false;
      } finally {
        this.loading = false;
      }
    }
  }
});

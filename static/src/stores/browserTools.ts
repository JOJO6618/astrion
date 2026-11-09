import { defineStore } from 'pinia';
import { workspaceScopedFetch as fetch } from '@/app/methods/workspaceRequestScope';

interface BrowserToolsResponse {
  success: boolean;
  supported?: boolean;
  enabled?: boolean;
  pending?: 'on' | 'off' | null;
  error?: string;
}

/** Per-conversation switch for the model's built-in browser tools (macOS desktop Agent only). */
export const useBrowserToolsStore = defineStore('browserTools', {
  state: () => ({
    supported: false,
    enabled: false,
    pending: '' as '' | 'on' | 'off',
    /** Selection on the /new page; submitted with the first message's settings. */
    draft: false,
    busy: false,
    error: ''
  }),
  getters: {
    effective: (state) => (state.pending ? state.pending === 'on' : state.enabled)
  },
  actions: {
    async refresh(conversationId: string | null) {
      try {
        const query = conversationId
          ? `?conversation_id=${encodeURIComponent(conversationId)}`
          : '';
        const response = await fetch(`/api/browser-tools${query}`);
        const data = (await response.json()) as BrowserToolsResponse;
        if (!response.ok || !data.success) return;
        this.supported = data.supported === true;
        if (conversationId) {
          this.enabled = data.enabled === true;
          this.pending = data.pending || '';
        }
      } catch {
        /* The switch just stays hidden when the backend is unreachable. */
      }
    },
    async set(conversationId: string | null, enabled: boolean) {
      this.error = '';
      if (!conversationId) {
        this.draft = enabled;
        return;
      }
      if (this.busy) return;
      this.busy = true;
      try {
        const response = await fetch('/api/browser-tools', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conversation_id: conversationId, enabled })
        });
        const data = (await response.json()) as BrowserToolsResponse;
        if (!response.ok || !data.success) throw new Error(data.error || 'failed');
        if (!data.pending) this.enabled = data.enabled === true;
        this.pending = data.pending || '';
      } catch (error) {
        this.error = String((error as Error).message || error);
      } finally {
        this.busy = false;
      }
    }
  }
});

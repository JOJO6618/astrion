import { defineStore } from 'pinia';
import { NavigationError, NavigationTransport, navigationId } from './desktopNavigationTransport';

export interface ConversationTab {
  /** Immutable for the entire tab lifetime, including new -> conversation binding. */
  key: string;
  kind: 'conv' | 'new';
  conversationId: string;
  workspaceId: string;
  workspaceLabel: string;
  title: string;
  error: string;
  running: boolean;
}

export interface DesktopNavigation {
  id: string;
  key: string;
  page: string;
  status: 'pending' | 'committed' | 'error';
  error: string;
}

export interface NavigationOwner {
  navigationId: string;
  rendererId: string;
  key: string;
}

const transports = new WeakMap<object, NavigationTransport>();
function transport(store: object) {
  if (!transports.has(store)) transports.set(store, new NavigationTransport());
  return transports.get(store)!;
}

function normalizeTab(raw: any): ConversationTab {
  return {
    key: String(raw.key),
    kind: raw.kind,
    conversationId: String(raw.conversation_id || ''),
    workspaceId: String(raw.workspace_id || ''),
    workspaceLabel: String(raw.workspace_label || ''),
    title: String(raw.title || ''),
    error: String(raw.error || ''),
    running: Boolean(raw.running)
  };
}

export const useConversationTabsStore = defineStore('conversationTabs', {
  state: () => ({
    enabled: Boolean((window as any).__ASTRION_DESKTOP__),
    tabs: [] as ConversationTab[],
    activeKey: '',
    resumeKey: '',
    workspaceId: '',
    navigation: null as DesktopNavigation | null,
    revision: -1,
    responseSequence: 0,
    hydrated: false,
    rendererId: '',
    localRendererId: navigationId(),
    attached: false,
    startupNavigationId: '',
    syncError: '',
    commandError: ''
  }),
  getters: {
    activeTab: (state): ConversationTab | null =>
      state.tabs.find((tab) => tab.key === state.activeKey) || null,
    requestedKey: (state): string =>
      state.navigation?.status === 'pending' ? state.navigation.key : ''
  },
  actions: {
    applyServerSnapshot(raw: any, sequence = 0) {
      if (!this.enabled || !raw || !Array.isArray(raw.tabs) || !Number.isInteger(raw.revision))
        return;
      if (
        raw.revision < this.revision ||
        (raw.revision === this.revision && sequence < this.responseSequence)
      )
        return;
      this.responseSequence = sequence;
      // The coordinator observes after this patch; synchronous nested-field watchers are unsafe.
      this.$patch({
        tabs: raw.tabs.map(normalizeTab),
        activeKey: String(raw.active_key || ''),
        resumeKey: String(raw.resume_key || ''),
        workspaceId: String(raw.workspace_id || ''),
        rendererId: String(raw.renderer_id || ''),
        navigation: raw.navigation || null,
        revision: raw.revision,
        hydrated: true,
        syncError: ''
      });
    },
    async hydrate() {
      if (!this.enabled || this.hydrated) return;
      await this.refreshSnapshot();
      this.startupNavigationId = this.navigation?.id || '';
    },
    async refreshSnapshot() {
      if (!this.enabled) return;
      try {
        const { result, sequence } = await transport(this).snapshot();
        this.applyServerSnapshot(result, sequence);
      } catch (error) {
        this.syncError = String((error as Error)?.message || error);
        throw error;
      }
    },
    startPolling() {
      if (!this.enabled) return;
      transport(this).start(
        (state, sequence) => this.applyServerSnapshot(state, sequence),
        (error) => {
          this.syncError = String((error as Error)?.message || error);
        }
      );
    },
    stopPolling() {
      transport(this).stop();
    },
    async command(action: string, payload: Record<string, unknown> = {}) {
      if (!this.enabled) return null;
      this.commandError = '';
      try {
        const { result, sequence } = await transport(this).command({
          command_id: navigationId(),
          action,
          payload
        });
        this.applyServerSnapshot(result.state, sequence);
        return result.result || {};
      } catch (error) {
        if (error instanceof NavigationError && error.status === 409) {
          await this.refreshSnapshot().catch(() => {});
        }
        this.commandError = String((error as Error)?.message || error);
        throw error;
      }
    },
    async attach(payload: Record<string, unknown>) {
      await this.command('attach', {
        ...payload,
        renderer_id: this.localRendererId,
        seen_navigation_id: this.startupNavigationId
      });
      this.attached = true;
    },
    ownsNavigation(owner: NavigationOwner) {
      return (
        this.attached &&
        this.rendererId === owner.rendererId &&
        this.localRendererId === owner.rendererId &&
        this.navigation?.id === owner.navigationId &&
        this.navigation?.key === owner.key &&
        (!owner.key || this.tabs.some((tab) => tab.key === owner.key))
      );
    },
    async commitNavigation(owner: NavigationOwner) {
      if (!this.ownsNavigation(owner)) return false;
      try {
        await this.command('commit', {
          navigation_id: owner.navigationId,
          renderer_id: owner.rendererId
        });
        return this.ownsNavigation(owner);
      } catch (error) {
        if (error instanceof NavigationError && error.status === 409) return false;
        throw error;
      }
    },
    addNewTab(payload: { workspaceId?: string; workspaceLabel?: string } = {}) {
      return this.command('create', {
        key: `tab_${navigationId()}`,
        workspace_id: payload.workspaceId || this.workspaceId,
        workspace_label: payload.workspaceLabel || ''
      });
    },
    openConversationTab(payload: { conversationId: string; workspaceId: string; title?: string }) {
      return this.command('open', {
        key: `tab_${navigationId()}`,
        conversation_id: payload.conversationId,
        workspace_id: payload.workspaceId,
        title: payload.title || ''
      });
    },
    closeTab(key: string) {
      return this.command('close', { key });
    },
    setActive(key: string) {
      return this.command('activate', { key });
    },
    bindConversation(key: string, workspaceId: string, conversationId: string, title: string) {
      return this.command('bind', {
        key,
        workspace_id: workspaceId,
        conversation_id: conversationId,
        renderer_id: this.localRendererId,
        title
      });
    },
    openPage(page: string) {
      return this.command('page', { page });
    },
    resume() {
      return this.command('resume', { workspace_id: this.workspaceId });
    },
    updateTitle(conversationId: string, title: string, workspaceId: string) {
      if (!this.enabled || !title) return;
      // The authoritative index also enriches titles; this command keeps the persisted fallback fresh.
      void this.command('title', {
        conversation_id: conversationId,
        workspace_id: workspaceId,
        title
      }).catch(() => {});
    }
  }
});

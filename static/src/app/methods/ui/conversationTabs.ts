// @ts-nocheck
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import {
  startDesktopNavigation,
  stopDesktopNavigation,
  desktopCreationTab
} from './desktopNavigation';
import { currentConversationSession } from '../conversation/session';
import { t } from '@/locales';

async function request(host, action, payload = {}) {
  try {
    await useConversationTabsStore().command(action, payload);
    return { success: true };
  } catch (error) {
    host.uiPushToast?.({
      title: t('appMessages.loadConversationErrorTitle'),
      message: String(error?.message || error),
      type: 'error'
    });
    return { success: false, error: String(error?.message || error) };
  }
}

export const conversationTabsMethods = {
  captureDesktopCreationTab() {
    return desktopCreationTab(this);
  },
  isDesktopNavigationReady() {
    const tabs = useConversationTabsStore();
    const session = currentConversationSession(this);
    const tab = tabs.activeTab;
    if (
      !tabs.attached ||
      tabs.rendererId !== tabs.localRendererId ||
      tabs.navigation?.status === 'pending' ||
      this.historyLoading ||
      this.hostWorkspaceSwitching ||
      !session ||
      !tab ||
      tab.workspaceId !== session.workspaceId
    )
      return false;
    if (tab.kind === 'new') return !this.currentConversationId && !session.conversationId;
    const normalize = (value) => String(value || '').replace(/^conv_/, '');
    return (
      normalize(tab.conversationId) === normalize(session.conversationId) &&
      normalize(tab.conversationId) === normalize(this.currentConversationId)
    );
  },
  openDesktopConversation(payload) {
    return request(this, 'open', {
      conversation_id: payload.conversationId,
      workspace_id: payload.workspaceId
    });
  },
  requestDesktopWorkspace(workspaceId, options = {}) {
    return request(this, options.newConversation ? 'create' : 'workspace', {
      workspace_id: workspaceId
    });
  },
  openDesktopPage(page) {
    return request(this, 'page', { page });
  },
  resumeDesktopTab() {
    return request(this, 'resume', { workspace_id: this.currentHostWorkspaceId || '' });
  },
  handleTabActivate(tab) {
    if (!tab) return;
    return request(this, 'activate', { key: tab.key });
  },
  handleTabNew(options = {}) {
    return request(this, 'create', {
      workspace_id: options.workspaceId || this.currentHostWorkspaceId || ''
    });
  },
  restoreConversationTabView() {
    return startDesktopNavigation(this);
  },
  stopConversationTabView() {
    stopDesktopNavigation(this);
  },
  handleGlobalTabShortcut(event) {
    const tabs = useConversationTabsStore();
    if (!tabs.enabled || !(event.metaKey || event.ctrlKey)) return;
    const key = String(event.key || '').toLowerCase();
    if (key === 't') {
      event.preventDefault();
      void this.handleTabNew();
    } else if (key === 'w') {
      const target = tabs.requestedKey || tabs.activeKey;
      const tab = tabs.tabs.find((item) => item.key === target);
      if (!tab || (tabs.tabs.length === 1 && tab.kind === 'new')) return;
      event.preventDefault();
      void request(this, 'close', { key: target });
    }
  },
  async enterNewConversationPage(options = {}) {
    const workspaceId = String(options.workspaceId || this.currentHostWorkspaceId || '');
    if (useConversationTabsStore().enabled) {
      return this.handleTabNew({ workspaceId });
    }
    if (this.isConversationIndependentRoute?.()) {
      window.location.assign(
        `/new${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`
      );
      return;
    }
    this.flushReasoningEffortSave?.();
    return this.handleHostWorkspaceSwitch(workspaceId, { newConversation: true });
  }
};

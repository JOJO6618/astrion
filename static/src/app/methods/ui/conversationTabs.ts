// @ts-nocheck
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { startDesktopView, stopDesktopView, desktopCreationTab } from './desktopView';
import { currentConversationSession } from '../conversation/session';
import { t } from '@/locales';

export function visibleSession(host) {
  return (
    currentConversationSession(host) || {
      conversationId: String(host.currentConversationId || ''),
      workspaceId: String(host.currentHostWorkspaceId || '')
    }
  );
}

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
  /** 发送守卫：已 attach、持有本标签 renderer 写权、标签仍在且身份与可见会话一致。 */
  isDesktopNavigationReady() {
    const tabs = useConversationTabsStore();
    // 刷新/冷启动的空白新对话视图没有 session（路由只 leaveConversationView）；
    // 此时以页面可见身份判定，发送时 ensureMessageSession 才建立 session。
    const session = visibleSession(this);
    const tab = tabs.viewTab;
    if (
      !tabs.ownsView ||
      this.historyLoading ||
      this.hostWorkspaceSwitching ||
      !session ||
      !tab ||
      tab.kind === 'page' ||
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
  /** 独立页面「返回」：回到 opener 标签（不关闭页面标签）。 */
  leaveDesktopPage() {
    return useConversationTabsStore()
      .leavePage()
      .then(() => ({ success: true }))
      .catch((error) => {
        this.uiPushToast?.({
          title: t('appMessages.loadConversationErrorTitle'),
          message: String(error?.message || error),
          type: 'error'
        });
        return { success: false };
      });
  },
  handleTabNew(options = {}) {
    return request(this, 'create', {
      workspace_id: options.workspaceId || this.currentHostWorkspaceId || ''
    });
  },
  restoreConversationTabView() {
    return startDesktopView(this);
  },
  stopConversationTabView() {
    stopDesktopView(this);
  },
  handleGlobalTabShortcut(event) {
    const tabs = useConversationTabsStore();
    if (!tabs.enabled || !(event.metaKey || event.ctrlKey)) return;
    const key = String(event.key || '').toLowerCase();
    if (key === 't') {
      event.preventDefault();
      void this.handleTabNew();
    } else if (key === 'w') {
      const tab = tabs.viewTab;
      if (!tab || (tabs.mainTabs.length === 1 && tab.kind === 'new')) return;
      event.preventDefault();
      void request(this, 'close', { key: tab.key });
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

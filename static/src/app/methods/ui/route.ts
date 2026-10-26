// @ts-nocheck
import { t } from '@/locales';
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { conversationViewEpoch, ownsConversationSession } from '../conversation/session';
import { prepareInitialWorkspaceIdentity } from './workspaceStartup';

async function restoreRouteDraft(host, reason) {
  const epoch = conversationViewEpoch(host);
  try {
    return await host.restoreComposerDraftState(reason);
  } catch (error) {
    if (conversationViewEpoch(host) !== epoch) return { superseded: true };
    host.uiPushToast?.({
      title: t('appUi.fetchInputDraftFailed'),
      message: String(error?.message || error),
      type: 'error'
    });
    return { success: false, error: String(error?.message || error) };
  }
}

export const routeMethods = {
  async bootstrapRoute() {
    let path = window.location.pathname.replace(/^\/+|\/+$/g, '');
    const blankRoute =
      !path || this.isExplicitNewConversationRoute() || this.isConversationIndependentRoute();
    if (blankRoute) this.leaveConversationView();
    const convId = blankRoute ? null : path.startsWith('conv_') ? path : `conv_${path}`;
    const session = convId
      ? this.beginConversationView(convId, this.currentHostWorkspaceId || '')
      : undefined;
    const epoch = conversationViewEpoch(this);
    const owns = () =>
      session ? ownsConversationSession(this, session) : conversationViewEpoch(this) === epoch;
    // Capture the pending desktop target before loading a possibly stale address-bar URL.
    let desktopTargetPending = false;
    try {
      const { useConversationTabsStore } = await import('../../../stores/conversationTabs');
      const tabsStore = useConversationTabsStore();
      if (tabsStore.enabled) {
        await tabsStore.hydrate();
        desktopTargetPending = tabsStore.navigation?.status === 'pending';
      }
    } catch (_tabsErr) {
      // 恢复失败不阻断路由
    }
    if (!owns()) return { success: false, superseded: true };
    if (!this.isConversationIndependentRoute() || path === 'git') {
      try {
        const ready = await prepareInitialWorkspaceIdentity(this, owns);
        if (!owns() || ready.superseded) return { success: false, superseded: true };
        if (ready.code === 'no_workspace') {
          this.initialRouteResolved = true;
          this.leaveConversationView();
          return ready;
        }
        if (session) session.workspaceId = this.currentHostWorkspaceId;
      } catch (error) {
        if (!owns()) return { success: false, superseded: true };
        this.initialRouteResolved = true;
        this.leaveConversationView();
        this.uiPushToast?.({
          title: t('appMessages.loadConversationErrorTitle'),
          message: String(error?.message || error),
          type: 'error'
        });
        return { success: false, error: String(error?.message || error) };
      }
    }
    // 在路由解析期间抑制标题动画，避免预置"新对话"闪烁
    this.suppressTitleTyping = true;
    this.titleReady = false;
    this.currentConversationTitle = '';
    this.titleTypingText = '';
    // 工作流编辑器：独立于对话体系的全屏路由，直接交给前端组件
    if (this.isConversationIndependentRoute()) {
      if (path === 'workflow') {
        history.replaceState({}, '', '/workflows');
        path = 'workflows';
      }
      if (path === 'settings' || path.startsWith('settings/')) {
        this.settingsRoute = path;
      } else if (path === 'git') {
        this.gitRoute = path;
      } else {
        this.workflowDemoRoute = path;
      }
      // Desktop page ownership is attached after bootstrap; no local tab mutation here.
      this.currentConversationId = null;
      this.currentConversationTitle = '';
      this.messages = [];
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.initialRouteResolved = true;
      return;
    }
    if (desktopTargetPending) {
      // The coordinator attaches after this route setup and loads the latest target.
      // Do not let a failed stale URL create a new tab over the saved pending intent.
      this.initialRouteResolved = true;
      this.leaveConversationView();
      return;
    }
    if (!path || this.isExplicitNewConversationRoute()) {
      this.currentConversationId = null;
      this.currentConversationTitle = t('common.newConversation');
      this.logMessageState('bootstrapRoute:clear-messages-for-new');
      this.messages = [];
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.startTitleTyping(t('common.newConversation'), { animate: false });
      this.initialRouteResolved = true;
      this.refreshBlankHeroState();
      if (!owns()) return { success: false, superseded: true };
      try {
        await this.loadNewConversationSettings();
      } catch (error) {
        if (!owns()) return { success: false, superseded: true };
        this.uiPushToast?.({
          title: t('appMessages.loadConversationErrorTitle'),
          message: String(error?.message || error),
          type: 'error'
        });
      }
      if (!owns()) return { success: false, superseded: true };
      await restoreRouteDraft(this, 'bootstrap-route:new');
      return;
    }

    try {
      // 统一加载协议：一次 bootstrap 拿元数据+历史+运行状态（纯只读，不切换后端上下文）
      const result = await this.enterConversation(convId, {
        source: 'refresh',
        session,
        workspaceId: this.currentHostWorkspaceId || '',
        urlMode: 'replace'
      });
      if (!owns() || result.superseded) return { success: false, superseded: true };
      if (result.success) {
        this.currentConversationTitle = result.title || '';
        this.titleReady = true;
        this.suppressTitleTyping = false;
        this.startTitleTyping(this.currentConversationTitle, { animate: false });
        // 刷新路径补拉当前快照的 token 统计。
        this.fetchConversationTokenStatistics();
        this.updateCurrentContextTokens();
      } else {
        this.initialRouteResolved = true;
        return this.enterNewConversationPage();
      }
    } catch (error) {
      if (!owns()) return { success: false, superseded: true };
      this.initialRouteResolved = true;
      console.warn('初始化路由失败:', error);
      return this.enterNewConversationPage();
    } finally {
      if (owns()) this.initialRouteResolved = true;
    }
    await restoreRouteDraft(this, 'bootstrap-route:conversation');
    if (this.currentConversationId) {
      this.fetchPendingUserQuestions?.();
    }
  },
  handlePopState(event) {
    const state = event.state || {};
    const convId = state.conversationId;
    if (!convId) {
      const workspaceId =
        new URLSearchParams(window.location.search).get('workspace_id') ||
        this.currentHostWorkspaceId;
      return this.enterNewConversationPage({ workspaceId });
    }
    this.handleSelectWorkspaceConversation({
      conversationId: String(convId || ''),
      workspaceId: String(this.currentHostWorkspaceId || '')
    });
  },
  refreshCurrentPage() {
    if (typeof window === 'undefined') {
      return;
    }
    this.persistComposerDraftNow({
      reason: 'refresh-page',
      force: true,
      keepalive: true
    })
      .catch(() => {})
      .finally(() => {
        window.location.reload();
      });
  },
  isExplicitNewConversationRoute() {
    const normalizedPath = window.location.pathname.replace(/^\/+|\/+$/g, '');
    // 多智能体旧路由在 bootstrapRoute 已被重定向到 /new，这里只判裸新建路由
    return normalizedPath === 'new';
  },
  /**
   * 当前 URL 是否为独立于对话体系的全屏路由（当前：/workflows、/workflow/*、/settings、/settings/*）。
   * 与 isExplicitNewConversationRoute 一样直接读 location.pathname，不依赖
   * bootstrap 阶段写入的状态字段，任意时机调用结果都正确。
   * 对话体系的自动恢复/广播接管/任务接管/URL 写回在这些路由下必须全部禁用，
   * 这是唯一判定入口，新增同类路由（全屏独立页）时在此登记。
   */
  isConversationIndependentRoute() {
    const normalizedPath = window.location.pathname.replace(/^\/+|\/+$/g, '');
    return (
      normalizedPath === 'git' ||
      normalizedPath === 'workflows' ||
      normalizedPath === 'workflow' ||
      normalizedPath.startsWith('workflow/') ||
      normalizedPath === 'settings' ||
      normalizedPath.startsWith('settings/')
    );
  },
  openWorkflowsPage() {
    if (useConversationTabsStore().enabled) return this.openDesktopPage('/workflows');
    this.leaveConversationView();
    window.location.assign('/workflows');
  },
  openGitPage() {
    const workspaceId = this.currentHostWorkspaceId;
    if (!workspaceId) {
      this.uiPushToast?.({ message: t('git.workspaceRequired'), type: 'warning' });
      return;
    }
    if (useConversationTabsStore().enabled)
      return this.openDesktopPage(`/git?workspace_id=${encodeURIComponent(workspaceId)}`);
    this.leaveConversationView();
    window.location.assign(`/git?workspace_id=${encodeURIComponent(workspaceId)}`);
  },
  closeGitPage() {
    const workspaceId = this.currentHostWorkspaceId;
    this.closeSettingsPage(
      workspaceId ? `/new?workspace_id=${encodeURIComponent(workspaceId)}` : '/new'
    );
  },
  openSettingsPage() {
    if (useConversationTabsStore().enabled) return this.openDesktopPage('/settings');
    this.leaveConversationView();
    window.location.assign('/settings');
  },
  closeSettingsPage(fallback = '/new') {
    if (useConversationTabsStore().enabled) return this.resumeDesktopTab();
    if (new URLSearchParams(window.location.search).get('from') === 'chat') {
      window.location.assign('/chat/new');
      return;
    }
    window.location.assign(typeof fallback === 'string' ? fallback : '/new');
  },
  stripConversationPrefix(conversationId) {
    if (!conversationId) return '';
    return conversationId.startsWith('conv_') ? conversationId.slice(5) : conversationId;
  }
};

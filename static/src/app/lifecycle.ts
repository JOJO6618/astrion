// @ts-nocheck
import { useChatActionStore } from '../stores/chatActions';
import { useSandboxSetupStore } from '../stores/sandboxSetup';
import { normalizeScrollLock } from '../composables/useScrollControl';
import {
  setupShowImageObserver,
  teardownShowImageObserver,
  setupImagePreviewDelegation
} from './bootstrap';
import { debugLog } from './methods/common';

const mountedHosts = new WeakSet<object>();

export function created() {
  const actionStore = useChatActionStore();
  actionStore.registerDependencies({
    pushToast: (payload) => this.uiPushToast(payload),
    autoResizeInput: () => this.autoResizeInput(),
    focusComposer: () => {
      const inputEl = this.getComposerElement('stadiumInput');
      if (inputEl && typeof inputEl.focus === 'function') {
        inputEl.focus();
      }
    },
    isConnected: () => this.isConnected,
    executeCommand: async (command) => this.executeSystemCommand(command, { showToast: false }),
    downloadResource: (url, filename) => this.downloadResource(url, filename)
  });
}

export async function mounted() {
  mountedHosts.add(this);
  debugLog('Vue应用已挂载');
  if (window.ensureCsrfToken) {
    window.ensureCsrfToken().catch((err) => {
      console.warn('CSRF token 初始化失败:', err);
    });
  }
  // 移动端视口判断必须在路由/对话加载前生效，
  // 否则加载期间移动端会按桌面布局渲染（QuickDock 挤压页面）。
  this.setupMobileViewportWatcher();
  // 并行启动路由解析与初始化数据，网页端不再依赖 WebSocket 初始化
  const routePromise = this.bootstrapRoute();
  await routePromise;
  if (!mountedHosts.has(this)) return;
  this.$nextTick(() => {
    this.ensureScrollListener();
    // 刷新后若无输出，自动解锁滚动锁定
    normalizeScrollLock(this);
  });
  setupShowImageObserver();
  setupImagePreviewDelegation();

  // 立即加载初始数据（并行获取状态，优先同步运行模式）
  const initialDataPromise = this.loadInitialData();
  this.startProjectGitSummaryIdleRefresh?.();
  this.startConnectionHeartbeat();
  this.startStatusIdleRefresh?.();
  this.fetchTerminalCount();
  this.startTerminalCountIdleRefresh();
  this.checkTutorialPrompt();
  // Windows 宿主机模式下检测沙箱环境，缺失时弹出居中的安装向导（不分 sandbox/direct 执行环境）
  void useSandboxSetupStore().autoCheck();
  if (initialDataPromise && typeof initialDataPromise.then === 'function') {
    initialDataPromise
      .then(() => {
        if (!mountedHosts.has(this)) return;
        // 初始数据加载完成后再刷新 git 摘要，确保 workspace/project_path 已就绪
        this.refreshProjectGitSummary?.();
        // 避免在初始加载阶段被覆盖，加载完成后再兜底检查一次
        this.checkTutorialPrompt();
      })
      .finally(() => {
        if (!mountedHosts.has(this)) return;
        // Restoring a tab advances the conversation epoch. Finish initialization
        // first, or its ownership checks silently skip preferences and workspaces.
        // Attach after failures too, so a transient auxiliary error cannot strand tabs.
        return this.restoreConversationTabView?.();
      })
      .catch(() => {});
  }

  // Desktop targets survive document replacement in the server navigation manager.
  // bootstrapRoute owns the complete snapshot and its live subscription.

  document.addEventListener('click', this.handleClickOutsideQuickMenu);
  document.addEventListener('click', this.handleClickOutsideHeaderMenu);
  document.addEventListener('click', this.handleCopyCodeClick);
  window.addEventListener('popstate', this.handlePopState);
  window.addEventListener('keydown', this.handleMobileOverlayEscape);
  // 桌面端标签快捷键（⌘T/⌘W）：双 webview 后主页面聚焦时按键到不了 chrome 条
  window.addEventListener('keydown', this.handleGlobalTabShortcut);
  window.addEventListener('beforeunload', this.handleBeforeUnloadDraftPersist);

  this.subAgentFetch();
  this.backgroundCommandFetch();

  this.$nextTick(() => {
    this.autoResizeInput();
  });
  this.resourceBindContainerVisibilityWatcher();
  this.resourceStartContainerStatsPolling();
  this.resourceStartProjectStoragePolling();
  this.resourceStartUsageQuotaPolling();

  // 设置拖拽上传
  this.setupDragAndDrop();
}

export function beforeUnmount() {
  mountedHosts.delete(this);
  if (this._todoRefreshTimer) {
    clearTimeout(this._todoRefreshTimer);
    this._todoRefreshTimer = null;
  }

  this.stopConversationTabView?.();
  this.leaveConversationView();
  this.stopWorkspaceTasksRefresh();

  document.removeEventListener('click', this.handleClickOutsideQuickMenu);
  document.removeEventListener('click', this.handleClickOutsideHeaderMenu);
  document.removeEventListener('click', this.handleCopyCodeClick);
  window.removeEventListener('popstate', this.handlePopState);
  window.removeEventListener('keydown', this.handleMobileOverlayEscape);
  window.removeEventListener('keydown', this.handleGlobalTabShortcut);
  window.removeEventListener('beforeunload', this.handleBeforeUnloadDraftPersist);
  this.teardownMobileViewportWatcher();
  this.stopProjectGitSummaryIdleRefresh?.();
  this.stopTerminalCountIdleRefresh?.();
  this.stopStatusIdleRefresh?.();
  this.resourceStopContainerStatsPolling();
  this.resourceStopProjectStoragePolling();
  this.resourceStopUsageQuotaPolling();
  this.stopConnectionHeartbeat();
  teardownShowImageObserver();

  // 移除拖拽上传监听
  this.teardownDragAndDrop();
  if (this.titleTypingTimer) {
    clearInterval(this.titleTypingTimer);
    this.titleTypingTimer = null;
  }
  const cleanup = this.destroyEasterEggEffect(true);
  if (cleanup && typeof cleanup.catch === 'function') {
    cleanup.catch(() => {});
  }
}

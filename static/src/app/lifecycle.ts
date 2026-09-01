// @ts-nocheck
import { useChatActionStore } from '../stores/chatActions';
import { useSandboxSetupStore } from '../stores/sandboxSetup';
import { normalizeScrollLock } from '../composables/useScrollControl';
import { setupShowImageObserver, teardownShowImageObserver, setupImagePreviewDelegation } from './bootstrap';
import { debugLog } from './methods/common';

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
  this.fetchTerminalCount();
  this.startTerminalCountIdleRefresh();
  this.checkTutorialPrompt();
  // Windows 宿主机模式下检测沙箱环境，缺失时弹出居中的安装向导（不分 sandbox/direct 执行环境）
  void useSandboxSetupStore().autoCheck();
  if (initialDataPromise && typeof initialDataPromise.then === 'function') {
    initialDataPromise
      .then(() => {
        // 初始数据加载完成后再刷新 git 摘要，确保 workspace/project_path 已就绪
        this.refreshProjectGitSummary?.();
        // 避免在初始加载阶段被覆盖，加载完成后再兜底检查一次
        this.checkTutorialPrompt();
      })
      .catch(() => {});
  }

  // 注册全局事件处理器（用于任务轮询）
  (window as any).__taskEventHandler = (event: any) => {
    if (typeof this.handleTaskEvent === 'function') {
      this.handleTaskEvent(event);
    }
  };

  // 立即尝试恢复运行中的任务（不延迟）
  if (typeof this.restoreTaskState === 'function') {
    this.restoreTaskState();
  }

  document.addEventListener('click', this.handleClickOutsideQuickMenu);
  document.addEventListener('click', this.handleClickOutsideHeaderMenu);
  document.addEventListener('click', this.handleClickOutsideMobileMenu);
  document.addEventListener('click', this.handleCopyCodeClick);
  window.addEventListener('popstate', this.handlePopState);
  window.addEventListener('keydown', this.handleMobileOverlayEscape);
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
  if (this._todoRefreshTimer) {
    clearTimeout(this._todoRefreshTimer);
    this._todoRefreshTimer = null;
  }

  // 停止任务轮询
  try {
    const { useTaskStore } = require('../stores/task');
    const taskStore = useTaskStore();
    taskStore.stopPolling();
  } catch (error) {
    // ignore
  }

  document.removeEventListener('click', this.handleClickOutsideQuickMenu);
  document.removeEventListener('click', this.handleClickOutsideHeaderMenu);
  document.removeEventListener('click', this.handleClickOutsideMobileMenu);
  document.removeEventListener('click', this.handleCopyCodeClick);
  window.removeEventListener('popstate', this.handlePopState);
  window.removeEventListener('keydown', this.handleMobileOverlayEscape);
  window.removeEventListener('beforeunload', this.handleBeforeUnloadDraftPersist);
  this.teardownMobileViewportWatcher();
  this.stopProjectGitSummaryIdleRefresh?.();
  this.stopTerminalCountIdleRefresh?.();
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

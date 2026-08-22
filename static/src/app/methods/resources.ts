// @ts-nocheck
import {
  formatTokenCount,
  formatBytes,
  formatPercentage,
  formatRate,
  formatResetTime,
  formatQuotaValue,
  quotaTypeLabel,
  buildQuotaResetSummary,
  isQuotaExceeded as isQuotaExceededUtil,
  buildQuotaToastMessage
} from '../../utils/formatters';

export const resourceMethods = {
  hasContainerStats() {
    return !!(
      this.containerStatus &&
      this.containerStatus.mode === 'docker' &&
      this.containerStatus.stats
    );
  },

  containerStatusClass() {
    if (!this.containerStatus) {
      return 'status-pill--host';
    }
    if (this.containerStatus.mode !== 'docker') {
      return 'status-pill--host';
    }
    const rawStatus =
      (this.containerStatus.state &&
        (this.containerStatus.state.status || this.containerStatus.state.Status)) ||
      '';
    const status = String(rawStatus).toLowerCase();
    if (status.includes('running')) {
      return 'status-pill--running';
    }
    if (status.includes('paused')) {
      return 'status-pill--stopped';
    }
    if (status.includes('exited') || status.includes('dead')) {
      return 'status-pill--stopped';
    }
    return 'status-pill--running';
  },

  containerStatusText() {
    if (!this.containerStatus) {
      return '未知';
    }
    if (this.containerStatus.mode !== 'docker') {
      return '宿主机模式';
    }
    const rawStatus =
      (this.containerStatus.state &&
        (this.containerStatus.state.status || this.containerStatus.state.Status)) ||
      '';
    const status = String(rawStatus).toLowerCase();
    if (status.includes('running')) {
      return '运行中';
    }
    if (status.includes('paused')) {
      return '已暂停';
    }
    if (status.includes('exited') || status.includes('dead')) {
      return '已停止';
    }
    return rawStatus || '容器模式';
  },

  formatTime(value) {
    if (!value) {
      return '未知时间';
    }
    let date;
    if (typeof value === 'number') {
      date = new Date(value);
    } else if (typeof value === 'string') {
      const parsed = Date.parse(value);
      if (!Number.isNaN(parsed)) {
        date = new Date(parsed);
      } else {
        const numeric = Number(value);
        if (!Number.isNaN(numeric)) {
          date = new Date(numeric);
        }
      }
    } else if (value instanceof Date) {
      date = value;
    }
    if (!date || Number.isNaN(date.getTime())) {
      return String(value);
    }
    const now = Date.now();
    const diff = now - date.getTime();
    if (diff < 60000) {
      return '刚刚';
    }
    if (diff < 3600000) {
      const mins = Math.floor(diff / 60000);
      return `${mins} 分钟前`;
    }
    if (diff < 86400000) {
      const hours = Math.floor(diff / 3600000);
      return `${hours} 小时前`;
    }
    const formatter = new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
    return formatter.format(date);
  },

  async updateCurrentContextTokens() {
    await this.resourceUpdateCurrentContextTokens(this.currentConversationId);
  },

  async fetchConversationTokenStatistics() {
    await this.resourceFetchConversationTokenStatistics(this.currentConversationId);
  },

  toggleTokenPanel() {
    this.resourceToggleTokenPanel();
  },

  applyStatusSnapshot(status) {
    this.resourceApplyStatusSnapshot(status);
    // 模型/思考模式/推理强度的权威来源规则：
    // - 非空对话：以对话 meta 为准（enterConversation bootstrap 恢复），
    //   status 快照不得覆盖（terminal 状态可能滞后于对话加载）
    // - 空对话/首页：terminal 状态即权威，从 status 同步
    // - 例外：显式新建路由（/new、/multiagent/new）上 terminal 状态属于上一个
    //   对话的残留上下文，不同步（个性化默认值是这里的权威，见 loadInitialData）
    const convId = this.currentConversationId;
    const hasConversation =
      typeof convId === 'string' && convId.length > 0 && !convId.startsWith('temp_');
    const onExplicitNewRoute =
      typeof this.isExplicitNewConversationRoute === 'function' &&
      this.isExplicitNewConversationRoute();
    if (!hasConversation && !onExplicitNewRoute) {
      if (status && typeof status.thinking_mode !== 'undefined') {
        this.thinkingMode = !!status.thinking_mode;
      }
      if (status && typeof status.run_mode === 'string') {
        // 历史值 deep 映射为 thinking
        this.runMode = status.run_mode === 'deep' ? 'thinking' : status.run_mode;
      } else if (status && typeof status.thinking_mode !== 'undefined') {
        this.runMode = status.thinking_mode ? 'thinking' : 'fast';
      }
      if (status && Object.prototype.hasOwnProperty.call(status, 'reasoning_effort')) {
        this.reasoningEffort =
          typeof status.reasoning_effort === 'string' ? status.reasoning_effort : null;
      }
      if (status && typeof status.model_key === 'string') {
        this.modelSet(status.model_key);
      }
    }
    if (status && typeof status.permission_mode === 'string') {
      this.currentPermissionMode = status.permission_mode;
    }
    // 运行模式（work_mode）：terminal 快照权威。对话加载后 load.ts 会再调
    // fetchWorkMode 以对话 metadata 为准修正一次，两者一致，不会跳变。
    if (status && typeof status.work_mode === 'string') {
      this.currentWorkMode = status.work_mode;
    }
    const pendingModes = status?.pending_runtime_modes || {};
    if (typeof pendingModes.permission_mode === 'string') {
      this.pendingPermissionMode = pendingModes.permission_mode;
    } else {
      this.pendingPermissionMode = '';
    }
    if (status?.execution_mode && typeof status.execution_mode === 'object') {
      if (typeof status.execution_mode.mode === 'string') {
        this.currentExecutionMode = status.execution_mode.mode;
      }
      this.executionModeEnabled =
        typeof status.execution_mode_enabled === 'boolean' ? status.execution_mode_enabled : true;
    }
    if (typeof status.network_permission === 'string') {
      this.currentNetworkPermission = status.network_permission;
    }
    this.networkPermissionEnabled =
      typeof status.network_permission_enabled === 'boolean' ? status.network_permission_enabled : false;
    if (typeof pendingModes.network_permission === 'string') {
      this.pendingNetworkPermission = pendingModes.network_permission;
    } else {
      this.pendingNetworkPermission = '';
    }
    if (typeof pendingModes.execution_mode === 'string') {
      this.pendingExecutionMode = pendingModes.execution_mode;
    } else {
      this.pendingExecutionMode = '';
    }
    // has_images/has_videos 遵循与上面模型/运行模式相同的权威规则：
    // 有打开对话时才从 status 同步；空对话/显式新建路由（/new）上 status 里的值
    // 是 terminal 残留的旧对话上下文，不能应用——空对话语义上无图片/视频，
    // 直接置 false，否则会误拦 handleModelSelect 的图片/视频能力检查。
    if (hasConversation && !onExplicitNewRoute) {
      if (status && typeof status.has_images !== 'undefined') {
        this.conversationHasImages = !!status.has_images;
      }
      if (status && typeof status.has_videos !== 'undefined') {
        this.conversationHasVideos = !!status.has_videos;
      }
    } else {
      this.conversationHasImages = false;
      this.conversationHasVideos = false;
    }
    // 压缩状态与 has_images/has_videos 同理：status 反映的是后端 terminal 当前
    // 对话的压缩状态。空对话/显式新建路由（/new）上没有打开对话，status 里的
    // 压缩标记属于残留的旧对话上下文，应用会把输入锁错误地带到 /new 页。
    const compression = status?.conversation?.compression;
    if (hasConversation && !onExplicitNewRoute) {
      if (compression && typeof compression === 'object') {
        this.compressionInProgress = !!compression.in_progress;
        this.compressionMode = compression.mode || '';
        this.compressionStage = compression.stage || '';
        this.compressionError = compression.error || '';
        this.compressionConversationId = this.compressionInProgress ? this.currentConversationId : null;
      } else {
        this.compressionInProgress = false;
        this.compressionConversationId = null;
        this.compressionMode = '';
        this.compressionStage = '';
        this.compressionError = '';
      }
    }
  },

  updateContainerStatus(status) {
    this.resourceUpdateContainerStatus(status);
  },

  pollContainerStats() {
    return this.resourcePollContainerStats();
  },

  startContainerStatsPolling() {
    this.resourceStartContainerStatsPolling();
  },

  stopContainerStatsPolling() {
    this.resourceStopContainerStatsPolling();
  },

  pollProjectStorage() {
    return this.resourcePollProjectStorage();
  },

  startProjectStoragePolling() {
    this.resourceStartProjectStoragePolling();
  },

  stopProjectStoragePolling() {
    this.resourceStopProjectStoragePolling();
  },

  fetchUsageQuota() {
    return this.resourceFetchUsageQuota();
  },

  startUsageQuotaPolling() {
    this.resourceStartUsageQuotaPolling();
  },

  stopUsageQuotaPolling() {
    this.resourceStopUsageQuotaPolling();
  },

  async downloadFile(path) {
    if (!path) {
      this.fileHideContextMenu();
      return;
    }
    const url = `/api/download/file?path=${encodeURIComponent(path)}`;
    const name = path.split('/').pop() || 'file';
    await this.downloadResource(url, name);
  },

  async downloadFolder(path) {
    if (!path) {
      this.fileHideContextMenu();
      return;
    }
    const url = `/api/download/folder?path=${encodeURIComponent(path)}`;
    const segments = path.split('/').filter(Boolean);
    const folderName = segments.length ? segments.pop() : 'folder';
    await this.downloadResource(url, `${folderName}.zip`);
  },

  async downloadResource(url, filename) {
    // Android App 内优先走原生下载桥接，解决 WebView 中 a.download / blob URL 无法触发系统下载的问题
    const androidBridge = (window as any).AndroidDownloadBridge;
    if (androidBridge && typeof androidBridge.downloadFile === 'function') {
      try {
        const absoluteUrl = new URL(url, window.location.href).href;
        androidBridge.downloadFile(absoluteUrl, filename || 'download');
        return;
      } catch (error) {
        console.warn('Android 桥接下载失败，回退:', error);
      }
    }

    try {
      const response = await fetch(url);
      if (!response.ok) {
        let message = response.statusText;
        try {
          const errorData = await response.json();
          message = errorData.error || errorData.message || message;
        } catch (err) {
          message = await response.text();
        }
        this.uiPushToast({
          title: '下载失败',
          message: message || '无法完成下载',
          type: 'error'
        });
        return;
      }

      const blob = await response.blob();
      const downloadName = filename || 'download';
      const link = document.createElement('a');
      const href = URL.createObjectURL(blob);
      link.href = href;
      link.download = downloadName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(href);
    } catch (error) {
      console.error('下载失败:', error);
      this.uiPushToast({
        title: '下载失败',
        message: error.message || String(error),
        type: 'error'
      });
    } finally {
      this.fileHideContextMenu();
    }
  },

  formatTokenCount,
  formatBytes,
  formatPercentage,
  formatRate,
  formatResetTime,
  formatQuotaValue,
  quotaTypeLabel,

  quotaResetSummary() {
    return buildQuotaResetSummary(this.usageQuota);
  },

  isQuotaExceeded(type) {
    return isQuotaExceededUtil(this.usageQuota, type);
  },

  showQuotaToast(payload) {
    if (!payload) {
      return;
    }
    const type = payload.type || 'fast';
    const message = buildQuotaToastMessage(type, this.usageQuota, payload.reset_at);
    this.uiShowQuotaToastMessage(message, type);
  }
};

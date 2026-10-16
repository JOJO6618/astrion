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
import { t, currentLocale } from '@/locales';
import { downloadWorkspaceFile } from '@/utils/downloadFile';

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
    void currentLocale.value;
    if (!this.containerStatus) {
      return t('appTasks.unknown');
    }
    if (this.containerStatus.mode !== 'docker') {
      return t('appTasks.hostMode');
    }
    const rawStatus =
      (this.containerStatus.state &&
        (this.containerStatus.state.status || this.containerStatus.state.Status)) ||
      '';
    const status = String(rawStatus).toLowerCase();
    if (status.includes('running')) {
      return t('common.running');
    }
    if (status.includes('paused')) {
      return t('appTasks.paused');
    }
    if (status.includes('exited') || status.includes('dead')) {
      return t('appTasks.stopped');
    }
    return rawStatus || t('appTasks.containerMode');
  },

  formatTime(value) {
    void currentLocale.value;
    if (!value) {
      return t('appTasks.unknownTime');
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
      return t('appTasks.justNow');
    }
    if (diff < 3600000) {
      const mins = Math.floor(diff / 60000);
      return t('appTasks.minutesAgo', { n: mins });
    }
    if (diff < 86400000) {
      const hours = Math.floor(diff / 3600000);
      return t('appTasks.hoursAgo', { n: hours });
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
    // Existing conversations restore model settings from bootstrap; /new owns its
    // prepared settings locally. Status polling must not replace either source.
    const convId = this.currentConversationId;
    const hasConversation =
      typeof convId === 'string' && convId.length > 0 && !convId.startsWith('temp_');
    const onExplicitNewRoute =
      typeof this.isExplicitNewConversationRoute === 'function' &&
      this.isExplicitNewConversationRoute();
    // Runtime mode updates apply only to the explicitly displayed conversation.
    const statusConvIdRaw = status?.conversation?.current_id;
    const statusConvId = typeof statusConvIdRaw === 'string' ? statusConvIdRaw : '';
    const modeSourceMatches = hasConversation && statusConvId === convId;
    if (modeSourceMatches) {
      if (status && typeof status.permission_mode === 'string') {
        this.currentPermissionMode = status.permission_mode;
      }
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
        typeof status.network_permission_enabled === 'boolean'
          ? status.network_permission_enabled
          : false;
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
    if (
      hasConversation &&
      !onExplicitNewRoute &&
      status?.conversation?.current_id === this.currentConversationId
    ) {
      if (compression && typeof compression === 'object') {
        this.compressionInProgress = !!compression.in_progress;
        this.compressionMode = compression.mode || '';
        this.compressionStage = compression.stage || '';
        this.compressionError = compression.error || '';
        this.compressionConversationId = this.compressionInProgress
          ? this.currentConversationId
          : null;
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
    await this.downloadWorkspacePath(path, 'file');
  },

  async downloadFolder(path) {
    if (!path) {
      this.fileHideContextMenu();
      return;
    }
    const segments = path.split('/').filter(Boolean);
    const folderName = segments.length ? segments.pop() : 'folder';
    await this.downloadWorkspacePath(path, 'folder', `${folderName}.zip`);
  },

  // 统一入口：桌面壳走宿主原生保存对话框，Android 走原生桥，浏览器走 blob 下载
  async downloadWorkspacePath(path, kind = 'file', filename = '') {
    const outcome = await downloadWorkspaceFile({
      path,
      kind,
      filename: filename || undefined
    });
    if (outcome.status === 'failed') {
      this.uiPushToast({
        title: t('common.downloadFailed'),
        message: outcome.error || t('appTasks.cannotCompleteDownload'),
        type: 'error'
      });
    }
    this.fileHideContextMenu();
  },

  // 兼容旧调用：从 /api/download/xxx?path=... 解析出工作区路径后走统一入口
  async downloadResource(url, filename) {
    const match = /[?&]path=([^&]+)/.exec(url || '');
    const path = match ? decodeURIComponent(match[1]) : '';
    const kind = (url || '').includes('/api/download/folder') ? 'folder' : 'file';
    await this.downloadWorkspacePath(path, kind, filename);
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

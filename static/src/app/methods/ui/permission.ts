// @ts-nocheck
import { debugLog } from '../common';
import { t, currentLocale } from '@/locales';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const permissionMethods = {
  applyPolicyUiLocks() {
    const policyStore = usePolicyStore();
    const blocks = policyStore.uiBlocks;
    if (blocks.collapse_workspace) {
      this.uiSetWorkspaceCollapsed(true);
    }
    if (blocks.block_virtual_monitor && this.chatDisplayMode === 'monitor') {
      this.uiSetChatDisplayMode('chat');
    }
  },
  isPolicyBlocked(key: string, message?: string) {
    const policyStore = usePolicyStore();
    if (policyStore.uiBlocks[key]) {
      this.uiPushToast({
        title: t('appUi.disabledByAdmin'),
        message: message || t('appUi.forceDisabledByAdmin'),
        type: 'warning'
      });
      return true;
    }
    return false;
  },
  getPermissionModeLabel(mode) {
    void currentLocale.value;
    const options = Array.isArray(this.permissionModeOptions) ? this.permissionModeOptions : [];
    const hit = options.find((item) => item.value === mode);
    return hit ? hit.label : mode || t('appUi.unknown');
  },
  getExecutionModeLabel(mode) {
    void currentLocale.value;
    const options = Array.isArray(this.executionModeOptions) ? this.executionModeOptions : [];
    const hit = options.find((item) => item.value === mode);
    return hit ? hit.label : mode || t('appUi.unknown');
  },
  async changePermissionMode(mode) {
    const target = String(mode || '')
      .trim()
      .toLowerCase();
    if (!target) {
      this.closePermissionMenu();
      return;
    }
    try {
      const response = await fetch('/api/permission-mode', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          mode: target,
          // 对话级隔离：携带当前对话 ID，让后端把模式设置到对话级 terminal
          // （任务实际运行的实例），并持久化到当前对话 metadata；
          // /new 页面无对话时回退到工作区级 terminal（新对话创建时继承）。
          ...(this.currentConversationId ? { conversation_id: this.currentConversationId } : {})
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.message || payload?.error || t('appUi.switchPermissionFailed'));
      }
      if (typeof payload?.mode === 'string') {
        this.currentPermissionMode = payload.mode;
      }
      // readonly 联动：后端在切到只读时会强制执行环境切到沙箱，同步前端显示
      const execState = payload?.state || {};
      if (typeof execState.mode === 'string') {
        this.currentExecutionMode = execState.mode;
      }
      this.pendingPermissionMode = '';
      this.uiPushToast({
        title: t('appUi.permissionUpdated'),
        message: payload?.message || t('appUi.appliedImmediately'),
        type: 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.switchPermissionFailed'));
      this.uiPushToast({
        title: t('appUi.switchPermissionFailed'),
        message: msg,
        type: 'error'
      });
    } finally {
      this.closePermissionMenu();
    }
  },
  async changeExecutionMode(mode) {
    const target = String(mode || '').trim().toLowerCase();
    if (!this.executionModeEnabled || !target) {
      return;
    }
    try {
      const response = await fetch('/api/execution-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: target,
          // 对话级隔离：携带当前对话 ID，让后端把模式设置到对话级 terminal
          // （任务实际运行的实例），并持久化到当前对话 metadata；
          // /new 页面无对话时回退到工作区级 terminal（新对话创建时继承）。
          ...(this.currentConversationId ? { conversation_id: this.currentConversationId } : {})
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.message || payload?.error || t('appUi.switchExecutionModeFailed'));
      }
      const state = payload?.state || {};
      if (typeof state.mode === 'string') {
        this.currentExecutionMode = state.mode;
      }
      this.pendingExecutionMode = '';
      this.uiPushToast({
        title: t('appUi.executionModeUpdated'),
        message: payload?.message || t('appUi.appliedImmediately'),
        type: this.currentExecutionMode === 'direct' ? 'warning' : 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.switchExecutionModeFailed'));
      this.uiPushToast({
        title: t('appUi.switchExecutionModeFailed'),
        message: msg,
        type: 'error'
      });
    }
  },
  async handleSwitchPermissionToUnrestricted(approvalId) {
    await this.changePermissionMode('unrestricted');
    if (approvalId) {
      await this.approveToolApproval(approvalId);
    }
  },
  async changeNetworkPermission(mode) {
    const target = String(mode || '').trim().toLowerCase();
    if (!this.networkPermissionEnabled || !target) {
      return;
    }
    try {
      const response = await fetch('/api/network-permission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: target,
          // 对话级隔离：携带当前对话 ID，让后端把模式设置到对话级 terminal
          // （任务实际运行的实例），并持久化到当前对话 metadata；
          // /new 页面无对话时回退到工作区级 terminal（新对话创建时继承）。
          ...(this.currentConversationId ? { conversation_id: this.currentConversationId } : {})
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.message || payload?.error || t('appUi.switchNetworkPermissionFailed'));
      }
      if (typeof payload.mode === 'string') {
        this.currentNetworkPermission = payload.mode;
      }
      this.pendingNetworkPermission = typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
      const labelMap: Record<string, string> = { restricted: t('appUi.networkRestricted'), full: t('appUi.networkFull') };
      this.uiPushToast({
        title: t('appUi.networkPermissionUpdated'),
        message: payload?.message || t('appUi.switchedToMode', { mode: labelMap[this.currentNetworkPermission] || this.currentNetworkPermission }),
        type: this.currentNetworkPermission === 'full' ? 'warning' : 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.switchNetworkPermissionFailed'));
      this.uiPushToast({
        title: t('appUi.switchNetworkPermissionFailed'),
        message: msg,
        type: 'error'
      });
    }
  },
  async fetchNetworkPermission() {
    try {
      const query = this.currentConversationId
        ? `?conversation_id=${encodeURIComponent(this.currentConversationId)}`
        : '';
      const response = await fetch(`/api/network-permission${query}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      this.networkPermissionEnabled = !!payload.enabled;
      if (typeof payload.mode === 'string') {
        this.currentNetworkPermission = payload.mode;
      }
      this.pendingNetworkPermission = typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
    } catch (_error) {
      // ignore
    }
  },
  async fetchPermissionMode() {
    try {
      const query = this.currentConversationId
        ? `?conversation_id=${encodeURIComponent(this.currentConversationId)}`
        : '';
      const response = await fetch(`/api/permission-mode${query}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      if (typeof payload.mode === 'string') {
        this.currentPermissionMode = payload.mode;
      }
      this.pendingPermissionMode = typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
    } catch (_error) {
      // ignore
    }
  },
  async fetchExecutionMode() {
    try {
      const query = this.currentConversationId
        ? `?conversation_id=${encodeURIComponent(this.currentConversationId)}`
        : '';
      const response = await fetch(`/api/execution-mode${query}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      this.executionModeEnabled = !!payload.enabled;
      const state = payload.state || {};
      if (typeof state.mode === 'string') {
        this.currentExecutionMode = state.mode;
      }
      this.pendingExecutionMode = typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
    } catch (_error) {
      // ignore
    }
  },
  async openPathAuthorizationDialog() {
    if (!this.executionModeEnabled) return;
    this.pathAuthorizationDialogOpen = true;
    try {
      const response = await fetch('/api/path-authorization');
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload?.success) {
        const writablePaths = Array.isArray(payload.writable_paths) ? payload.writable_paths : [];
        const readableExtraPaths = Array.isArray(payload.readable_extra_paths)
          ? payload.readable_extra_paths
          : [];
        this.pathAuthorizationWritableDraft = writablePaths.join('\n');
        this.pathAuthorizationReadableDraft = readableExtraPaths.join('\n');
        this.pathAuthorizationMode = 'writable';
        this.pathAuthorizationDraft = this.pathAuthorizationWritableDraft;
      }
    } catch (_error) {
      // ignore
    }
  },
  setPathAuthorizationMode(mode) {
    if (this.pathAuthorizationMode === 'readable') {
      this.pathAuthorizationReadableDraft = String(this.pathAuthorizationDraft || '');
    } else {
      this.pathAuthorizationWritableDraft = String(this.pathAuthorizationDraft || '');
    }
    const next = mode === 'readable' ? 'readable' : 'writable';
    this.pathAuthorizationMode = next;
    this.pathAuthorizationDraft =
      next === 'readable' ? this.pathAuthorizationReadableDraft : this.pathAuthorizationWritableDraft;
  },
  closePathAuthorizationDialog() {
    this.pathAuthorizationDialogOpen = false;
  },
  async savePathAuthorization() {
    const currentLines = String(this.pathAuthorizationDraft || '')
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean);
    if (this.pathAuthorizationMode === 'readable') {
      this.pathAuthorizationReadableDraft = currentLines.join('\n');
    } else {
      this.pathAuthorizationWritableDraft = currentLines.join('\n');
    }
    const writableLines = String(this.pathAuthorizationWritableDraft || '')
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean);
    const readableLines = String(this.pathAuthorizationReadableDraft || '')
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean);
    this.pathAuthorizationSaving = true;
    try {
      const response = await fetch('/api/path-authorization', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          writable_paths: writableLines,
          readable_extra_paths: readableLines
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || t('appUi.saveFailed'));
      }
      const savedWritable = Array.isArray(payload.writable_paths) ? payload.writable_paths : writableLines;
      const savedReadable = Array.isArray(payload.readable_extra_paths)
        ? payload.readable_extra_paths
        : readableLines;
      this.pathAuthorizationWritableDraft = savedWritable.join('\n');
      this.pathAuthorizationReadableDraft = savedReadable.join('\n');
      this.pathAuthorizationDraft =
        this.pathAuthorizationMode === 'readable'
          ? this.pathAuthorizationReadableDraft
          : this.pathAuthorizationWritableDraft;
      this.uiPushToast({
        title: t('appUi.pathAuthorizationSaved'),
        message: t('appUi.pathAuthApplyMessage'),
        type: 'success'
      });
      this.pathAuthorizationDialogOpen = false;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.saveFailed'));
      this.uiPushToast({ title: t('appUi.savePathAuthorizationFailed'), message: msg, type: 'error' });
    } finally {
      this.pathAuthorizationSaving = false;
    }
  },
  async fetchPendingToolApprovals() {
    if (!this.currentConversationId) {
      this.pendingToolApprovals = [];
      return;
    }
    try {
      const response = await fetch(
        `/api/tool-approvals/pending?conversation_id=${encodeURIComponent(this.currentConversationId)}`
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      this.pendingToolApprovals = items;
      // 自动审核模式 + 个人空间开启「隐藏工具审核面板」时，不自动展开审核面板
      const hideApprovalPanel =
        this.currentPermissionMode === 'auto_approval' &&
        usePersonalizationStore().form.hide_tool_approval_panel !== false;
      // 电脑端：有审批时自动展开面板
      if (items.length > 0 && !this.isMobileViewport && !hideApprovalPanel) {
        this.rightCollapsed = false;
        if (this.rightWidth < this.minPanelWidth) {
          this.rightWidth = this.minPanelWidth;
        }
      }
    } catch (_error) {
      // ignore
    }
  },
  togglePermissionMenu() {
    if (!this.isConnected) {
      return;
    }
    this.permissionMenuOpen = !this.permissionMenuOpen;
  },
  closePermissionMenu() {
    this.permissionMenuOpen = false;
  }
};

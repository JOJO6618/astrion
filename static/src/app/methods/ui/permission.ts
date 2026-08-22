// @ts-nocheck
import { debugLog } from '../common';
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
        title: '已被管理员禁用',
        message: message || '被管理员强制禁用',
        type: 'warning'
      });
      return true;
    }
    return false;
  },
  getPermissionModeLabel(mode) {
    const options = Array.isArray(this.permissionModeOptions) ? this.permissionModeOptions : [];
    const hit = options.find((item) => item.value === mode);
    return hit ? hit.label : mode || '未知';
  },
  getExecutionModeLabel(mode) {
    const options = Array.isArray(this.executionModeOptions) ? this.executionModeOptions : [];
    const hit = options.find((item) => item.value === mode);
    return hit ? hit.label : mode || '未知';
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
        throw new Error(payload?.message || payload?.error || '切换权限失败');
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
        title: '权限已更新',
        message: payload?.message || '已立即生效',
        type: 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '切换权限失败');
      this.uiPushToast({
        title: '切换权限失败',
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
        throw new Error(payload?.message || payload?.error || '切换执行环境失败');
      }
      const state = payload?.state || {};
      if (typeof state.mode === 'string') {
        this.currentExecutionMode = state.mode;
      }
      this.pendingExecutionMode = '';
      this.uiPushToast({
        title: '执行环境已更新',
        message: payload?.message || '已立即生效',
        type: this.currentExecutionMode === 'direct' ? 'warning' : 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '切换执行环境失败');
      this.uiPushToast({
        title: '切换执行环境失败',
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
        throw new Error(payload?.message || payload?.error || '切换网络权限失败');
      }
      if (typeof payload.mode === 'string') {
        this.currentNetworkPermission = payload.mode;
      }
      this.pendingNetworkPermission = typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
      const labelMap: Record<string, string> = { restricted: '受限', full: '完全开放' };
      this.uiPushToast({
        title: '网络权限已更新',
        message: payload?.message || `已切换为 ${labelMap[this.currentNetworkPermission] || this.currentNetworkPermission}`,
        type: this.currentNetworkPermission === 'full' ? 'warning' : 'info',
        duration: 1800
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '切换网络权限失败');
      this.uiPushToast({
        title: '切换网络权限失败',
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
        throw new Error(payload?.error || '保存失败');
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
        title: '路径授权已保存',
        message: '命令工具立即生效；终端会话请重开后生效',
        type: 'success'
      });
      this.pathAuthorizationDialogOpen = false;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '保存失败');
      this.uiPushToast({ title: '保存路径授权失败', message: msg, type: 'error' });
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

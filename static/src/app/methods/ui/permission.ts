// @ts-nocheck
import { t, currentLocale } from '@/locales';
import {
  isFullAccessApproval,
  needsHumanDecision,
  normalizeApproval
} from '@/components/input/approvalModel';
import { usePolicyStore } from '../../../stores/policy';
import { usePersonalizationStore } from '../../../stores/personalization';

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
    return hit ? t(hit.labelKey) : mode || t('appUi.unknown');
  },
  getExecutionModeLabel(mode) {
    void currentLocale.value;
    const options = Array.isArray(this.executionModeOptions) ? this.executionModeOptions : [];
    const hit = options.find((item) => item.value === mode);
    return hit ? t(hit.labelKey) : mode || t('appUi.unknown');
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
      const msg =
        error instanceof Error ? error.message : String(error || t('appUi.switchPermissionFailed'));
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
    const target = String(mode || '')
      .trim()
      .toLowerCase();
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
      const msg =
        error instanceof Error
          ? error.message
          : String(error || t('appUi.switchExecutionModeFailed'));
      this.uiPushToast({
        title: t('appUi.switchExecutionModeFailed'),
        message: msg,
        type: 'error'
      });
    }
  },
  async changeNetworkPermission(mode) {
    const target = String(mode || '')
      .trim()
      .toLowerCase();
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
        throw new Error(
          payload?.message || payload?.error || t('appUi.switchNetworkPermissionFailed')
        );
      }
      if (typeof payload.mode === 'string') {
        this.currentNetworkPermission = payload.mode;
      }
      this.pendingNetworkPermission =
        typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
      const labelMap: Record<string, string> = {
        restricted: t('appUi.networkRestricted'),
        full: t('appUi.networkFull')
      };
      this.uiPushToast({
        title: t('appUi.networkPermissionUpdated'),
        message:
          payload?.message ||
          t('appUi.switchedToMode', {
            mode: labelMap[this.currentNetworkPermission] || this.currentNetworkPermission
          }),
        type: this.currentNetworkPermission === 'full' ? 'warning' : 'info',
        duration: 1800
      });
    } catch (error) {
      const msg =
        error instanceof Error
          ? error.message
          : String(error || t('appUi.switchNetworkPermissionFailed'));
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
      this.pendingNetworkPermission =
        typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
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
      this.pendingPermissionMode =
        typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
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
      this.pendingExecutionMode =
        typeof payload.pending_mode === 'string' ? payload.pending_mode : '';
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
        const wsWritable = Array.isArray(payload.workspace_writable_paths)
          ? payload.workspace_writable_paths
          : [];
        const wsReadable = Array.isArray(payload.workspace_readable_extra_paths)
          ? payload.workspace_readable_extra_paths
          : [];
        this.pathAuthorizationWritableDraft = writablePaths.join('\n');
        this.pathAuthorizationReadableDraft = readableExtraPaths.join('\n');
        this.pathAuthorizationWorkspaceWritableDraft = wsWritable.join('\n');
        this.pathAuthorizationWorkspaceReadableDraft = wsReadable.join('\n');
        this.pathAuthorizationWorkspacePath =
          typeof payload.workspace_path === 'string' ? payload.workspace_path : '';
        // 默认选中「当前工作区」作用域（最小权限原则）
        this.pathAuthorizationScope = 'workspace';
        this.pathAuthorizationMode = 'writable';
        this.pathAuthorizationDraft = this.pathAuthorizationWorkspaceWritableDraft;
      }
    } catch (_error) {
      // ignore
    }
  },
  _stashPathAuthorizationDraft() {
    // 切换作用域/类型前把 textarea 当前内容暂存到对应草稿槽，切来切去不丢未保存内容
    const text = String(this.pathAuthorizationDraft || '');
    if (this.pathAuthorizationScope === 'workspace') {
      if (this.pathAuthorizationMode === 'readable') {
        this.pathAuthorizationWorkspaceReadableDraft = text;
      } else {
        this.pathAuthorizationWorkspaceWritableDraft = text;
      }
    } else if (this.pathAuthorizationMode === 'readable') {
      this.pathAuthorizationReadableDraft = text;
    } else {
      this.pathAuthorizationWritableDraft = text;
    }
  },
  _currentPathAuthorizationDraft() {
    if (this.pathAuthorizationScope === 'workspace') {
      return this.pathAuthorizationMode === 'readable'
        ? this.pathAuthorizationWorkspaceReadableDraft
        : this.pathAuthorizationWorkspaceWritableDraft;
    }
    return this.pathAuthorizationMode === 'readable'
      ? this.pathAuthorizationReadableDraft
      : this.pathAuthorizationWritableDraft;
  },
  setPathAuthorizationMode(mode) {
    this._stashPathAuthorizationDraft();
    this.pathAuthorizationMode = mode === 'readable' ? 'readable' : 'writable';
    this.pathAuthorizationDraft = this._currentPathAuthorizationDraft();
  },
  setPathAuthorizationScope(scope) {
    this._stashPathAuthorizationDraft();
    this.pathAuthorizationScope = scope === 'global' ? 'global' : 'workspace';
    this.pathAuthorizationDraft = this._currentPathAuthorizationDraft();
  },
  closePathAuthorizationDialog() {
    this.pathAuthorizationDialogOpen = false;
  },
  async savePathAuthorization() {
    this._stashPathAuthorizationDraft();
    const isWorkspace = this.pathAuthorizationScope === 'workspace';
    const toLines = (text) =>
      String(text || '')
        .split('\n')
        .map((x) => x.trim())
        .filter(Boolean);
    const writableLines = toLines(
      isWorkspace
        ? this.pathAuthorizationWorkspaceWritableDraft
        : this.pathAuthorizationWritableDraft
    );
    const readableLines = toLines(
      isWorkspace
        ? this.pathAuthorizationWorkspaceReadableDraft
        : this.pathAuthorizationReadableDraft
    );
    if (isWorkspace) {
      this.pathAuthorizationWorkspaceWritableDraft = writableLines.join('\n');
      this.pathAuthorizationWorkspaceReadableDraft = readableLines.join('\n');
    } else {
      this.pathAuthorizationWritableDraft = writableLines.join('\n');
      this.pathAuthorizationReadableDraft = readableLines.join('\n');
    }
    this.pathAuthorizationSaving = true;
    try {
      const response = await fetch('/api/path-authorization', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: isWorkspace ? 'workspace' : 'global',
          writable_paths: writableLines,
          readable_extra_paths: readableLines
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || t('appUi.saveFailed'));
      }
      // 响应带回两个作用域的最新值，全部同步回草稿槽
      const savedWritable = Array.isArray(payload.writable_paths) ? payload.writable_paths : [];
      const savedReadable = Array.isArray(payload.readable_extra_paths)
        ? payload.readable_extra_paths
        : [];
      const savedWsWritable = Array.isArray(payload.workspace_writable_paths)
        ? payload.workspace_writable_paths
        : [];
      const savedWsReadable = Array.isArray(payload.workspace_readable_extra_paths)
        ? payload.workspace_readable_extra_paths
        : [];
      this.pathAuthorizationWritableDraft = savedWritable.join('\n');
      this.pathAuthorizationReadableDraft = savedReadable.join('\n');
      this.pathAuthorizationWorkspaceWritableDraft = savedWsWritable.join('\n');
      this.pathAuthorizationWorkspaceReadableDraft = savedWsReadable.join('\n');
      this.pathAuthorizationDraft = this._currentPathAuthorizationDraft();
      this.uiPushToast({
        title: t('appUi.pathAuthorizationSaved'),
        message: t('appUi.pathAuthApplyMessage'),
        type: 'success'
      });
      this.pathAuthorizationDialogOpen = false;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.saveFailed'));
      this.uiPushToast({
        title: t('appUi.savePathAuthorizationFailed'),
        message: msg,
        type: 'error'
      });
    } finally {
      this.pathAuthorizationSaving = false;
    }
  },
  async fetchPendingToolApprovals() {
    if (!this.currentConversationId) {
      this.pendingToolApprovals = [];
      return;
    }
    const conversationId = this.currentConversationId;
    const snapshotVersion = this.approvalSnapshotVersion;
    try {
      const response = await fetch(
        `/api/tool-approvals/pending?conversation_id=${encodeURIComponent(conversationId)}`
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      if (
        conversationId !== this.currentConversationId ||
        snapshotVersion !== this.approvalSnapshotVersion
      )
        return;
      const resolvedIds = this.resolvedToolApprovalIds || [];
      const items = (Array.isArray(payload.items) ? payload.items : []).filter(
        (item) => !resolvedIds.includes(item.approval_id)
      );
      const previous = this.pendingToolApprovals || [];
      this.pendingToolApprovals = items.map((item) =>
        normalizeApproval(
          item,
          previous.find((entry) => entry.approval_id === item.approval_id),
          this.currentPermissionMode === 'auto_approval'
        )
      );
      const hideApprovalPanel =
        this.currentPermissionMode === 'auto_approval' &&
        usePersonalizationStore().form.hide_tool_approval_panel !== false;
      const mandatory = this.pendingToolApprovals.some(
        (item) => isFullAccessApproval(item) && needsHumanDecision(item)
      );
      const isNewRequest = items.some(
        (item) => !previous.some((entry) => entry.approval_id === item.approval_id)
      );
      const newlyMandatory =
        mandatory &&
        !previous.some((item) => isFullAccessApproval(item) && needsHumanDecision(item));
      if (items.length && (isNewRequest || newlyMandatory) && (!hideApprovalPanel || mandatory)) {
        this.restoreToolApprovalPanel();
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

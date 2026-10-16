// @ts-nocheck
import { t } from '@/locales';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { conversationViewEpoch } from '../conversation/session';

/** The caller owns navigation; this request never selects a conversation. */
export async function requestNewConversationView(workspaceId, previousConversationId, signal) {
  const response = await fetch('/api/new-conversation/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Astrion-Workspace-Id': workspaceId },
    signal,
    body: JSON.stringify({
      workspace_id: workspaceId,
      previous_conversation_id: previousConversationId || null
    })
  });
  const result = await response.json();
  if (!response.ok || !result.success || !result.data?.settings)
    throw new Error(result.error || t('appUi.switchFailed'));
  return result.data;
}

export function captureNewConversationSettings(host) {
  return {
    model_key: host.currentModelKey,
    run_mode: host.runMode,
    thinking_mode: host.runMode === 'thinking',
    reasoning_effort: host.reasoningEffort ?? null,
    work_mode: host.currentWorkMode,
    permission_mode: host.currentPermissionMode,
    execution_mode: host.currentExecutionMode,
    network_permission: host.currentNetworkPermission,
    restore_modes: { ...host.newConversationRestoreModes }
  };
}

export const newConversationMethods = {
  applyNewConversationView(view) {
    const settings = view.settings;
    this.modelSet(settings.model_key || '');
    this.runMode = settings.run_mode;
    this.thinkingMode = settings.thinking_mode;
    this.reasoningEffort = settings.reasoning_effort;
    this.currentWorkMode = settings.work_mode;
    this.currentPermissionMode = settings.permission_mode;
    this.currentExecutionMode = settings.execution_mode;
    this.currentNetworkPermission = settings.network_permission;
    this.newConversationRestoreModes = { ...settings.restore_modes };
    this.pendingPermissionMode = '';
    this.pendingExecutionMode = '';
    this.pendingNetworkPermission = '';
    this.executionModeEnabled = !!view.execution_mode_enabled;
    this.networkPermissionEnabled = !!view.network_permission_enabled;
    this.newConversationSettingsReady = true;
  },
  async loadNewConversationSettings() {
    const epoch = conversationViewEpoch(this);
    const workspaceId = String(this.currentHostWorkspaceId || '');
    if (!workspaceId) return;
    this.newConversationSettingsReady = false;
    const view = await requestNewConversationView(workspaceId, null);
    if (
      conversationViewEpoch(this) !== epoch ||
      this.currentConversationId ||
      workspaceId !== this.currentHostWorkspaceId
    )
      return;
    this.applyNewConversationView(view);
  },
  /** /new selections belong to this frontend until the first message is sent. */
  setNewConversationMode(kind, target) {
    const restore = { ...this.newConversationRestoreModes };
    if (kind === 'work') {
      if (target === 'plan' && this.currentWorkMode !== 'plan') {
        restore.pre_plan_permission_mode = this.currentPermissionMode;
        restore.pre_plan_execution_mode = this.currentExecutionMode;
        // Preserve the execution choice across plan -> unrestricted as well.
        if (this.currentPermissionMode === 'unrestricted')
          restore.pre_readonly_execution_mode = this.currentExecutionMode;
        this.currentPermissionMode = 'readonly';
        this.currentExecutionMode = 'sandbox';
      } else if (this.currentWorkMode === 'plan' && target !== 'plan') {
        this.currentPermissionMode = restore.pre_plan_permission_mode || 'approval';
        if (this.currentPermissionMode === 'unrestricted')
          this.currentExecutionMode = restore.pre_plan_execution_mode || 'sandbox';
        else this.currentExecutionMode = 'sandbox';
        restore.pre_plan_permission_mode = null;
        restore.pre_plan_execution_mode = null;
      }
      this.currentWorkMode = target;
    } else if (kind === 'permission') {
      if (this.currentWorkMode === 'plan') return;
      if (target !== 'unrestricted' && this.currentPermissionMode === 'unrestricted') {
        restore.pre_readonly_execution_mode = this.currentExecutionMode;
        this.currentExecutionMode = 'sandbox';
      } else if (target === 'unrestricted' && this.currentPermissionMode !== 'unrestricted') {
        if (restore.pre_readonly_execution_mode === 'direct') this.currentExecutionMode = 'direct';
        restore.pre_readonly_execution_mode = null;
      }
      this.currentPermissionMode = target;
    } else if (kind === 'execution') {
      if (this.currentWorkMode === 'plan' || this.currentPermissionMode !== 'unrestricted') return;
      this.currentExecutionMode = target;
    } else if (kind === 'network') {
      this.currentNetworkPermission = target;
    }
    this.newConversationRestoreModes = restore;
  }
};

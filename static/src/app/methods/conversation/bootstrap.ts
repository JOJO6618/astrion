// @ts-nocheck
import { debugLog } from '../common';
import { parseSystemNoticeLabel } from '../ui/shared';
import { displayMessageTime } from './display';
import { useTaskStore } from '../../../stores/task';
import { useQuickDockStore } from '../../../stores/quickDock';
import { usePreviewStore } from '../../../stores/preview';
import { useConversationStore } from '../../../stores/conversation';
import { useWorkflowStore } from '../../../stores/workflow';
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { usePersonalizationStore } from '../../../stores/personalization';
import {
  isFullAccessApproval,
  needsHumanDecision,
  normalizeApproval
} from '@/components/input/approvalModel';
import { bindAuxiliaryConversationHost } from '../auxiliaryOwnership';
import {
  beginConversationSession,
  currentConversationSession,
  leaveConversationSession,
  ownsConversationSession
} from './session';

const snapshotRequests = new WeakMap<object, object>();

export const bootstrapMethods = {
  beginConversationView(conversationId, workspaceId = '') {
    const session = beginConversationSession(
      this,
      conversationId,
      workspaceId || this.currentHostWorkspaceId || ''
    );
    bindAuxiliaryConversationHost(this);
    useTaskStore().clearTask();
    this.historyLoading = true;
    this.historyLoadingFor = conversationId;
    return session;
  },
  leaveConversationView() {
    leaveConversationSession(this);
    useTaskStore().clearTask();
    this.historyLoading = false;
    this.historyLoadingFor = null;
    this.stopRunningStateReconcile?.();
  },
  async refreshConversationSnapshot() {
    const session = currentConversationSession(this);
    if (!session || !ownsConversationSession(this, session)) return;
    if (this.historyLoading || session.submission) return;
    const id = this.currentConversationId;
    if (!id || session.conversationId !== id) return;
    return this.enterConversation(id, {
      session,
      workspaceId: this.currentHostWorkspaceId || '',
      urlMode: 'none',
      source: 'sync'
    });
  },
  async enterConversation(conversationId, options = {}) {
    const { workspaceId = '', urlMode = 'push', preserveListPosition = false } = options;
    const session = options.session || this.beginConversationView(conversationId, workspaceId);
    const syncing = options.source === 'sync';
    if (!ownsConversationSession(this, session) || (syncing && session.submission)) {
      return { success: false, superseded: true };
    }
    const request = {};
    const submissionVersion = session.submissionVersion;
    snapshotRequests.set(this, request);
    const ownsRequest = () =>
      ownsConversationSession(this, session) && snapshotRequests.get(this) === request;
    const owns = () =>
      ownsRequest() &&
      (!syncing || (!session.submission && session.submissionVersion === submissionVersion));
    this.historyLoading = true;
    this.historyLoadingFor = conversationId;
    const wsQuery = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    try {
      const response = await fetch(`/api/conversations/${conversationId}/bootstrap${wsQuery}`, {
        signal: session.controller.signal
      });
      const result = await response.json();
      if (!owns()) return { success: false, superseded: true };
      if (!response.ok || !result.success) return result;
      const data = result.data || {};
      const meta = data.meta || {};
      const normalizedId = data.conversation_id || conversationId;
      const display = data.display || {};
      const historyState = this.buildHistoryState(
        Array.isArray(data.messages) ? data.messages : [],
        normalizedId
      );
      const live = Array.isArray(display.messages) ? display.messages : [];
      for (const message of live) {
        if (message.role === 'user') {
          message.created_at = displayMessageTime(message.created_at ?? message.timestamp);
        }
        for (const action of message.actions || []) {
          if (action.type === 'system') action.content = parseSystemNoticeLabel(action.content);
        }
      }
      const messages = [...historyState.messages, ...live];
      const preserveInteraction = syncing && this.currentConversationId === normalizedId;
      // Keep the existing subscription while fetching; replace it only at commit.
      useTaskStore().clearTask();
      this.clearLocalTaskUiState?.(`snapshot:${normalizedId}`);
      session.conversationId = normalizedId;
      if (meta.run_mode) {
        this.runMode = meta.run_mode === 'deep' ? 'thinking' : meta.run_mode;
        this.thinkingMode =
          typeof meta.thinking_mode === 'boolean' ? meta.thinking_mode : this.runMode !== 'fast';
      }
      if (meta.model_key) this.modelSet(meta.model_key);
      if (meta.work_mode) this.currentWorkMode = meta.work_mode;
      if (meta.permission_mode) this.currentPermissionMode = meta.permission_mode;
      if (meta.execution_mode) this.currentExecutionMode = meta.execution_mode;
      if (meta.network_permission) this.currentNetworkPermission = meta.network_permission;
      this.reasoningEffort =
        typeof meta.reasoning_effort === 'string' ? meta.reasoning_effort : null;
      this.currentConversationType = meta.multi_agent_mode ? 'multi_agent' : 'normal';
      useConversationStore().$patch({ multiAgentMode: !!meta.multi_agent_mode });
      this.currentConversationId = normalizedId;
      this.messages = messages;
      this.conversationHasImages = historyState.hasImages || live.some((m) => m.images?.length);
      this.conversationHasVideos = historyState.hasVideos || live.some((m) => m.videos?.length);
      this.currentMessageIndex =
        messages.length && messages[messages.length - 1].role === 'assistant'
          ? messages.length - 1
          : -1;
      this.currentConversationTitle = meta.title || '';
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.startTitleTyping?.(this.currentConversationTitle, { animate: false });
      this.hydrateRuntimeDisplay(display, data.running || {}, preserveInteraction);
      useWorkflowStore().setWorkflow(null, false);
      this.runtimeQueuePaused = !!data.runtime_queue?.paused;
      this.applyRuntimeQueuedMessages(data.runtime_queue?.messages || []);
      this.handleCompressionState(
        data.compression || { conversation_id: normalizedId, in_progress: false }
      );
      useQuickDockStore().setEditedFiles(data.edited_files || []);
      usePreviewStore().setRuntime(data.preview_base, data.preview_token);
      usePreviewStore().setTargets(data.preview_targets || [], false);
      if (!preserveListPosition) this.promoteConversationToTop(normalizedId);
      if (urlMode !== 'none') {
        history[urlMode === 'replace' ? 'replaceState' : 'pushState'](
          { conversationId: normalizedId },
          '',
          `/${this.stripConversationPrefix(normalizedId)}`
        );
      }
      this.historyLoading = false;
      this.historyLoadingFor = null;
      this.refreshBlankHeroState();
      const task = display.task;
      if (task && ['pending', 'running', 'cancel_requested'].includes(task.status)) {
        useTaskStore().attachSnapshot(task, display.next_event_idx || 0, (event) => {
          if (ownsConversationSession(this, session)) this.handleTaskEvent(event);
        });
      }
      this.startRunningStateReconcile();
      // Scroll and auxiliary panels never hold up the snapshot or live subscription.
      this.$nextTick(() => {
        if (!ownsConversationSession(this, session)) return;
        if (preserveInteraction) this.conditionalScrollToBottom?.();
        else this.scrollHistoryToBottomInstant();
      });
      void this.fetchConversationWorkflow(normalizedId, session);
      const tabsStore = useConversationTabsStore();
      if (tabsStore.enabled) {
        const wsId = workspaceId || this.currentHostWorkspaceId || '';
        const ws = (this.hostWorkspaces || []).find((item) => item.workspace_id === wsId);
        tabsStore.openConversationTab({
          conversationId: normalizedId,
          workspaceId: wsId,
          workspaceLabel: ws?.label || '',
          title: meta.title || ''
        });
      }
      return { success: true, title: meta.title || '', conversation_id: normalizedId };
    } catch (error) {
      if (!owns() || error?.name === 'AbortError') return { success: false, superseded: true };
      throw error;
    } finally {
      if (ownsRequest()) {
        snapshotRequests.delete(this);
        this.historyLoading = false;
        this.historyLoadingFor = null;
      }
    }
  },
  hydrateRuntimeDisplay(display, running, preserveInteraction = false) {
    const state = display.state || {};
    const previousApprovalIds = new Set(
      (this.pendingToolApprovals || []).map((item) => item.approval_id)
    );
    this.approvalSnapshotVersion += 1;
    this.resolvedToolApprovalIds = state.resolved_tool_approval_ids || [];
    this.pendingToolApprovals = (state.pending_tool_approvals || []).map((approval) =>
      normalizeApproval(approval, undefined, this.currentPermissionMode === 'auto_approval')
    );
    this.approvalReviewRecords = state.approval_review_records || [];
    this.pendingUserQuestions = state.pending_user_questions || [];
    this.pendingPlanApprovals = state.pending_plan_approvals || [];
    this.decidingApprovalIds = [];
    this.answeringUserQuestionIds = [];
    this.answeringPlanApprovalIds = [];
    if (this.approvalAutoCloseTimer) clearTimeout(this.approvalAutoCloseTimer);
    this.approvalAutoCloseTimer = null;
    this.restoreUserQuestionTitle?.();
    if (!preserveInteraction) {
      const hideAutomatic =
        this.currentPermissionMode === 'auto_approval' &&
        usePersonalizationStore().form.hide_tool_approval_panel !== false;
      this.approvalPanelCollapsed = !this.pendingToolApprovals.some(
        (approval) =>
          !hideAutomatic || (isFullAccessApproval(approval) && needsHumanDecision(approval))
      );
      this.userQuestionActiveIndex = 0;
      this.userQuestionMinimized = false;
      this.planApprovalMinimized = false;
    }
    if (
      preserveInteraction &&
      this.pendingToolApprovals.some(
        (approval) =>
          !previousApprovalIds.has(approval.approval_id) &&
          (this.currentPermissionMode !== 'auto_approval' ||
            usePersonalizationStore().form.hide_tool_approval_panel === false ||
            (isFullAccessApproval(approval) && needsHumanDecision(approval)))
      )
    )
      this.approvalPanelCollapsed = false;
    if (!this.pendingToolApprovals.length && (!preserveInteraction || previousApprovalIds.size)) {
      this.approvalPanelCollapsed = true;
    }
    this.userQuestionDialogVisible = this.pendingUserQuestions.length > 0;
    this.taskInProgress = !!running.is_truly_active;
    this.streamingMessage = !!state.streaming && !!running.is_main_running;
    this.waitingForSubAgent = !!running.has_running_sub_agents;
    this.waitingForBackgroundCommand = !!running.has_running_background_commands;
    this.apiRequestPending = !!state.api_request_pending;
    this.goalProgress = state.goal_progress || display.task?.goal_progress || null;
    this.goalRunning = !!state.goal_running && !!running.is_main_running;
    this._summaryToolBatchId = state.summary_tool_batch_id || '';
    this._summaryToolBatchSequence = state.summary_tool_batch_sequence || 0;
    for (const message of display.messages || []) {
      if (message.role !== 'assistant') continue;
      for (const action of message.actions || []) {
        if (action.type === 'thinking') {
          action.collapsed = true;
          if (action.streaming) this.chatSetThinkingLock?.(action.blockId || action.id, true);
        }
        if (action.type === 'tool' && action.tool) {
          action.tool.argumentSnapshot = this.cloneToolArguments(action.tool.arguments || {});
          action.tool.argumentLabel = this.buildToolLabel(action.tool.argumentSnapshot);
          for (const alias of [
            action.id,
            action.tool.id,
            action.tool.executionId,
            action.tool.execution_id,
            action.tool.preparingId,
            action.tool.preparing_id,
            action.tool.tool_call_id
          ]) {
            if (alias != null && alias !== '') this.toolRegisterAction(action, String(alias));
          }
          const status = String(action.tool.status || '').toLowerCase();
          if (running.is_main_running && status === 'preparing') {
            for (const alias of [
              action.tool.preparingId,
              action.tool.preparing_id,
              action.tool.id
            ]) {
              if (alias != null && alias !== '') this.preparingTools.set(String(alias), action);
            }
          }
          if (
            running.is_main_running &&
            ['preparing', 'running', 'pending', 'queued', 'awaiting_user_answer'].includes(status)
          ) {
            this.toolTrackAction(action.tool.name, action);
          }
        }
      }
    }
  },
  async fetchConversationWorkflow(conversationId, session) {
    try {
      const response = await fetch(
        `/api/workflow/status?conversation_id=${encodeURIComponent(conversationId)}`,
        {
          signal: session.controller.signal
        }
      );
      const data = await response.json();
      if (response.ok && ownsConversationSession(this, session)) {
        useWorkflowStore().setWorkflow(data?.snapshot, false);
      }
    } catch (error) {
      if (ownsConversationSession(this, session) && error?.name !== 'AbortError') {
        debugLog('conversation workflow unavailable', String(error));
      }
    }
  }
};

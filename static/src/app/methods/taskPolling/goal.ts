// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { getMessageVisibility, messageStartsWork } from '../../../utils/messageVisibility';
import {
  debugNotifyLog,
  keyNotifyLog,
  jsonDebug,
  userMDebug,
  isRestoreDebugEnabled,
  restoreDebugLog,
  isSystemAutoUserMessagePayload,
  isRuntimeModeNoticePayload,
  resolveUserMessageSource,
  resolveUserMessageMetadata,
  isEmptyAssistantPlaceholderMessage,
  getOptimisticUserEchoTarget,
  findRecentMatchingUserMessage,
} from './shared';

export const goalMethods = {
  handleGoalProgress(data: any) {
    // 安全检查：确保目标事件属于当前对话，防止切换对话后旧任务事件污染
    if (data?.conversation_id && this.currentConversationId && data.conversation_id !== this.currentConversationId) {
      debugLog('[Goal] 忽略不匹配对话的目标进度事件', { eventCid: data.conversation_id, currentCid: this.currentConversationId });
      return;
    }
    debugLog('[Goal] 目标进度更新', data);
    const status = String(data?.status || 'running').toLowerCase();
    this.goalRunning = status === 'running';
    this.goalModeArmed = false;
    this.goalProgress = {
      ...(data || {}),
      goal: data?.goal || this.goalProgress?.goal || '',
      status,
      turn_count: Number(data?.turn_count ?? 0),
      tokens_used: Number(data?.tokens_used ?? 0),
      tool_calls: Number(data?.tool_calls ?? 0),
      duration_seconds: Number(data?.duration_seconds ?? 0)
    };
  },
  handleGoalReviewProgress(data: any) {
    const progress = data?.progress || data || {};
    if (!progress || typeof progress !== 'object') {
      return;
    }
    if (!Array.isArray(this.autoApprovalFeedLines)) {
      this.autoApprovalFeedLines = [];
    }
    this.autoApprovalTitle = '目标审批';
    if (progress.stage === 'start') {
      this.autoApprovalFeedLines = ['开始审核'];
      this.autoApprovalFinalMessage = '';
    } else if (progress.stage === 'model_call') {
      this.autoApprovalFeedLines.push(String(progress.message || `审核轮次 ${progress.round || ''}`).trim());
    } else if (progress.stage === 'run_command' && progress.command) {
      this.autoApprovalFeedLines.push(String(progress.command));
    } else if (progress.message) {
      this.autoApprovalFeedLines.push(String(progress.message));
    }
    this.autoApprovalFeedLines = this.autoApprovalFeedLines.slice(-20);
    this.$forceUpdate();
  },
  scheduleGoalApprovalPanelAutoClose() {
    if (this.isMobileViewport || this.autoApprovalTitle !== '目标审批') {
      return;
    }
    if (this.approvalAutoCloseTimer) {
      clearTimeout(this.approvalAutoCloseTimer);
    }
    this.approvalAutoCloseTimer = setTimeout(() => {
      this.rightCollapsed = true;
    }, 3000);
  },
  handleGoalCompleted(data: any) {
    // 安全检查：确保目标事件属于当前对话
    if (data?.conversation_id && this.currentConversationId && data.conversation_id !== this.currentConversationId) {
      debugLog('[Goal] 忽略不匹配对话的目标完成事件', { eventCid: data.conversation_id, currentCid: this.currentConversationId });
      return;
    }
    debugLog('[Goal] 目标已达成', data);
    this.goalRunning = false;
    this.goalModeArmed = false;
    this.goalProgress = { ...(data || {}), status: 'done' };
    this.goalDialogOpen = true;
    this.scheduleGoalApprovalPanelAutoClose?.();
  },
  handleGoalStopped(data: any) {
    // 安全检查：确保目标事件属于当前对话
    if (data?.conversation_id && this.currentConversationId && data.conversation_id !== this.currentConversationId) {
      debugLog('[Goal] 忽略不匹配对话的目标停止事件', { eventCid: data.conversation_id, currentCid: this.currentConversationId });
      return;
    }
    debugLog('[Goal] 目标已停止', data);
    this.goalRunning = false;
    this.goalModeArmed = false;
    if (String(data?.stopped_reason || '').toLowerCase() === 'user_cancel') {
      this.goalProgress = null;
      this.goalDialogOpen = false;
      return;
    }
    this.goalProgress = { ...(data || {}), status: 'stopped' };
    // 停止也弹一次窗，告知用户原因与进度
    this.goalDialogOpen = true;
    this.scheduleGoalApprovalPanelAutoClose?.();
  }
};

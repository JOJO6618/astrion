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

export const placeholderMethods = {
  cleanupTrailingEmptyAssistantPlaceholder(reason = 'unspecified') {
    if (!Array.isArray(this.messages) || !this.messages.length) {
      return false;
    }
    const last = this.messages[this.messages.length - 1];
    if (!isEmptyAssistantPlaceholderMessage(last)) {
      return false;
    }
    this.messages.pop();
    if (typeof this.currentMessageIndex === 'number') {
      this.currentMessageIndex = this.messages.length - 1;
    }
    userMDebug('taskPolling.cleanupTrailingEmptyAssistantPlaceholder:removed', {
      reason,
      messagesLengthAfter: this.messages.length,
      currentMessageIndex: this.currentMessageIndex
    });
    return true;
  },
  moveTrailingEmptyAssistantPlaceholderAfterUserInsert(reason = 'runtime-user-insert') {
    if (!Array.isArray(this.messages) || !this.messages.length) {
      return false;
    }
    const last = this.messages[this.messages.length - 1];
    if (!isEmptyAssistantPlaceholderMessage(last)) {
      return false;
    }
    this.messages.pop();
    if (typeof this.currentMessageIndex === 'number') {
      this.currentMessageIndex = -1;
    }
    userMDebug('taskPolling.moveTrailingEmptyAssistantPlaceholderAfterUserInsert:removed-before-insert', {
      reason,
      messagesLengthAfterRemove: this.messages.length
    });
    return true;
  },
  ensureRunningAssistantPlaceholder(runningTask: any = null, reason = 'restore-running-task') {
    if (!Array.isArray(this.messages) || this.messages.length === 0) {
      return false;
    }
    const last = this.messages[this.messages.length - 1];
    const lastActions = Array.isArray(last?.actions) ? last.actions : [];
    const alreadyWaiting =
      last?.role === 'assistant' && lastActions.length === 0 && !!last.awaitingFirstContent;
    if (alreadyWaiting) {
      this.taskInProgress = true;
      this.streamingMessage = true;
      this.stopRequested = false;
      return false;
    }
    if (last?.role === 'assistant' && lastActions.length > 0) {
      return false;
    }
    if (last?.role !== 'user') {
      return false;
    }

    const startedAt =
      typeof runningTask?.created_at === 'number'
        ? new Date(runningTask.created_at * 1000).toISOString()
        : new Date().toISOString();
    last.metadata = {
      ...(last.metadata || {}),
      work_timer: last.metadata?.work_timer || {
        status: 'working',
        started_at: startedAt
      }
    };
    this.taskInProgress = true;
    this.stopRequested = false;
    this.streamingMessage = true;
    this.chatStartAssistantMessage();
    if (typeof this.monitorShowPendingReply === 'function') {
      this.monitorShowPendingReply();
    }
    this.$forceUpdate();
    this.$nextTick(() => {
      this.conditionalScrollToBottom?.();
    });
    debugLog('[TaskPolling] 已恢复等待首包占位', {
      reason,
      conversationId: this.currentConversationId,
      taskId: runningTask?.task_id || null
    });
    return true;
  },
  markLatestUserWorkCompleted() {
    if (!Array.isArray(this.messages) || this.messages.length === 0) {
      return;
    }
    for (let i = this.messages.length - 1; i >= 0; i -= 1) {
      const msg = this.messages[i];
      if (!msg || msg.role !== 'user') {
        continue;
      }
      msg.metadata = msg.metadata || {};
      const timer = msg.metadata.work_timer;
      if (!timer || typeof timer !== 'object') {
        continue;
      }
      if (timer.status === 'completed' && typeof timer.duration_ms === 'number') {
        return;
      }
      const nowIso = new Date().toISOString();
      const startedAt = timer.started_at || msg.timestamp || nowIso;
      const startMs = Date.parse(startedAt);
      const endMs = Date.now();
      const durationMs = Number.isFinite(startMs) ? Math.max(0, endMs - startMs) : 0;
      timer.status = 'completed';
      timer.started_at = startedAt;
      timer.finished_at = nowIso;
      timer.duration_ms = durationMs;
      return;
    }
  },
  isPlaceholderConversationTitle(title: any) {
    const normalized = String(title || '').trim();
    // 默认标题双语判等：zh '新对话' / en 'New Chat'（后端 modules/i18n.py conversation.default_title 按语言生成；\u 转义仅过审计）
    return !normalized || normalized === '\u65b0\u5bf9\u8bdd' || normalized === 'New Chat';
  }
};

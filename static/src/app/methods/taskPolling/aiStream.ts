// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import { currentConversationSession, ownsConversationSession } from '../conversation/session';

export function trackStreamAttempt(host: any, data: any, idx: number) {
  const msg = host.chatEnsureAssistantMessage();
  if (msg && data?.task_id && msg.taskId !== data.task_id) {
    msg.taskId = data.task_id;
    msg.id = `${data.task_id}:${idx}:assistant`;
  }
  const batch = host._summaryToolBatchId || '';
  if (msg && msg.streamAttemptBatchId !== batch) {
    msg.streamAttemptStart = msg.actions.length;
    msg.streamAttemptBatchId = batch;
  }
}

function identifyAction(host, action, data, idx, kind) {
  if (!action || !data?.task_id || !Number.isInteger(idx)) return;
  action.id = `${data.task_id}:${idx}:${kind}`;
  if (kind === 'thinking') {
    action.blockId = action.id;
    host.messages[host.currentMessageIndex].activeThinkingId = action.id;
  }
}

export const aiStreamMethods = {
  handleApiRequestStart() {
    const msg = this.messages[this.currentMessageIndex];
    if (msg?.role === 'assistant') {
      msg.streamAttemptStart = msg.actions.length;
      msg.streamAttemptBatchId = this._summaryToolBatchId || '';
    }
  },
  handleAiMessageStart(data: any, eventIdx: number) {
    let msg = this.messages[this.messages.length - 1];
    const ownsPlaceholder =
      msg?.role === 'assistant' &&
      msg.taskId === data.task_id &&
      msg.awaitingFirstContent &&
      !msg.actions?.length;
    if (!ownsPlaceholder) {
      this.monitorResetSpeech();
      this.chatStartAssistantMessage();
      msg = this.messages[this.messages.length - 1];
    }
    this.currentMessageIndex = this.messages.length - 1;
    msg.id = `${data.task_id}:${eventIdx}:assistant`;
    msg.taskId = data.task_id;
    msg.streamAttemptStart = msg.actions.length;
    msg.streamAttemptBatchId = this._summaryToolBatchId || '';
    msg.awaitingFirstContent = !msg.actions.length;
    msg.generatingLabel = msg.awaitingFirstContent ? t('appTasks.thinkingLabel') : '';
    this.waitingForSubAgent = false;
    this.taskInProgress = true;
    this.stopRequested = false;
    this.streamingMessage = true;
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  },
  handleThinkingStart(data: any, eventIdx: number) {
    if (this.runMode === 'fast' || this.thinkingMode === false) return;
    trackStreamAttempt(this, data, eventIdx);
    const result = this.chatStartThinkingAction();
    identifyAction(this, result?.action, data, eventIdx, 'thinking');
    const blockId = result?.action?.blockId;
    if (blockId) {
      this.chatExpandBlock(blockId);
      this.chatSetThinkingLock(blockId, true);
    }
    this.monitorShowThinking();
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  },
  handleThinkingChunk(data: any, eventIdx: number) {
    if (this.runMode === 'fast' || this.thinkingMode === false || !data?.content) return;
    trackStreamAttempt(this, data, eventIdx);
    let action = this.chatAppendThinkingChunk(data.content);
    if (!action) {
      const result = this.chatStartThinkingAction();
      identifyAction(this, result?.action, data, eventIdx, 'thinking');
      action = this.chatAppendThinkingChunk(data.content);
    }
    if (action?.blockId) this.scrollThinkingToBottom(action.blockId);
    this.monitorShowThinking();
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  },
  handleThinkingEnd(data: any) {
    if (this.runMode === 'fast' || this.thinkingMode === false) return;
    const blockId = this.chatCompleteThinkingAction(data?.full_content);
    if (blockId) {
      this.chatSetThinkingLock(blockId, false);
      const session = currentConversationSession(this);
      setTimeout(() => {
        if (!session || !ownsConversationSession(this, session)) return;
        this.chatCollapseBlock(blockId);
        this.$forceUpdate();
      }, 1000);
      this.scrollThinkingToBottom(blockId);
    }
    this.$forceUpdate();
    this.monitorEndModelOutput();
  },
  handleTextStart(data: any, eventIdx: number) {
    trackStreamAttempt(this, data, eventIdx);
    identifyAction(this, this.chatStartTextAction(), data, eventIdx, 'text');
    this.$forceUpdate();
  },
  handleTextChunk(data: any, eventIdx: number) {
    if (typeof data?.content !== 'string' || !data.content.length) return;
    trackStreamAttempt(this, data, eventIdx);
    const msg = this.messages[this.currentMessageIndex];
    const previous = msg?.actions?.[msg.actions.length - 1];
    const action = this.chatAppendTextChunk(data.content);
    if (action && action !== previous) identifyAction(this, action, data, eventIdx, 'text');
    this.$forceUpdate();
    this.conditionalScrollToBottom();
    const speech = data.content.replace(/\r/g, '');
    if (speech) this.monitorShowSpeech(speech);
  },
  handleTextEnd(data: any) {
    this.chatCompleteTextAction(data?.full_content);
    this.$forceUpdate();
    this.monitorEndModelOutput();
  },
  handleStreamReset(data: any) {
    debugLog('[TaskPolling] stream attempt reset');
    const attempt = Number(data?.attempt) || 0;
    const max = Number(data?.max_attempts) || 0;
    const label =
      attempt && max
        ? t('appTasks.streamRetrying', { attempt, max })
        : t('appTasks.streamRetryingGeneric');
    const removed = this.chatResetStreamingAttemptActions(label);
    for (const action of removed) {
      for (const [alias, stored] of this.preparingTools || []) {
        if (stored === action) this.preparingTools.delete(alias);
      }
      if (action.type === 'tool') this.toolUnregisterAction?.(action);
    }
    this.streamingMessage = true;
    this.taskInProgress = true;
    this.apiRequestPending = false;
    this.chatClearThinkingLocks?.();
    this.monitorEndModelOutput();
    this.$forceUpdate();
  }
};

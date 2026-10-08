// @ts-nocheck
import { goalModeDebugLog } from '../common';
import { t } from '@/locales';
import { useTaskStore } from '../../../stores/task';
import { ensureMessageSession } from './ownership';
import {
  currentConversationSession,
  isConversationSubmitting,
  ownsConversationSession
} from '../conversation/session';

export const messageControlMethods = {
  async handleSendOrStop() {
    if (isConversationSubmitting(this)) return;
    const session = currentConversationSession(this);
    const owns = () => !!session && ownsConversationSession(this, session);
    const hasText = !!((this.inputMessage || '').trim().length > 0);
    const hasMedia =
      (Array.isArray(this.selectedImages) && this.selectedImages.length > 0) ||
      (Array.isArray(this.selectedVideos) && this.selectedVideos.length > 0);
    const hasFiles = Array.isArray(this.selectedFiles) && this.selectedFiles.length > 0;
    if (hasFiles && !hasText && !hasMedia && !this.composerBusy) {
      this.uiPushToast({
        title: t('appMessages.textRequiredTitle'),
        message: t('appMessages.textRequiredMessage'),
        type: 'warning'
      });
      return;
    }
    // An empty view has no task to queue into, regardless of old presentation state.
    if (!this.currentConversationId) {
      this.clearLocalTaskUiState?.('send:new-conversation');
      return this.sendMessage();
    }
    const mainIdle = this.mainChatIdle;
    if (this.composerBusy && mainIdle && hasText) {
      if (Array.isArray(this.pendingUserQuestions) && this.pendingUserQuestions.length > 0) {
        const answered = await this.answerUserQuestionFromComposer(this.inputMessage);
        if (!owns()) return;
        if (answered) {
          this.inputClearMessage();
          this.inputSetLineCount(1);
          this.inputSetMultiline(false);
          this.autoResizeInput();
        }
        return;
      }
      return this.sendMessage();
    }
    if (this.composerBusy && mainIdle && hasMedia) {
      this.uiPushToast({
        title: t('appMessages.subAgentRunningTitle'),
        message: t('appMessages.subAgentRunningMessage'),
        type: 'warning'
      });
      return;
    }
    if (this.composerBusy) {
      if (hasText) {
        if (Array.isArray(this.pendingUserQuestions) && this.pendingUserQuestions.length > 0) {
          const answered = await this.answerUserQuestionFromComposer(this.inputMessage);
          if (!owns()) return;
          if (answered) {
            this.inputClearMessage();
            this.inputSetLineCount(1);
            this.inputSetMultiline(false);
            this.autoResizeInput();
          }
          return;
        }
        const queued = await this.enqueueRuntimeQueuedMessage(
          this.inputMessage,
          Array.isArray(this.selectedFiles) ? [...this.selectedFiles] : []
        );
        if (!owns()) return;
        if (queued) {
          this.inputClearMessage();
          this.inputClearSelectedFiles();
          this.inputSetLineCount(1);
          this.inputSetMultiline(false);
          this.autoResizeInput();
        }
        return;
      }
      if (hasMedia) {
        this.uiPushToast({
          title: t('appMessages.runningTextOnlyTitle'),
          message: t('appMessages.runningTextOnlyMessage'),
          type: 'warning'
        });
        return;
      }
      return this.stopTask();
    }
    if (this.currentWorkspaceHasRunningTask) {
      this.uiPushToast({
        title: t('appMessages.conversationRunningTitle'),
        message: t('appMessages.conversationRunningMessage'),
        type: 'warning'
      });
      return;
    }
    return this.sendMessage();
  },
  async stopTask() {
    if (this._stopTaskRunning) {
      goalModeDebugLog('stopTask:debounce-rejected', { stopRequested: this.stopRequested });
      return;
    }
    this._stopTaskRunning = true;
    const session = ensureMessageSession(this);
    const owns = () => ownsConversationSession(this, session);

    // 压缩属于主智能体活动，停止精确取消当前任务。
    const canStop =
      (this.streamingUi || this.compressionActiveForCurrentConversation) && !this.stopRequested;
    goalModeDebugLog('stopTask:entry', {
      composerBusy: this.composerBusy,
      stopRequested: this.stopRequested,
      taskInProgress: this.taskInProgress,
      streamingUi: this.streamingUi,
      canStop,
      currentTaskId: this.currentTaskId
    });
    if (!canStop) {
      this._stopTaskRunning = false;
      return;
    }
    const shouldDropToolEvents = this.streamingUi;
    this.markRuntimeQueueSuppressedByManualStop?.();
    this.stopRequested = true;
    this.dropToolEvents = shouldDropToolEvents;
    if (this.goalRunning || this.goalModeArmed) {
      this.goalRunning = false;
      this.goalModeArmed = false;
      this.goalProgress = null;
      this.goalDialogOpen = false;
    }
    try {
      const taskStore = useTaskStore();
      if (taskStore.currentTaskId) await taskStore.cancelTask();
      // 轮询继续，由 task_stopped 事件确认后端停止。
      await new Promise((resolve) => setTimeout(resolve, 300));
      if (!owns()) return;
      const shouldKeepBusy = ['running', 'pending', 'cancel_requested', 'canceled'].includes(
        String(taskStore.taskStatus)
      );
      goalModeDebugLog('stopTask:try-end', {
        currentTaskId: taskStore.currentTaskId,
        taskStatus: taskStore.taskStatus,
        shouldKeepBusy,
        streamingMessage: this.streamingMessage
      });
      this.clearPendingTools('user_stop');
      this.streamingMessage = false;
      this.taskInProgress = shouldKeepBusy;
      this.forceUnlockMonitor('user_stop');
      const lastMessage = this.messages[this.messages.length - 1];
      if (lastMessage && lastMessage.role === 'assistant') {
        lastMessage.awaitingFirstContent = false;
        lastMessage.generatingLabel = '';
      }
    } catch (error) {
      if (!owns()) return;
      console.error('[Message] 取消任务失败:', error);
      const taskStore = useTaskStore();
      const shouldKeepBusy = ['running', 'pending', 'cancel_requested', 'canceled'].includes(
        String(taskStore.taskStatus)
      );
      goalModeDebugLog('stopTask:catch', {
        error: String(error),
        currentTaskId: taskStore.currentTaskId,
        taskStatus: taskStore.taskStatus,
        shouldKeepBusy
      });
      this.clearPendingTools('user_stop');
      this.streamingMessage = false;
      this.taskInProgress = shouldKeepBusy;
      this.forceUnlockMonitor('user_stop');
      const lastMessage = this.messages[this.messages.length - 1];
      if (lastMessage && lastMessage.role === 'assistant') {
        lastMessage.awaitingFirstContent = false;
        lastMessage.generatingLabel = '';
      }
      this.uiPushToast({
        title: t('appMessages.stopRequestedTitle'),
        message: t('appMessages.stopRequestedMessage'),
        type: 'info'
      });
    } finally {
      if (owns()) {
        this.dropToolEvents = false;
        this.stopRequested = false;
      }
      this._stopTaskRunning = false;
    }
  }
};

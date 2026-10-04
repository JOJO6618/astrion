// @ts-nocheck
import { t } from '@/locales';

export const chatMethods = {
  async clearChat() {
    const confirmed = await this.confirmAction({
      title: t('appMessages.clearChatTitle'),
      message: t('appMessages.clearChatConfirmMessage'),
      confirmText: t('appMessages.clearChatConfirmText'),
      cancelText: t('common.cancel')
    });
    if (confirmed) {
      await this.executeSystemCommand('/clear', { showToast: false });
    }
  },
  async compressConversation() {
    if (!this.currentConversationId) {
      this.uiPushToast({
        title: t('appMessages.cannotCompressTitle'),
        message: t('appMessages.cannotCompressMessage'),
        type: 'info'
      });
      return;
    }

    if (this.compressionActiveForCurrentConversation || !this.mainChatIdle) {
      return;
    }
    const conversationId = this.currentConversationId;
    let accepted = false;
    this.compressing = true;
    this.compressionInProgress = true;
    this.compressionConversationId = this.currentConversationId;
    this.compressionMode = 'manual';
    this.compressionStage = 'requesting';
    this.compressionError = '';
    if (this.compressionToastId) {
      this.uiDismissToast(this.compressionToastId);
      this.compressionToastId = null;
    }
    this.compressionToastId = this.uiPushToast({
      title: t('appMessages.compressingTitle'),
      message: t('appMessages.compressingMessage'),
      type: 'info',
      duration: null,
      closable: false
    });

    try {
      const response = await fetch(`/api/conversations/${conversationId}/compress`, {
        method: 'POST'
      });

      const result = await response.json();

      if (response.ok && result.success && result.data?.task_id) {
        accepted = true;
        const { useTaskStore } = await import('../../../stores/task');
        if (this.currentConversationId === conversationId) {
          this.taskInProgress = true;
          this.stopRequested = false;
          this.clearProcessedEvents();
          useTaskStore().resumeTask(result.data.task_id, {
            status: 'running',
            eventHandler: (event: any) => this.handleTaskEvent(event)
          });
        }
        await this.refreshRunningWorkspaceTasks?.();
      } else {
        const message = result.message || result.error || t('appMessages.compressionFailed');
        this.compressionError = message;
        this.uiPushToast({
          title: t('appMessages.compressionFailed'),
          message,
          type: 'error'
        });
      }
    } catch (error) {
      console.error('压缩对话异常:', error);
      this.compressionError = error.message || t('common.retryLater');
      this.uiPushToast({
        title: t('appMessages.compressionErrorTitle'),
        message: error.message || t('common.retryLater'),
        type: 'error'
      });
    } finally {
      this.compressing = false;
      // 受理成功后由任务事件结束压缩状态；切换对话不接管原对话。
      if (!accepted && this.currentConversationId === conversationId) {
        this.handleCompressionState({ conversation_id: conversationId, in_progress: false });
      }
    }
  },
  async sendAutoUserMessage(text) {
    const message = (text || '').trim();
    if (!message || !this.isConnected) {
      return false;
    }
    const quotaType = this.thinkingMode ? 'thinking' : 'fast';
    if (this.isQuotaExceeded(quotaType)) {
      this.showQuotaToast({ type: quotaType });
      return false;
    }
    this.taskInProgress = true;
    this.chatAddUserMessage(message, [], [], [], 'user');
    this.chatStartAssistantMessage();
    this.stopRequested = false;
    if (typeof this.monitorShowPendingReply === 'function') {
      this.monitorShowPendingReply();
    }
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      if (typeof this.clearProcessedEvents === 'function') {
        this.clearProcessedEvents();
      }
      await taskStore.createTask(message, [], [], this.currentConversationId, {
        eventHandler: (event: any) => this.handleTaskEvent(event)
      });
    } catch (error) {
      console.error('[Message] 自动消息创建任务失败:', error);
      this.uiPushToast({
        title: t('appMessages.sendFailedTitle'),
        message: error?.message || t('appMessages.createTaskFailedMessage'),
        type: 'error'
      });
      this.streamingMessage = false;
      this.taskInProgress = false;
      if (typeof this.cleanupTrailingEmptyAssistantPlaceholder === 'function') {
        this.cleanupTrailingEmptyAssistantPlaceholder('auto_create_task_failed');
      }
      if (typeof this.forceUnlockMonitor === 'function') {
        this.forceUnlockMonitor('auto_create_task_failed');
      }
      return false;
    }
    if (this.autoScrollEnabled) {
      this.scrollToBottom();
    }
    this.autoResizeInput();
    setTimeout(() => {
      if (this.currentConversationId) {
        this.updateCurrentContextTokens();
      }
    }, 1000);
    return true;
  },
  autoResizeInput() {
    this.$nextTick(() => {
      const textarea = this.getComposerElement('stadiumInput');
      if (!textarea || !(textarea instanceof HTMLTextAreaElement)) {
        return;
      }
      const previousHeight = textarea.offsetHeight;
      textarea.style.height = 'auto';
      const computedStyle = window.getComputedStyle(textarea);
      const lineHeight = parseFloat(computedStyle.lineHeight || '20') || 20;
      const maxHeight = lineHeight * 6;
      const targetHeight = Math.min(textarea.scrollHeight, maxHeight);
      this.inputSetLineCount(Math.max(1, Math.round(targetHeight / lineHeight)));
      this.inputSetMultiline(targetHeight > lineHeight * 1.4);
      if (Math.abs(targetHeight - previousHeight) <= 0.5) {
        textarea.style.height = `${targetHeight}px`;
        return;
      }
      textarea.style.height = `${previousHeight}px`;
      void textarea.offsetHeight;
      requestAnimationFrame(() => {
        textarea.style.height = `${targetHeight}px`;
      });
    });
  }
};

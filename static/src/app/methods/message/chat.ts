// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import { useTaskStore } from '../../../stores/task';
import {
  extractSkillRefsFromMessage,
  SKILL_MARKDOWN_LINK_RE,
} from './shared';

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

    if (this.compressing) {
      return;
    }

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
      const response = await fetch(`/api/conversations/${this.currentConversationId}/compress`, {
        method: 'POST'
      });

      const result = await response.json();

      if (response.ok && result.success) {
        this.compressionStage = 'switching';
        const newId = result.compressed_conversation_id;
        const isInPlace = newId && newId === this.currentConversationId;
        // in-place 压缩：对话 id 不变，不重新加载（避免 resetAllStates
        // 重置滚动状态 + fetchAndDisplayHistory 清空重渲染导致闪烁和锁死）。
        if (newId && !isInPlace) {
          await this.loadConversation(newId, { force: true });
        }
        const guideMessage = (result.guide_message || '').trim();
        // 手动压缩只有一种行为：后端已把引导语作为 user 消息追加进历史，
        // 这里刷新展示即可，不触发新一轮请求（等待用户继续发送消息才工作）。
        if (newId && !isInPlace) {
          await this.loadConversation(newId, { force: true });
        } else if (isInPlace) {
          await this.loadConversation(this.currentConversationId, { force: true });
        }
        void guideMessage;

        if (!isInPlace) {
          await this.loadConversationsList();
        }

        debugLog('对话压缩完成:', result);
        this.uiPushToast({
          title: t('appMessages.compressionCompletedTitle'),
          message: t('appMessages.compressionCompletedMessage'),
          type: 'success',
          duration: 2200
        });
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
      if (this.compressionToastId) {
        this.uiDismissToast(this.compressionToastId);
        this.compressionToastId = null;
      }
      this.compressing = false;
      this.compressionInProgress = false;
      this.compressionConversationId = null;
      this.compressionMode = '';
      this.compressionStage = '';
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

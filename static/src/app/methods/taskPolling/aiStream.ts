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
import { t } from '@/locales';

export const aiStreamMethods = {
  handleAiMessageStart(data: any, eventIdx: number) {
    const lastMessage = this.messages[this.messages.length - 1];

    debugLog('[TaskPolling] AI消息开始, idx:', eventIdx);

    if (this.waitingForSubAgent) {
      this.waitingForSubAgent = false;
    }

    // 检查是否已经有 assistant 消息
    const hasAssistantMessage = lastMessage && lastMessage.role === 'assistant';

    // 判断是否是刷新恢复的情况：
    // 1. 有assistant消息
    // 2. 且最后一条 assistant 仍处于“未完成流式状态”（而不是普通已完成消息）
    // 3. 且任务确实仍在进行中
    const lastActions = Array.isArray(lastMessage?.actions) ? lastMessage.actions : [];
    const hasUnfinishedAction = lastActions.some((action: any) => {
      if (!action) return false;
      if (action.streaming) return true;
      if (action.type === 'tool' && action.tool) {
        const status = String(action.tool.status || '').toLowerCase();
        return ['preparing', 'running', 'pending', 'queued', 'awaiting_approval', 'awaiting_user_answer'].includes(status);
      }
      return false;
    });
    const hasNoActionsYet = !lastActions.length;
    const isRefreshRestore =
      hasAssistantMessage &&
      (hasNoActionsYet || hasUnfinishedAction || lastMessage.awaitingFirstContent === true) &&
      (this.taskInProgress || this.streamingMessage);

    userMDebug('taskPolling.handleAiMessageStart:decision', {
      eventIdx,
      hasAssistantMessage,
      hasNoActionsYet,
      hasUnfinishedAction,
      awaitingFirstContent: !!lastMessage?.awaitingFirstContent,
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      isRefreshRestore
    });

    if (isRefreshRestore) {
      debugLog('[TaskPolling] 刷新恢复场景，复用现有 assistant 消息');
      // 只更新状态，不创建新消息
      this.taskInProgress = true;
      this.stopRequested = false;
      this.streamingMessage = true;

      // 确保 awaitingFirstContent 被正确设置
      const hasContent = lastMessage.actions && lastMessage.actions.length > 0;
      if (this._rebuildingFromScratch) {
        // 从头重建时，restoreTaskState 已根据事件流分析（hasAssistantContentEvent）
        // 正确设置了 awaitingFirstContent，此处不覆盖。
        // actions 被清空不代表没有内容，事件重放会恢复。
      } else if (!hasContent) {
        lastMessage.awaitingFirstContent = true;
        lastMessage.generatingLabel = lastMessage.generatingLabel || t('appTasks.thinkingLabel');
      } else {
        // 如果已有内容，确保等待动画不显示
        lastMessage.awaitingFirstContent = false;
        lastMessage.generatingLabel = '';
      }
      return;
    }

    // 其他情况：创建新的 assistant 消息
    debugLog('[TaskPolling] 创建新的 assistant 消息');
    this.monitorResetSpeech();
    this.cleanupStaleToolActions();
    this.taskInProgress = true;
    this.chatStartAssistantMessage();
    this.stopRequested = false;
    this.streamingMessage = true;

    const newMessage = this.messages[this.messages.length - 1];

    // 如果是从头重建，标记消息为静默恢复
    if (this._rebuildingFromScratch) {
      if (newMessage && newMessage.role === 'assistant') {
        debugLog('[TaskPolling] 标记消息为静默恢复（从头重建）');
        newMessage.awaitingFirstContent = false;
        newMessage.generatingLabel = '';
      }
    }

    // 强制触发Vue响应式更新
    this.$forceUpdate();

    this.$nextTick(() => {
      this.conditionalScrollToBottom();
    });
  },
  handleThinkingStart(data: any, eventIdx: number) {
    debugLog('[TaskPolling] 思考开始, idx:', eventIdx);
    const ignoreThinking = this.runMode === 'fast' || this.thinkingMode === false;
    if (ignoreThinking) {
      this.monitorEndModelOutput();
      return;
    }

    // 防御性检查：如果没有assistant消息，先创建一个
    const lastMessage = this.messages[this.messages.length - 1];
    if (!lastMessage || lastMessage.role !== 'assistant') {
      this.chatStartAssistantMessage();
    }

    this.monitorShowThinking();

    const result = this.chatStartThinkingAction();

    if (result && result.blockId) {
      const blockId = result.blockId;

      const lastMessage = this.messages[this.messages.length - 1];

      // 有内容了，关闭等待动画
      if (lastMessage && lastMessage.role === 'assistant') {
        lastMessage.awaitingFirstContent = false;
        lastMessage.generatingLabel = '';
      }

      // 只在非历史恢复时展开思考块
      if (!this._rebuildingFromScratch) {
        this.chatExpandBlock(blockId);
      }

      this.conditionalScrollToBottom();
      this.chatSetThinkingLock(blockId, true);

      // 强制触发 actions 数组的响应式更新
      if (lastMessage && lastMessage.actions) {
        lastMessage.actions = [...lastMessage.actions];
      }

      this.$forceUpdate();
    }
  },
  handleThinkingChunk(data: any) {
    if (this.runMode === 'fast' || this.thinkingMode === false) {
      return;
    }
    let thinkingAction = this.chatAppendThinkingChunk(data.content);
    // 兜底：刷新恢复时可能只能拿到 chunk 事件（start 事件已被服务端窗口截断）
    // 此时补建一个 thinking action，避免思考内容丢失。
    if (!thinkingAction && data?.content) {
      const started = this.chatStartThinkingAction();
      thinkingAction = this.chatAppendThinkingChunk(data.content);
      restoreDebugLog('restore:thinking-chunk-auto-start', {
        startedBlockId: started?.blockId || null,
        contentLength: String(data.content || '').length,
        currentConversationId: this.currentConversationId
      });
    }
    if (thinkingAction) {
      this.$forceUpdate();
      this.$nextTick(() => {
        if (thinkingAction && thinkingAction.blockId) {
          this.scrollThinkingToBottom(thinkingAction.blockId);
        }
        this.conditionalScrollToBottom();
      });
    }
    this.monitorShowThinking();
  },
  handleThinkingEnd(data: any) {
    debugLog('[TaskPolling] 思考结束');
    if (this.runMode === 'fast' || this.thinkingMode === false) {
      return;
    }
    const blockId = this.chatCompleteThinkingAction(data.full_content);
    if (blockId) {
      // 解锁思考块
      this.chatSetThinkingLock(blockId, false);

      // 只在非历史恢复时延迟折叠思考块
      if (!this._rebuildingFromScratch) {
        // 延迟折叠思考块（给用户一点时间看到思考完成）
        setTimeout(() => {
          this.chatCollapseBlock(blockId);
          this.$forceUpdate();
        }, 1000);
      } else {
        // 历史恢复时立即折叠，不需要动画
        this.chatCollapseBlock(blockId);
      }

      this.$nextTick(() => this.scrollThinkingToBottom(blockId));
    }
    this.$forceUpdate();
    this.monitorEndModelOutput();
  },
  handleTextStart() {
    debugLog('[TaskPolling] 文本开始');

    // 防御性检查：如果没有assistant消息，先创建一个
    const lastMessage = this.messages[this.messages.length - 1];
    if (!lastMessage || lastMessage.role !== 'assistant') {
      this.chatStartAssistantMessage();
    }

    this.chatStartTextAction();

    // 有内容了，关闭等待动画
    const currentMessage = this.messages[this.messages.length - 1];
    if (currentMessage && currentMessage.role === 'assistant') {
      currentMessage.awaitingFirstContent = false;
      currentMessage.generatingLabel = '';
    }

    this.$forceUpdate();
  },
  handleTextChunk(data: any) {
    if (data && typeof data.content === 'string' && data.content.length) {
      let textAction = this.chatAppendTextChunk(data.content);
      // 兜底：刷新恢复时可能只有 text_chunk（缺少 text_start），需要自动补建 text action
      if (!textAction) {
        this.chatStartTextAction();
        textAction = this.chatAppendTextChunk(data.content);
        restoreDebugLog('restore:text-chunk-auto-start', {
          contentLength: data.content.length,
          appendedAfterStart: !!textAction,
          currentConversationId: this.currentConversationId
        });
      }
      this.$forceUpdate();
      this.conditionalScrollToBottom();
      const speech = data.content.replace(/\r/g, '');
      if (speech) {
        this.monitorShowSpeech(speech);
      }
    }
  },
  handleTextEnd(data: any) {
    debugLog('[TaskPolling] 文本结束');
    const full = data?.full_content || '';
    this.chatCompleteTextAction(full);
    this.$forceUpdate();
    this.monitorEndModelOutput();
  }
};

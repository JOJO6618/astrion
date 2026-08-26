// @ts-nocheck
import { debugLog, traceLog } from '../common';
import { usePersonalizationStore } from '../../../stores/personalization';
import {

} from './shared';

export const stateMethods = {
  resetAllStates(reason = 'unspecified', options: { preserveMonitorWindows?: boolean } = {}) {

    // 如果正在等待子智能体完成，不重置任务状态
    if (this.waitingForSubAgent) {
      debugLog('跳过状态重置：正在等待子智能体完成', { reason });
      return;
    }

    debugLog('重置所有前端状态', { reason, conversationId: this.currentConversationId });
    this.logMessageState('resetAllStates:before-cleanup', { reason });
    this.fileHideContextMenu();
    this.monitorResetVisual({
      preserveBubble: true,
      preservePointer: true,
      preserveWindows: !!options?.preserveMonitorWindows,
      preserveQueue: !!options?.preserveMonitorWindows
    });

    // 重置消息和流状态
    // 先清消息内残留的流式字段（currentStreamingType/activeThinkingId/streaming
    // action），否则异常中断后状态头像会一直卡在「思考中」（avatarStatus 的
    // isThinking 只认这两个字段，与任务标志无关）
    this.chatClearStreamingResidualState();
    this.apiRequestPending = false;
    this.streamingMessage = false;
    this.currentMessageIndex = -1;
    this.stopRequested = false;
    this.taskInProgress = false;
    this.dropToolEvents = false;

    // 清理工具状态
    this.toolResetTracking();

    // 新增：将所有未完成的工具标记为已完成，并清理awaitingFirstContent状态
    const assistantMsgsBefore = this.messages
      .filter((m) => m.role === 'assistant')
      .map((m) => ({
        awaitingFirstContent: m.awaitingFirstContent,
        generatingLabel: m.generatingLabel
      }));

    this.messages.forEach((msg) => {
      if (msg.role === 'assistant') {
        // 清理等待动画状态
        if (msg.awaitingFirstContent) {
          msg.awaitingFirstContent = false;
        }
        if (msg.generatingLabel) {
          msg.generatingLabel = '';
        }

        // 清理工具状态
        if (msg.actions) {
          msg.actions.forEach((action) => {
            if (
              action.type === 'tool' &&
              (action.tool.status === 'preparing' || action.tool.status === 'running')
            ) {
              action.tool.status = 'completed';
            }
          });
        }
      }
    });

    const assistantMsgsAfter = this.messages
      .filter((m) => m.role === 'assistant')
      .map((m) => ({
        awaitingFirstContent: m.awaitingFirstContent,
        generatingLabel: m.generatingLabel
      }));

    // 清理Markdown缓存
    if (this.markdownCache) {
      this.markdownCache.clear();
    }
    this.chatClearThinkingLocks();

    // 强制更新视图
    this.$forceUpdate();

    this.inputSetSettingsOpen(false);
    this.inputSetToolMenuOpen(false);
    this.inputSetQuickMenuOpen(false);
    this.modeMenuOpen = false;
    this.permissionMenuOpen = false;
    this.workModeMenuOpen = false;
    this.inputSetLineCount(1);
    this.inputSetMultiline(false);
    this.inputClearMessage();
    this.runtimeQueuedMessages = [];
    this.runtimeGuidanceFallbackQueue = [];
    this.runtimeQueueAutoSendInProgress = false;
    this.runtimeQueueSyncLockKey = '';
    this.runtimeQueueSyncLockUntil = 0;
    if (typeof this.clearRuntimeQueueSuppressionState === 'function') {
      this.clearRuntimeQueueSuppressionState();
    } else {
      this.runtimeQueueSuppressedMessageIds = new Set();
      this.runtimeGuidanceSuppressedTextCounts = {};
    }
    this.composerReservedHeight = 80;
    this.inputClearSelectedImages();
    this.inputSetImagePickerOpen(false);
    this.imageEntries = [];
    this.imageLoading = false;
    this.conversationHasImages = false;
    this.conversationHasVideos = false;
    this.toolSetSettingsLoading(false);
    this.toolSetSettings([]);
    this.pendingToolApprovals = [];
    this.decidingApprovalIds = [];
    this.autoApprovalTitle = '自动审批记录';
    // 切换对话/工作区/新建视图时，清理上一轮目标模式的本地完成提示。
    // 如果目标任务仍在运行，后续 restoreTaskState 会重新恢复运行态。
    this.goalModeArmed = false;
    this.goalRunning = false;
    this.goalProgress = null;
    this.goalDialogOpen = false;

    debugLog('前端状态重置完成');
    this._scrollListenerReady = false;
    this._manualScrollSuppressUntil = 0;
    this._escapedByUserScroll = false;
    this._autoRelockCooldownUntil = 0;
    this.$nextTick(() => {
      this.ensureScrollListener();
      const composerRef = typeof this.getInputComposerRef === 'function' ? this.getInputComposerRef() : null;
      if (composerRef && typeof composerRef.emitComposerHeight === 'function') {
        composerRef.emitComposerHeight();
      }
    });

    // 重置已加载对话标记，便于后续重新加载新对话历史
    this.lastHistoryLoadedConversationId = null;

    this.logMessageState('resetAllStates:after-cleanup', { reason });
  },
  scheduleResetAfterTask(
    reason = 'unspecified',
    options: { preserveMonitorWindows?: boolean } = {}
  ) {
    const start = Date.now();
    const maxWait = 4000;
    const interval = 200;
    const tryReset = () => {
      if (!this.monitorIsLocked || Date.now() - start >= maxWait) {
        this.resetAllStates(reason, options);
        return;
      }
      setTimeout(tryReset, interval);
    };
    tryReset();
  },
  resetTokenStatistics() {
    this.resourceResetTokenStatistics();
  }
};

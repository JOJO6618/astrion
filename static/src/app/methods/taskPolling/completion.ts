// @ts-nocheck
import { debugLog, goalModeDebugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { jsonDebug } from './shared';
import { t } from '@/locales';
import { isToolArgumentError, taskErrorToast } from '@/utils/taskErrorToast';
import { currentConversationSession } from '../conversation/session';

export const completionMethods = {
  isCurrentTaskEvent(data: any) {
    return (
      (!data?.conversation_id || data.conversation_id === this.currentConversationId) &&
      (!data?.task_id || data.task_id === useTaskStore().currentTaskId)
    );
  },
  handleTaskComplete(data: any) {
    if (!this.isCurrentTaskEvent(data)) return;
    const taskId = data?.task_id || useTaskStore().currentTaskId;
    const session = currentConversationSession(this);
    const owns = () =>
      currentConversationSession(this) === session &&
      (!useTaskStore().currentTaskId || useTaskStore().currentTaskId === taskId);
    if (data?.task_type === 'compression' || this.compressionActiveForCurrentConversation) {
      this.handleCompressionState({
        conversation_id: this.currentConversationId,
        in_progress: false
      });
    }
    this.recoverRuntimeMessagesToDraft?.(data?.runtime_queued_messages || []);
    const pendingToolsBefore = this.hasPendingToolActions?.() ?? null;
    jsonDebug('handleTaskComplete:before', {
      data,
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested,
      pendingToolsBefore,
      preparingToolsSize: this.preparingTools?.size ?? null,
      activeToolsSize: this.activeTools?.size ?? null,
      waitingForSubAgent: this.waitingForSubAgent,
      waitingForBackgroundCommand: this.waitingForBackgroundCommand
    });
    const hasRunningSubAgents = !!data?.has_running_sub_agents;
    const hasRunningBackgroundCommands = !!data?.has_running_background_commands;
    const hasRunningMultiAgent = !!data?.has_running_multi_agent;
    if (hasRunningSubAgents || hasRunningMultiAgent) {
      debugLog('[TaskPolling] 任务完成，但仍有后台子智能体/多智能体运行');
    } else {
      debugLog('[TaskPolling] 任务完成');
    }

    // 行内引用：任务完成事件带回权威来源（轮询通道；socket 通道在 useLegacySocket 同款处理），
    // 挂到最后一条 assistant 消息 metadata，MarkdownRenderer watcher 据此做 chip 裁决与富化
    if (Array.isArray(data?.citations) && Array.isArray(this.messages) && this.messages.length) {
      const lastMsg = this.messages[this.messages.length - 1];
      if (lastMsg && lastMsg.role === 'assistant') {
        lastMsg.metadata = {
          ...(lastMsg.metadata || {}),
          citations: data.citations
        };
      }
    }

    // 同步处理状态更新
    this.streamingMessage = false;
    this.stopRequested = false;
    // 兜底清理可能残留的流式状态（正常流程 thinking_end/text_end 已清，此处幂等）
    this.chatClearStreamingResidualState?.();
    this.apiRequestPending = false;
    if (!hasRunningSubAgents && !hasRunningMultiAgent && !hasRunningBackgroundCommands) {
      this.markLatestUserWorkCompleted();
    }

    if (hasRunningMultiAgent) {
      // 多智能体模式下主智能体已空闲，但仍有实例在运行，保持对话运行态继续接收后续消息；
      // 不启动后台等待轮询，也不阻塞输入区（waitingForSubAgent=false）。
      // 但必须启动运行中任务探测，否则子智能体后续输出触发的新主任务无法被前端发现。
      jsonDebug('handleTaskComplete:hasRunningMultiAgent', {
        taskInProgress: this.taskInProgress,
        currentConversationId: this.currentConversationId
      });
      this.taskInProgress = true;
      this.waitingForSubAgent = false;
      this.waitingForBackgroundCommand = hasRunningBackgroundCommands;
      this.startMultiAgentTaskProbe();
    } else if (hasRunningSubAgents || hasRunningBackgroundCommands) {
      this.taskInProgress = true;
      this.waitingForSubAgent = hasRunningSubAgents;
      this.waitingForBackgroundCommand = hasRunningBackgroundCommands;
      this.startWaitingTaskProbe();
    } else {
      this.cleanupTrailingEmptyAssistantPlaceholder('task_complete');
      // 主任务已结束：若有遗留工具块处于 running/preparing，会导致发送按钮继续显示“停止”。
      // 这里统一清理遗留中的工具状态，避免前端忙碌态卡死。
      if (typeof this.clearPendingTools === 'function') {
        this.clearPendingTools('task_complete');
      }
      this.taskInProgress = false;
      this.waitingForSubAgent = false;
      this.waitingForBackgroundCommand = false;
      this.stopWaitingTaskProbe();
      this.stopMultiAgentTaskProbe();
      this.clearTaskState(taskId); // 清理任务状态
    }

    this.$forceUpdate();

    // 只更新统计，不重新加载历史
    if (this.currentConversationId) {
      setTimeout(() => {
        if (!owns()) return;
        this.fetchConversationTokenStatistics();
        this.updateCurrentContextTokens();
      }, 500);
    }
    this.scheduleTodoListRefresh(100);
    if (
      data?.conversation_id &&
      data.conversation_id === this.currentConversationId &&
      data?.task_id
    ) {
      this.acknowledgeCompletedWorkspaceTask?.(data.task_id);
    }
    setTimeout(() => {
      if (owns()) this.refreshRunningWorkspaceTasks?.();
    }, 0);
    // 标题生成可能晚于主任务完成；主轮询停止后主动短轮询当前会话标题，避免必须刷新页面。
    this.scheduleGeneratedTitleRefresh('task_complete', {
      conversationId: data?.conversation_id || this.currentConversationId,
      deadlineMs: 90000,
      maxAttempts: 60
    });
    const pendingToolsAfter =
      typeof this.hasPendingToolActions === 'function' ? this.hasPendingToolActions() : null;
    jsonDebug('handleTaskComplete:after', {
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested,
      pendingToolsAfter,
      preparingToolsSize: this.preparingTools?.size ?? null,
      activeToolsSize: this.activeTools?.size ?? null,
      waitingForSubAgent: this.waitingForSubAgent,
      waitingForBackgroundCommand: this.waitingForBackgroundCommand
    });
  },
  handleTaskStopped(data: any, eventIdx: number) {
    if (!this.isCurrentTaskEvent(data)) return;
    const taskId = data?.task_id || useTaskStore().currentTaskId;
    const session = currentConversationSession(this);
    const owns = () =>
      currentConversationSession(this) === session &&
      (!useTaskStore().currentTaskId || useTaskStore().currentTaskId === taskId);
    if (Array.isArray(data?.runtime_queued_messages))
      this.recoverRuntimeMessagesToDraft?.(data.runtime_queued_messages);
    this.handleCompressionState({
      conversation_id: this.currentConversationId,
      in_progress: false
    });
    goalModeDebugLog('handleTaskStopped:entered', { eventIdx, data });
    jsonDebug('handleTaskStopped:before', {
      eventIdx,
      data,
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested
    });
    debugLog('[TaskPolling] 任务已停止, idx:', eventIdx, data);

    const hasRunningSubAgents = !!data?.has_running_sub_agents;
    const hasRunningBackgroundCommands = !!data?.has_running_background_commands;
    const hasRunningBackground = hasRunningSubAgents || hasRunningBackgroundCommands;

    goalModeDebugLog('handleTaskStopped', {
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      hasRunningSubAgents,
      hasRunningBackgroundCommands,
      data
    });

    this.cleanupTrailingEmptyAssistantPlaceholder('task_stopped');
    this.chatClearStreamingResidualState?.();
    this.apiRequestPending = false;
    this.streamingMessage = false;
    this.stopRequested = false;

    if (hasRunningBackground) {
      // 主智能体已停，后台任务仍在跑：保持 taskInProgress=true （对话列表显示运行中），
      // 但输入栏不再锁定（stopRequested 已重置），用户可发新消息触发下一轮。
      // 后台任务的停止由独立的子智能体/后台指令按钮处理，不再走停止按钮二次点击。
      this.taskInProgress = true;
      this.waitingForSubAgent = hasRunningSubAgents;
      this.waitingForBackgroundCommand = hasRunningBackgroundCommands;
      debugLog('[TaskPolling] 任务已停止，仍有后台任务运行，保持对话运行态但释放输入区');
    } else {
      // 对话真正停止：停止计时器并持久化
      this.markLatestUserWorkCompleted();
      this.taskInProgress = false;
      this.waitingForSubAgent = false;
      this.waitingForBackgroundCommand = false;
      this.stopWaitingTaskProbe();
      if (typeof this.clearPendingTools === 'function') {
        this.clearPendingTools('task_stopped');
      }
      this.clearTaskState(taskId);
    }

    goalModeDebugLog('handleTaskStopped:after', {
      taskInProgress: this.taskInProgress,
      waitingForSubAgent: this.waitingForSubAgent,
      waitingForBackgroundCommand: this.waitingForBackgroundCommand,
      hasRunningBackground
    });

    this.scheduleTodoListRefresh(100);
    setTimeout(() => {
      if (owns()) this.refreshRunningWorkspaceTasks?.();
    }, 0);
    this.$forceUpdate();
    jsonDebug('handleTaskStopped:after', {
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested
    });
  },
  clearTaskState(taskId = useTaskStore().currentTaskId) {
    const taskStore = useTaskStore();
    if (taskStore.currentTaskId !== taskId) return false;
    this.stopWaitingTaskProbe();
    taskStore.clearTask();
    return true;
  },
  handleTaskError(data: any) {
    if (!this.isCurrentTaskEvent(data)) return;
    const taskId = data?.task_id || useTaskStore().currentTaskId;
    if (data?.preserve_pending_messages) {
      this.recoverRuntimeMessagesToDraft?.(data.runtime_queued_messages || []);
      this.handleCompressionState({
        conversation_id: this.currentConversationId,
        in_progress: false
      });
    }
    jsonDebug('handleTaskError:incoming', {
      data,
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested
    });
    const shouldRetry = Boolean(data?.retry);
    if (shouldRetry) {
      const retryIn = Number(data?.retry_in) || 5;
      const attempt = Number(data?.attempt) || 1;
      const maxAttempts = Number(data?.max_attempts) || attempt;

      debugLog('[TaskPolling] API错误，等待自动重试', {
        retryIn,
        attempt,
        maxAttempts,
        message: data?.message
      });

      this.stopRequested = false;
      this.taskInProgress = true;
      this.streamingMessage = true;
      this.$forceUpdate();

      this.uiPushToast(taskErrorToast(data));
      return;
    }

    const errorMessage = data.message || t('common.unknownError');
    const errorType = data.error_type || 'unknown';
    const isToolArgumentParseError = isToolArgumentError(data);

    // 工具参数解析失败属于“单个工具调用失败”，后端会继续执行主任务。
    // 这里不能停止轮询，否则会出现“后端继续跑、前端不再更新”的假死状态。
    if (isToolArgumentParseError) {
      console.warn('[TaskPolling] 工具参数解析失败（非致命），继续轮询任务:', data);
      jsonDebug('handleTaskError:tool-args-parse-error-ignored', {
        errorType,
        errorMessage,
        taskInProgress: this.taskInProgress,
        streamingMessage: this.streamingMessage,
        stopRequested: this.stopRequested
      });
      this.uiPushToast(taskErrorToast(data));
      // 保持当前运行态，不在这里强制置为“工作中”，避免后续任务结束时状态粘住
      this.stopRequested = false;
      this.$forceUpdate();
      return;
    }

    console.error('[TaskPolling] 任务错误:', data.message);
    this.uiPushToast(taskErrorToast(data));

    // 清理状态（顺序：先清依赖 awaitingFirstContent 判定的空占位，再清残留流式字段）
    this.cleanupTrailingEmptyAssistantPlaceholder?.('task_error');
    this.chatClearStreamingResidualState?.();
    if (typeof this.clearPendingTools === 'function') {
      this.clearPendingTools('task_error');
    }
    this.apiRequestPending = false;
    this.markLatestUserWorkCompleted();
    this.streamingMessage = false;
    this.taskInProgress = false;
    this.stopRequested = false;
    this.$forceUpdate();
    jsonDebug('handleTaskError:fatal-after-state-update', {
      errorType,
      errorMessage,
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested
    });

    // 同步清理精确所属任务，避免异步模块导入后误停新任务。
    this.clearTaskState(taskId);
    this.waitingForSubAgent = false;
    this.waitingForBackgroundCommand = false;
  }
};

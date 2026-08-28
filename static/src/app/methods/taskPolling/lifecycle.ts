// @ts-nocheck
import { debugLog, goalModeDebugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { useQuickDockStore } from '../../../stores/quickDock';
import { useChatStore } from '../../../stores/chat';
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

export const lifecycleMethods = {
  handleTaskEvent(event: any) {
    if (!event || !event.type) {
      return;
    }

    const eventType = event.type;
    const eventData = event.data || {};
    const eventIdx = event.idx;
    const taskStore = useTaskStore();
    if (eventType === 'task_complete' || eventType === 'task_stopped' || eventType === 'error') {
      jsonDebug('task-event', {
        eventType,
        eventIdx,
        currentConversationId: this.currentConversationId,
        eventConversationId: eventData?.conversation_id,
        taskInProgress: this.taskInProgress,
        streamingMessage: this.streamingMessage,
        stopRequested: this.stopRequested,
        waitingForSubAgent: this.waitingForSubAgent,
        waitingForBackgroundCommand: this.waitingForBackgroundCommand,
        errorType: eventData?.error_type,
        errorMessage: eventData?.message
      });
    }
    if (
      eventType === 'system_message' ||
      eventType === 'sub_agent_waiting' ||
      (eventType === 'user_message' && eventData?.sub_agent_notice)
    ) {
      debugNotifyLog('[DEBUG_NOTIFY][event] captured-key-event', {
        eventType,
        eventIdx,
        eventData
      });
      keyNotifyLog('[DEBUG_NOTIFY_KEY][event] key-event', {
        eventType,
        idx: eventIdx,
        task_id: eventData?.task_id,
        sub_agent_notice: !!eventData?.sub_agent_notice,
        has_running_sub_agents: eventData?.has_running_sub_agents,
        has_running_background_commands: eventData?.has_running_background_commands
      });
    }

    // 检查事件的 conversation_id 是否匹配当前对话
    // 如果不匹配，忽略该事件（避免切换对话后旧任务的事件显示到新对话中）
    const crossConversationAllowed = new Set([
      'shallow_compression',
      'compression_finished'
    ]);
    // 显式新建路由（/new、/multiagent/new）与独立全屏路由（工作流编辑器等）上
    // currentConversationId 为空是常态：空 id 不等于“无归属”，此时任何携带
    // conversation_id 的事件都属于其他对话，必须丢弃，否则运行中对话的
    // 用户输入/AI输出/工具调用会渲染到空白页/被覆盖的隐藏视图。
    const onExplicitNewRoute =
      typeof this.isExplicitNewConversationRoute === 'function' &&
      this.isExplicitNewConversationRoute();
    const onIndependentRoute =
      typeof this.isConversationIndependentRoute === 'function' &&
      this.isConversationIndependentRoute();
    const eventConversationMismatch = !!(
      eventData.conversation_id &&
      (this.currentConversationId
        ? eventData.conversation_id !== this.currentConversationId
        : onExplicitNewRoute || onIndependentRoute)
    );
    if (!crossConversationAllowed.has(eventType) && eventConversationMismatch) {
      {
        if (
          [
            'ai_message_start',
            'thinking_start',
            'thinking_chunk',
            'thinking_end',
            'text_start',
            'text_chunk',
            'text_end'
          ].includes(eventType)
        ) {
          restoreDebugLog('event:drop-conversation-mismatch', {
            eventType,
            eventIdx,
            eventConversationId: eventData.conversation_id,
            currentConversationId: this.currentConversationId
          });
        }
        debugLog(`[TaskPolling] 忽略不匹配的事件 #${eventIdx}: ${eventType}, 事件对话=${eventData.conversation_id}, 当前对话=${this.currentConversationId}`);
        return;
      }
    }
    // 检查事件 task_id 是否仍是当前正在接管的任务，防止切换/新建对话后旧轮询的在途响应串写。
    // task_stopped / task_complete / error 属于任务终态事件，即使 currentTaskId 已被清理
    //（例如用户点击停止后 stopPolling 清空了 ID），只要 conversation_id 匹配就应该处理。
    const isTerminalTaskEvent = ['task_complete', 'task_stopped', 'error'].includes(eventType);
    if (
      !crossConversationAllowed.has(eventType) &&
      eventData.task_id &&
      (!taskStore.currentTaskId || eventData.task_id !== taskStore.currentTaskId) &&
      !(isTerminalTaskEvent && eventData.conversation_id === this.currentConversationId)
    ) {
      restoreDebugLog('event:drop-task-mismatch', {
        eventType,
        eventIdx,
        eventTaskId: eventData.task_id,
        currentTaskId: taskStore.currentTaskId,
        eventConversationId: eventData.conversation_id,
        currentConversationId: this.currentConversationId
      });
      debugLog(`[TaskPolling] 忽略不匹配的任务事件 #${eventIdx}: ${eventType}, 事件任务=${eventData.task_id}, 当前任务=${taskStore.currentTaskId}`);
      return;
    }

    // 事件去重检查必须在 conversation/task 校验之后执行，避免其他对话同 idx 事件污染当前任务去重表。
    const dedupeKey =
      eventData.task_id && typeof eventIdx === 'number'
        ? `${eventData.task_id}:${eventIdx}`
        : typeof eventIdx === 'number'
          ? `idx:${eventIdx}`
          : '';
    if (dedupeKey) {
      if (!this._processedEventIndices) {
        this._processedEventIndices = new Set();
      }

      if (this._processedEventIndices.has(dedupeKey)) {
        if (
          [
            'ai_message_start',
            'thinking_start',
            'thinking_chunk',
            'thinking_end',
            'text_start',
            'text_chunk',
            'text_end'
          ].includes(eventType)
        ) {
          restoreDebugLog('event:drop-duplicate', {
            eventType,
            eventIdx,
            dedupeKey,
            currentConversationId: this.currentConversationId
          });
        }
        debugLog(`[TaskPolling] 跳过重复事件 ${dedupeKey}`);
        return;
      }

      this._processedEventIndices.add(dedupeKey);

      // 限制 Set 大小（保留最近 1000 个）
      if (this._processedEventIndices.size > 1000) {
        const firstValue = this._processedEventIndices.values().next().value;
        this._processedEventIndices.delete(firstValue);
      }
    }

    debugLog(`[TaskPolling] 处理事件 #${eventIdx}: ${eventType}`, eventData);

    // 「等待 API 响应」状态维护：api_request_start 置位；任何「响应开始」
    // （thinking_start/text_start/tool_preparing）或任务终结信号都清除。
    // 历史事件重放时按序经过此处，最终状态自然收敛正确。
    if (eventType === 'api_request_start') {
      this.apiRequestPending = true;
    } else if (
      eventType === 'thinking_start' ||
      eventType === 'text_start' ||
      eventType === 'tool_preparing' ||
      eventType === 'task_complete' ||
      eventType === 'task_stopped' ||
      eventType === 'error'
    ) {
      this.apiRequestPending = false;
    }

    // 根据事件类型调用对应的处理方法
    switch (eventType) {
      case 'api_request_start':
        // 状态已在上方统一维护，无其他处理
        break;

      case 'ai_message_start':
        this.handleAiMessageStart(eventData, eventIdx);
        break;

      case 'thinking_start':
        this.handleThinkingStart(eventData, eventIdx);
        break;

      case 'thinking_chunk':
        this.handleThinkingChunk(eventData, eventIdx);
        break;

      case 'thinking_end':
        this.handleThinkingEnd(eventData, eventIdx);
        break;

      case 'text_start':
        this.handleTextStart(eventData, eventIdx);
        break;

      case 'text_chunk':
        this.handleTextChunk(eventData, eventIdx);
        break;

      case 'text_end':
        this.handleTextEnd(eventData, eventIdx);
        break;

      case 'tool_preparing':
        this.handleToolPreparing(eventData, eventIdx);
        break;

      case 'tool_start':
        this.handleToolStart(eventData, eventIdx);
        break;

      case 'tool_intent':
        this.handleToolIntent(eventData, eventIdx);
        break;

      case 'tool_update_action':
      case 'update_action':
        this.handleToolUpdateAction(eventData, eventIdx);
        break;
      case 'tool_approval_required':
        this.handleToolApprovalRequired(eventData, eventIdx);
        break;
      case 'tool_approval_resolved':
        this.handleToolApprovalResolved(eventData, eventIdx);
        break;
      case 'user_question_required':
      case 'user_questions_required':
        this.handleUserQuestionsRequired(eventData, eventIdx);
        break;
      case 'user_question_resolved':
      case 'user_questions_resolved':
        this.handleUserQuestionsResolved(eventData, eventIdx);
        break;
      case 'plan_approval_required':
        this.handlePlanApprovalRequired(eventData, eventIdx);
        break;
      case 'plan_approval_resolved':
        this.handlePlanApprovalResolved(eventData, eventIdx);
        break;
      case 'auto_approval_progress':
        this.handleAutoApprovalProgress(eventData, eventIdx);
        break;

      case 'goal_progress':
        this.handleGoalProgress?.(eventData, eventIdx);
        break;

      case 'goal_review_progress':
        this.handleGoalReviewProgress?.(eventData, eventIdx);
        break;

      case 'goal_completed':
        this.handleGoalCompleted?.(eventData, eventIdx);
        break;

      case 'goal_stopped':
        this.handleGoalStopped?.(eventData, eventIdx);
        break;

      case 'workflow_progress':
        this.handleWorkflowProgress?.(eventData, eventIdx);
        break;

      case 'workflow_review_progress':
        this.handleWorkflowReviewProgress?.(eventData, eventIdx);
        break;

      case 'append_payload':
        this.handleAppendPayload(eventData, eventIdx);
        break;

      case 'modify_payload':
        this.handleModifyPayload(eventData, eventIdx);
        break;

      case 'task_complete':
        this.handleTaskComplete(eventData, eventIdx);
        break;

      case 'task_stopped':
        this.handleTaskStopped(eventData, eventIdx);
        break;

      case 'error':
        this.handleTaskError(eventData, eventIdx);
        break;

      case 'token_update':
        this.handleTokenUpdate(eventData, eventIdx);
        break;

      case 'conversation_changed':
        this.handleConversationChanged(eventData, eventIdx);
        break;

      case 'conversation_resolved':
        this.handleConversationResolved(eventData, eventIdx);
        break;

      case 'todo_updated':
        // 任务期广播的待办快照（创建/勾选/清空都会触发）。live=true 让窗口播动画。
        // 快照直接可用；缺失时退化为 REST 拉取（fetchTodoList 带 conversation_id）。
        if (eventData && 'todo_list' in eventData) {
          this.fileSetTodoList(eventData.todo_list || null, true);
        } else if (typeof this.scheduleTodoListRefresh === 'function') {
          this.scheduleTodoListRefresh(0);
        }
        break;

      case 'edited_files_updated':
        // 快捷窗口文件记录：edit/write/delete/rename 后广播，payload 携带最新列表
        useQuickDockStore().setEditedFiles(
          Array.isArray(eventData?.edited_files) ? eventData.edited_files : [],
          true
        );
        break;

      case 'edit_summary_updated':
        // 编辑摘要卡片：合并 diff 实时写入发起工作的 user 消息 metadata。
        // 重放历史事件时跳过——历史消息的 metadata 已随对话加载落地，
        // 重写反而可能用旧事件覆盖最新状态。
        if (this._rebuildingFromScratch) break;
        useChatStore().updateEditSummaryByMessageId(
          typeof eventData?.message_id === 'string' ? eventData.message_id : '',
          eventData?.edit_summary || null
        );
        break;
      case 'compression_state':
        // 重放历史事件时不处理管理类事件，避免触发副作用（如 toast 闪烁 / 状态回退）。
        if (this._rebuildingFromScratch) break;
        this.handleCompressionState(eventData, eventIdx);
        break;
      case 'compression_finished':
        // 重放历史事件时不处理管理类事件，避免 restoreTaskState → 重放 → 再次触发 compression_finished 的无限循环。
        if (this._rebuildingFromScratch) break;
        this.handleCompressionFinished(eventData, eventIdx);
        break;
      case 'shallow_compression':
        // 同上。
        if (this._rebuildingFromScratch) break;
        this.handleShallowCompression(eventData, eventIdx);
        break;

      case 'user_message':
        debugNotifyLog('[DEBUG_NOTIFY][event] dispatch:user_message', {
          idx: eventIdx,
          data: eventData
        });
        this.handleUserMessage(eventData, eventIdx);
        break;

      case 'system_message':
        this.handleSystemMessage(eventData, eventIdx);
        break;
      case 'runtime_queue_sync':
        // 从头重建期间跳过 runtime_queue_sync，避免与已加载的历史冲突导致 UI 闪回卡死
        if (this._rebuildingFromScratch) break;
        this.handleRuntimeQueueSync(eventData);
        break;

      default:
        debugLog(`[TaskPolling] 未知事件类型: ${eventType}`);
    }

    // 注意：不要在这里清除 _rebuildingFromScratch 标记
    // 该标记应该在恢复完所有历史事件后才清除
  },
  handleTaskComplete(data: any) {
    const pendingToolsBefore =
      typeof this.hasPendingToolActions === 'function' ? this.hasPendingToolActions() : null;
    const pendingRuntimeGuidance = Array.isArray(data?.pending_runtime_guidance_messages)
      ? data.pending_runtime_guidance_messages
          .map((item: any) => String(item || '').trim())
          .filter((item: string) => item.length > 0)
      : [];
    if (pendingRuntimeGuidance.length > 0) {
      const limit = Math.max(1, Number(this.runtimeQueueLimit || 5));
      const mergedGuidance = [
        ...(this.runtimeGuidanceFallbackQueue || []),
        ...pendingRuntimeGuidance
      ]
        .map((item: any) => String(item || '').trim())
        .filter((item: string) => item.length > 0)
        .slice(0, limit);
      const queueAllowance = Math.max(0, limit - mergedGuidance.length);
      if (
        Array.isArray(this.runtimeQueuedMessages) &&
        this.runtimeQueuedMessages.length > queueAllowance
      ) {
        this.runtimeQueuedMessages = this.runtimeQueuedMessages.slice(0, queueAllowance);
      }
      this.runtimeGuidanceFallbackQueue = mergedGuidance;
    }
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

    // 同步处理状态更新
    this.streamingMessage = false;
    this.stopRequested = false;
    // 兜底清理可能残留的流式状态（正常流程 thinking_end/text_end 已清，此处幂等）
    this.chatClearStreamingResidualState?.();
    this.apiRequestPending = false;
    if (!hasRunningSubAgents && !hasRunningMultiAgent) {
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
    } else if (hasRunningSubAgents) {
      this.taskInProgress = true;
      this.waitingForSubAgent = true;
      this.waitingForBackgroundCommand = hasRunningBackgroundCommands;
      // 关键修复：主任务结束后将切到新的通知任务，先清空旧任务事件索引去重表。
      this.clearProcessedEvents();
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
      this.clearTaskState(); // 清理任务状态
      this.$nextTick(() => {
        if (typeof this.tryAutoSendRuntimeQueuedMessages === 'function') {
          this.tryAutoSendRuntimeQueuedMessages('task_complete');
        }
      });
    }

    this.$forceUpdate();

    // 只更新统计，不重新加载历史
    if (this.currentConversationId) {
      setTimeout(() => {
        this.fetchConversationTokenStatistics();
        this.updateCurrentContextTokens();
      }, 500);
    }
    this.scheduleTodoListRefresh(100);
    if (data?.conversation_id && data.conversation_id === this.currentConversationId && data?.task_id) {
      this.acknowledgeCompletedWorkspaceTask?.(data.task_id);
    }
    setTimeout(() => this.refreshRunningWorkspaceTasks?.(), 0);
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
      data,
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
      this.clearTaskState();
      this.$nextTick(() => {
        if (typeof this.tryAutoSendRuntimeQueuedMessages === 'function') {
          this.tryAutoSendRuntimeQueuedMessages('task_stopped');
        }
      });
    }

    goalModeDebugLog('handleTaskStopped:after', {
      taskInProgress: this.taskInProgress,
      waitingForSubAgent: this.waitingForSubAgent,
      waitingForBackgroundCommand: this.waitingForBackgroundCommand,
      hasRunningBackground,
    });

    this.scheduleTodoListRefresh(100);
    setTimeout(() => this.refreshRunningWorkspaceTasks?.(), 0);
    this.$forceUpdate();
    jsonDebug('handleTaskStopped:after', {
      taskInProgress: this.taskInProgress,
      streamingMessage: this.streamingMessage,
      stopRequested: this.stopRequested
    });
  },
  clearTaskState() {
    this.stopWaitingTaskProbe();
    (async () => {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      taskStore.clearTask(); // 使用 clearTask 而不是 stopPolling
    })();

    if (typeof this.clearProcessedEvents === 'function') {
      this.clearProcessedEvents();
    }
  },
  handleTaskError(data: any) {
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

      this.uiPushToast({
        title: t('appTasks.retrySoonTitle'),
        message: t('appTasks.retryInSeconds', {
          n: retryIn,
          attempt,
          max: maxAttempts,
          error: data?.message || t('common.unknownError')
        }),
        type: 'info',
        duration: Math.max(retryIn, 1) * 1000
      });
      return;
    }

    const errorMessage = data.message || t('common.unknownError');
    const errorType = data.error_type || 'unknown';
    const isToolArgumentParseError =
      // 双语匹配后端 modules/i18n.py tool.param_parse_failed 的 zh/en 产出（\u 转义仅过审计）
      errorType === 'parameter_format_error' || /(?:\u5de5\u5177\u53c2\u6570\u89e3\u6790\u5931\u8d25|Failed to parse tool arguments)/.test(String(errorMessage || ''));

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
      this.uiPushToast({
        title: t('appTasks.toolCallFailed'),
        message: errorMessage,
        type: 'warning',
        duration: 6000
      });
      // 保持当前运行态，不在这里强制置为“工作中”，避免后续任务结束时状态粘住
      this.stopRequested = false;
      this.$forceUpdate();
      return;
    }

    let title = t('appTasks.taskFailedTitle');
    let message = errorMessage;

    // 根据错误类型提供友好提示
    if (errorType === 'api_error') {
      title = t('appTasks.apiErrorTitle');
      message = t('appTasks.apiErrorMessage', { error: errorMessage });
    } else if (errorType === 'timeout') {
      title = t('appTasks.timeoutTitle');
      message = t('appTasks.timeoutMessage');
    } else if (errorType === 'quota_exceeded') {
      title = t('appTasks.quotaTitle');
      message = t('appTasks.quotaMessage');
    }

    console.error('[TaskPolling] 任务错误:', data.message);
    this.uiPushToast({
      title,
      message,
      type: 'error',
      duration: 8000
    });

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

    // 停止轮询
    (async () => {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      taskStore.stopPolling();
    })();
    this.$nextTick(() => {
      if (typeof this.tryAutoSendRuntimeQueuedMessages === 'function') {
        this.tryAutoSendRuntimeQueuedMessages('task_error');
      }
    });
  }
};

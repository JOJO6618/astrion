// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { useQuickDockStore } from '../../../stores/quickDock';
import { usePreviewStore } from '../../../stores/preview';
import { useChatStore } from '../../../stores/chat';
import { debugNotifyLog, keyNotifyLog, jsonDebug } from './shared';
import { completionMethods } from './completion';

export const lifecycleMethods = {
  handleTaskEvent(event: any) {
    return this.dispatchTaskEvent(event);
  },
  dispatchTaskEvent(event: any) {
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
    const eventConversationMismatch =
      !!eventData.conversation_id && eventData.conversation_id !== this.currentConversationId;
    if (eventConversationMismatch) return;
    // 同对话也可能已开始下一任务；终态必须精确匹配当前 task_id。
    if (eventData.task_id && eventData.task_id !== taskStore.currentTaskId) {
      debugLog(
        `[TaskPolling] Ignoring mismatched task event #${eventIdx}: ${eventType}, event task=${eventData.task_id}, current task=${taskStore.currentTaskId}`
      );
      return;
    }

    debugLog(`[TaskPolling] 处理事件 #${eventIdx}: ${eventType}`, eventData);

    // 「等待 API 响应」状态维护：api_request_start 置位；任何「响应开始」
    // （thinking_start/text_start/tool_preparing）或任务终结信号都清除。
    if (eventType === 'api_request_start') {
      this.apiRequestPending = true;
      // Every request starts a distinct tool batch, even without reasoning output.
      this._summaryToolBatchSequence = (this._summaryToolBatchSequence || 0) + 1;
      this._summaryToolBatchId = `request-${this._summaryToolBatchSequence}`;
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
        this.handleApiRequestStart(eventData, eventIdx);
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

      case 'stream_reset':
        // 断流重试「清除重来」：清理本轮 attempt 半截内容；
        this.handleStreamReset(eventData, eventIdx);
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

      case 'preview_targets_updated':
        // 预览面板目标：文件编辑/命令检测/模型输出链接实时登记后广播
        // live=true 供自动展开判定（设置项 preview_auto_open）
        usePreviewStore().setTargets(
          Array.isArray(eventData?.preview_targets) ? eventData.preview_targets : [],
          true
        );
        break;

      case 'edit_summary_updated':
        // 编辑摘要卡片：合并 diff 实时写入发起工作的 user 消息 metadata。
        useChatStore().updateEditSummaryByMessageId(
          typeof eventData?.message_id === 'string' ? eventData.message_id : '',
          eventData?.edit_summary || null
        );
        break;
      case 'compression_state':
        this.handleCompressionState(eventData, eventIdx);
        break;
      case 'compression_finished':
        this.handleCompressionFinished(eventData, eventIdx);
        break;
      case 'shallow_compression':
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
        this.handleRuntimeQueueSync(eventData);
        break;
      case 'event_window_gap':
        // 事件窗口缺口（协议 §5.2/§5.4）：中间事件已被服务端裁剪，
        // 统一快照负责恢复消息与新水位。
        debugLog('[TaskPolling] 事件窗口缺口，触发会话快照对账:', eventData);
        if ((window as any).__vueApp?.uiPushToast) {
          (window as any).__vueApp.uiPushToast({
            title: t('stores.eventWindowGap'),
            message: t('stores.eventWindowGapReload'),
            type: 'warning',
            duration: 5000
          });
        }
        Promise.resolve(this.refreshConversationSnapshot()).catch((error) => {
          debugLog('[TaskPolling] gap snapshot unavailable', String(error));
        });
        break;

      default:
        debugLog(`[TaskPolling] 未知事件类型: ${eventType}`);
    }
  },
  ...completionMethods
};

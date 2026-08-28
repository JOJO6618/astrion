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

export const compressionMethods = {
  handleCompressionState(data: any) {
    if (!data || typeof data !== 'object') {
      return;
    }
    const wasInProgress = !!this.compressionInProgress;
    this.compressionInProgress = !!data.in_progress;
    // 记录压缩所属对话：压缩锁只作用于该对话，不影响其他对话与 /new 新建页
    this.compressionConversationId = this.compressionInProgress
      ? (data.conversation_id || this.currentConversationId || null)
      : null;
    this.compressionMode = data.mode || '';
    this.compressionStage = data.stage || '';
    if (this.compressionInProgress && !wasInProgress) {
      const modeLabel = this.compressionMode === 'manual' ? t('appTasks.compressionManual') : t('appTasks.compressionAuto');
      if (this.compressionToastId) {
        this.uiDismissToast(this.compressionToastId);
        this.compressionToastId = null;
      }
      this.compressionToastId = this.uiPushToast({
        title: t('appTasks.compressing'),
        message: t('appTasks.compressingMessage', { mode: modeLabel }),
        type: 'info',
        duration: null,
        closable: false
      });
    }
    if (!this.compressionInProgress) {
      this.compressionError = data.error || '';
      if (this.compressionToastId) {
        this.uiDismissToast(this.compressionToastId);
        this.compressionToastId = null;
      }
    }
  },
  handleShallowCompression(data: any, eventIdx?: number) {
    const count = Number(data?.compressed_count || 0);
    if (count <= 0) {
      return;
    }
    debugLog('[TaskPolling] 自动浅层压缩触发, idx:', eventIdx, data);
    this.uiPushToast({
      title: t('appTasks.shallowCompressionTitle'),
      message: t('appTasks.shallowCompressedMessage', { n: count }),
      type: 'info',
      duration: 2500
    });
  },
  async handleCompressionFinished(data: any) {
    if (this.compressionToastId) {
      this.uiDismissToast(this.compressionToastId);
      this.compressionToastId = null;
    }
    this.compressionInProgress = false;
    this.compressionConversationId = null;
    this.compressionMode = '';
    this.compressionStage = '';
    this.compressionError = '';
    const newId = data?.conversation_id;
    const isInPlace = newId && newId === this.currentConversationId;
    if (newId && !isInPlace) {
      // 旧行为兼容：压缩产生了新对话 id（非 in-place），需要切换并重新加载。
      await this.loadConversation(newId, { force: true });
      this.conversationsOffset = 0;
      if (typeof this.loadConversationsList === 'function') {
        await this.loadConversationsList();
      }
      await this.refreshRunningWorkspaceTasks?.();
      await this.restoreTaskState?.();
    }
    // in-place 压缩：对话 id 不变，不重新加载对话，也不触发 restoreTaskState。
    // loadConversation 会 clearTask 停止轮询才需要 restore；in-place 跳过了
    // loadConversation，轮询仍在运行，restoreTaskState 的 rebuild 反而会
    this.uiPushToast({
      title: t('appTasks.compressionComplete'),
      message: t('appTasks.compressedEarlierContent'),
      type: 'success',
      duration: 2400
    });
  },
  async restoreTaskState(options = {}) {
    // 统一加载协议快速路径：bootstrap 已聚合任务摘要/全量事件/判据输入，
    // 跳过 GET /api/tasks、历史死等与 GET /api/tasks/{id} 三次请求，行为与原有逻辑等价。
    const bootstrapReplay = options && options.bootstrapReplay ? options.bootstrapReplay : null;
    // 清理已处理的事件索引
    this.clearProcessedEvents();
    // 启动运行状态对账循环（幂等）：事件负责即时性，对账负责正确性
    if (typeof this.startRunningStateReconcile === 'function') {
      this.startRunningStateReconcile();
    }
    restoreDebugLog('restore:start', {
      currentConversationId: this.currentConversationId,
      streamingMessage: this.streamingMessage,
      taskInProgress: this.taskInProgress,
      historyLoading: this.historyLoading,
      historyLoadingFor: this.historyLoadingFor,
      messagesLen: Array.isArray(this.messages) ? this.messages.length : -1
    });

    // 显式新建对话路由（/new、/multiagent/new）与独立全屏路由（工作流编辑器等）：
    // 前者是用户主动选择的空白页，后者不归对话体系。这里不能接管任务/重放事件/
    // 恢复轮询——loadRunningTask(null) 会匹配任意运行中任务，一旦接管，该对话的
    // 用户输入/AI输出/工具调用会被全量重放到空白页/被覆盖的隐藏视图（刷新也复现），
    // 压缩完成等回调还会 loadConversation 改写 URL。
    // 侧边栏运行标识由 refreshRunningWorkspaceTasks 独立维护，不受影响。
    const onIndependentRoute =
      typeof this.isConversationIndependentRoute === 'function' &&
      this.isConversationIndependentRoute();
    if (
      !this.currentConversationId &&
      ((typeof this.isExplicitNewConversationRoute === 'function' &&
        this.isExplicitNewConversationRoute()) ||
        onIndependentRoute)
    ) {
      restoreDebugLog('restore:skip-explicit-new-route', {
        path: typeof window !== 'undefined' ? window.location.pathname : ''
      });
      this._restoreTaskStateWaitCount = 0;
      return;
    }

    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();

      // 如果已经在流式输出中，不重复恢复
      if (this.streamingMessage || this.taskInProgress) {
        restoreDebugLog('restore:skip-already-running', {
          streamingMessage: this.streamingMessage,
          taskInProgress: this.taskInProgress,
          currentConversationId: this.currentConversationId
        });
        debugLog('[TaskPolling] 任务已在进行中，跳过恢复', {
          streamingMessage: this.streamingMessage,
          taskInProgress: this.taskInProgress,
          currentConversationId: this.currentConversationId
        });
        return;
      }

      // 查找运行中的任务（快速路径：复用 bootstrap 任务摘要，
      // 复刻 loadRunningTask 的 store 设置与续播偏移量）
      let runningTask;
      if (bootstrapReplay && bootstrapReplay.task && bootstrapReplay.task.task_id) {
        runningTask = bootstrapReplay.task;
        taskStore.currentTaskId = runningTask.task_id;
        taskStore.taskStatus = runningTask.status;
        taskStore.taskCreatedAt = runningTask.created_at;
        taskStore.taskUpdatedAt = runningTask.updated_at;
        taskStore.pollingError = null;
        taskStore.pollingErrorCount = 0;
        taskStore.pollingWarned = false;
        taskStore.runtimeQueueSnapshotKey = '';
        taskStore.lastEventIndex =
          typeof bootstrapReplay.replay_from === 'number'
            ? bootstrapReplay.replay_from
            : (Array.isArray(bootstrapReplay.events) ? bootstrapReplay.events.length : 0);
      } else {
        runningTask = await taskStore.loadRunningTask(this.currentConversationId);
      }

      if (!runningTask) {
        restoreDebugLog('restore:no-running-task', {
          currentConversationId: this.currentConversationId
        });
        debugLog('[TaskPolling] 没有运行中的任务', {
          currentConversationId: this.currentConversationId
        });
        this._restoreTaskStateWaitCount = 0;
        await this.restoreSubAgentWaitingState();
        return;
      }
      if (
        (this.versioningHostMode || this.dockerProjectMode) &&
        this.currentHostWorkspaceId &&
        runningTask?.workspace_id &&
        runningTask.workspace_id !== this.currentHostWorkspaceId
      ) {
        restoreDebugLog('restore:skip-workspace-mismatch', {
          taskId: runningTask?.task_id,
          taskWorkspaceId: runningTask?.workspace_id,
          currentHostWorkspaceId: this.currentHostWorkspaceId
        });
        taskStore.clearTask();
        await this.restoreSubAgentWaitingState();
        return;
      }

      debugLog('[TaskPolling] 发现运行中的任务，开始恢复状态', {
        taskId: runningTask?.task_id,
        status: runningTask?.status,
        conversationId: runningTask?.conversation_id
      });
      if (runningTask?.goal_mode) {
        const existingGoalProgress = this.goalProgress && typeof this.goalProgress === 'object'
          ? this.goalProgress
          : {};
        const taskGoalProgress = runningTask?.goal_progress && typeof runningTask.goal_progress === 'object'
          ? runningTask.goal_progress
          : {};
        this.goalRunning = true;
        this.goalModeArmed = false;
        this.goalProgress = {
          ...existingGoalProgress,
          ...taskGoalProgress,
          goal: taskGoalProgress?.goal || existingGoalProgress?.goal || runningTask?.message || '',
          status: 'running',
          turn_count: Number(taskGoalProgress?.turn_count ?? existingGoalProgress?.turn_count ?? 0),
          tokens_used: Number(taskGoalProgress?.tokens_used ?? existingGoalProgress?.tokens_used ?? 0),
          tool_calls: Number(taskGoalProgress?.tool_calls ?? existingGoalProgress?.tool_calls ?? 0),
          duration_seconds: Number(taskGoalProgress?.duration_seconds ?? existingGoalProgress?.duration_seconds ?? 0)
        };
      }
      restoreDebugLog('restore:running-task-found', {
        taskId: runningTask?.task_id,
        status: runningTask?.status,
        taskConversationId: runningTask?.conversation_id,
        currentConversationId: this.currentConversationId
      });

      // 检查历史是否已加载（快速路径：历史已由 enterConversation 渲染，跳过死等）
      const hasMessages = Array.isArray(this.messages) && this.messages.length > 0;
      const historyLoadingSameConversation =
        !!this.historyLoading && this.historyLoadingFor === this.currentConversationId;

      if (!hasMessages && !bootstrapReplay) {
        this._restoreTaskStateWaitCount = (this._restoreTaskStateWaitCount || 0) + 1;
        const waitedTooLong = this._restoreTaskStateWaitCount >= 8; // ~4s
        restoreDebugLog('restore:history-empty', {
          waitCount: this._restoreTaskStateWaitCount,
          waitedTooLong,
          historyLoading: this.historyLoading,
          historyLoadingFor: this.historyLoadingFor,
          currentConversationId: this.currentConversationId
        });
        if (historyLoadingSameConversation && !waitedTooLong) {
          debugLog('[TaskPolling] 历史未加载，等待历史加载完成', {
            waitCount: this._restoreTaskStateWaitCount,
            historyLoading: this.historyLoading,
            historyLoadingFor: this.historyLoadingFor
          });
          setTimeout(() => {
            this.restoreTaskState();
          }, 500);
          return;
        }
        // 兜底：即使历史未成功拉取，也允许直接通过任务事件重放恢复界面，
        // 避免 show_html 流式阶段刷新后“本次请求内容完全消失”。
        debugLog('[TaskPolling] 历史为空，启用事件重放兜底恢复', {
          waitCount: this._restoreTaskStateWaitCount,
          historyLoading: this.historyLoading,
          historyLoadingFor: this.historyLoadingFor
        });
      } else {
        this._restoreTaskStateWaitCount = 0;
      }

      debugLog('[TaskPolling] 开始精细恢复', {
        hasMessages,
        historyLoading: this.historyLoading,
        historyLoadingFor: this.historyLoadingFor
      });

      // 获取任务的所有事件（快速路径：bootstrap 已聚合全量事件，跳过详情请求）
      let allEvents;
      if (bootstrapReplay) {
        allEvents = Array.isArray(bootstrapReplay.events) ? bootstrapReplay.events : [];
      } else {
        const detailResponse = await fetch(`/api/tasks/${taskStore.currentTaskId}`);
        if (!detailResponse.ok) {
          debugLog('[TaskPolling] 获取任务详情失败');
          return;
        }

        const detailResult = await detailResponse.json();
        if (!detailResult.success || !detailResult.data.events) {
          restoreDebugLog('restore:task-detail-invalid', {
            ok: !!detailResult?.success,
            hasData: !!detailResult?.data,
            hasEvents: !!detailResult?.data?.events
          });
          debugLog('[TaskPolling] 任务详情无效');
          return;
        }

        allEvents = detailResult.data.events;
      }
      debugLog(`[TaskPolling] 获取到 ${allEvents.length} 个事件`);
      restoreDebugLog('restore:task-detail-events', {
        total: allEvents.length,
        firstIdx: allEvents[0]?.idx,
        firstType: allEvents[0]?.type,
        lastIdx: allEvents[allEvents.length - 1]?.idx,
        lastType: allEvents[allEvents.length - 1]?.type
      });

      // 找到最后一条消息
      const lastMessage = this.messages[this.messages.length - 1];
      const isAssistantMessage = lastMessage && lastMessage.role === 'assistant';

      // console.log('[TaskPolling] 最后一条消息:', {
      //     exists: !!lastMessage,
      //     role: lastMessage?.role,
      //     actionsCount: lastMessage?.actions?.length || 0,
      //     isAssistant: isAssistantMessage
      // });

      // 先分析事件状态（用于判定是否强制重建）
      // 快速路径：分析输入直接取自 bootstrap 的服务端判据（语义与下方循环等价）
      const injectedDecision = bootstrapReplay ? bootstrapReplay.decision_inputs || null : null;
      let inThinking = false;
      let inText = false;
      let hasTextChunkEvent = false;
      let hasAssistantResponseEvent = false;
      let hasAssistantContentEvent = false;

      if (injectedDecision) {
        inThinking = !!injectedDecision.in_thinking;
        inText = !!injectedDecision.in_text;
        hasTextChunkEvent = !!injectedDecision.has_text_chunk_event;
        hasAssistantResponseEvent = !!injectedDecision.has_assistant_response_event;
        hasAssistantContentEvent = !!injectedDecision.has_assistant_content_event;
      } else {
        for (let i = 0; i < allEvents.length; i++) {
          const event = allEvents[i];
          const eventType = String(event?.type || '');
          if (
            [
              'ai_message_start',
              'thinking_start',
              'thinking_chunk',
              'thinking_end',
              'text_start',
              'text_chunk',
              'text_end',
              'tool_preparing',
              'tool_start',
              'tool_update',
              'tool_complete'
            ].includes(eventType)
          ) {
            hasAssistantResponseEvent = true;
          }
          if (
            [
              'thinking_chunk',
              'text_chunk',
              'tool_preparing',
              'tool_start',
              'tool_update',
              'tool_complete'
            ].includes(eventType)
          ) {
            hasAssistantContentEvent = true;
          }
          if (event.type === 'thinking_start') {
            inThinking = true;
          } else if (event.type === 'thinking_end') {
            inThinking = false;
          }
          if (event.type === 'text_start') {
            inText = true;
          } else if (event.type === 'text_end') {
            inText = false;
          }
          if (event.type === 'text_chunk') {
            hasTextChunkEvent = true;
          }
        }
      }

      // 检查是否需要从头重建
      // 1. 最后一条不是 assistant 消息
      // 2. 最后一条是空的 assistant 消息
      // 3. 事件数量远大于历史中的 actions 数量（说明历史不完整）
      // 4. 任务正处于文本流式阶段（刷新时易丢分段内容，强制重放全部事件）
      const forceRebuildForStreamingText = inText || hasTextChunkEvent || inThinking;

      // 检查 assistant 消息的 actions 是否有仍在进行中的
      const hasInProgressActions = isAssistantMessage && Array.isArray(lastMessage.actions) && lastMessage.actions.some((action: any) => {
        if (!action) return false;
        if (action.streaming) return true;
        if (action.type === 'tool' && action.tool) {
          const status = String(action.tool.status || '').toLowerCase();
          if (['preparing', 'running', 'pending', 'queued'].includes(status)) return true;
        }
        return false;
      });

      // 仅当确实需要时才重建，避免引导消息等场景下不必要的 assistant 消息移除和事件重放
      //
      // 【工具调用期间刷新重复显示修复】bootstrap 快速路径下必须采纳服务端判定。
      // 原因：renderHistoryMessages 会把历史中进行中的工具状态改写为 'stale'（避免
      // 按钮卡死），而上方 hasInProgressActions 只认 preparing/running/pending/queued，
      // 导致「思考→工具调用（无文本输出）期间刷新」时本地误判为无需重建；但服务端
      // 因文件态末条 assistant 带 tool_calls（工具结果未落盘）判定 needs_rebuild=True
      // 并把 replay_from 置 0。此时若误入 follow 分支，轮询会从事件 0 全量重放，
      // handleAiMessageStart 再新建 assistant，同一段运行记录显示两遍。
      // 注意：思考→输出→工具调用 场景不会触发，因为 text_chunk 事件会让
      // forceRebuildForStreamingText 在两侧一致为 true，掩盖该分歧。
      // 方向考量：重建（重放）是安全方向，follow 是风险方向，故对服务端判定取 OR。
      const serverNeedsRebuild = !!(
        bootstrapReplay && bootstrapReplay.needs_rebuild === true
      );
      const serverHasPendingToolCalls = !!(
        injectedDecision && injectedDecision.has_pending_tool_calls === true
      );
      const needsRebuild =
        !isAssistantMessage ||
        (isAssistantMessage && (!lastMessage.actions || lastMessage.actions.length === 0)) ||
        forceRebuildForStreamingText ||
        hasInProgressActions ||
        serverNeedsRebuild ||
        serverHasPendingToolCalls;
      restoreDebugLog('restore:rebuild-decision', {
        needsRebuild,
        isAssistantMessage,
        hasAssistantResponseEvent,
        hasAssistantContentEvent,
        inText,
        hasTextChunkEvent,
        inThinking,
        forceRebuildForStreamingText,
        hasInProgressActions,
        serverNeedsRebuild,
        serverHasPendingToolCalls
      });

      if (needsRebuild) {
        debugLog('[TaskPolling] 需要从头重建 assistant 响应');

        // 定位「本次运行区间」：从消息末尾向前，越过 assistant 消息与运行期注入的
        // user 消息（运行中引导/通知、多智能体内联消息、压缩续接引导语），直到发起
        // 本次任务的 user 消息（或更早的历史消息）为止。
        //
        // 背景：事件重放会从 0 重建本次运行的全部 assistant 内容。若运行区间中夹着
        // 静态的运行期 user 消息（典型：运行中发送了引导对话、运行中触发对话压缩），
        // 只清空末条 assistant 会让引导/压缩消息之前的静态分段残留，重放又把全部
        // 内容重建到新 assistant 中，导致同一段运行记录显示两遍。
        //
        // 处理：复用运行区间内【第一条】assistant 作为重放容器（清空 actions 并绑定
        // currentMessageIndex），并移除区间内其后的所有消息——运行期注入的 user
        // 消息在任务事件流中都有对应 user_message 事件，会与后续 assistant 段一起
        // 由事件重放按原顺序重建，位置与运行时一致。
        const isMidRunRuntimeUserMessage = (msg: any): boolean => {
          if (!msg || msg.role !== 'user') {
            return false;
          }
          const meta = msg.metadata || {};
          if (meta.runtime_injected === true) {
            return true;
          }
          // 压缩续接引导语走任务递归入口持久化，没有 runtime_injected 标记，
          // 但它同样是运行中插入、且事件流中有对应 user_message 事件。
          const src = String(meta.message_source || '').trim().toLowerCase();
          return src === 'compression' || src === 'compression_handoff';
        };
        let runBoundaryIdx = this.messages.length - 1;
        while (runBoundaryIdx >= 0) {
          const msg = this.messages[runBoundaryIdx];
          if (msg && msg.role === 'assistant') {
            runBoundaryIdx -= 1;
            continue;
          }
          if (isMidRunRuntimeUserMessage(msg)) {
            runBoundaryIdx -= 1;
            continue;
          }
          break;
        }
        let rebuildContainer: any = null;
        let rebuildContainerIdx = -1;
        for (let j = Math.max(runBoundaryIdx + 1, 0); j < this.messages.length; j += 1) {
          if (this.messages[j] && this.messages[j].role === 'assistant') {
            rebuildContainer = this.messages[j];
            rebuildContainerIdx = j;
            break;
          }
        }

        // 关键修复：不要 pop assistant 消息，而是清空其 actions 并设置标记，
        // 让 handleAiMessageStart 检测到 isRefreshRestore 并复用现有消息容器。
        // 这样避免视觉闪回（消息不会突然消失）+ 新事件通过轮询增量追加，不会一次性同步处理几百个事件导致卡死。
        if (rebuildContainer) {
          debugLog('[TaskPolling] 复用现有 assistant 消息容器，清空 actions 等待事件重建', {
            hasAssistantContentEvent,
            rebuildContainerIdx,
            removedAfterContainer: this.messages.length - 1 - rebuildContainerIdx
          });
          // 移除重建容器之后的运行区间消息（运行期注入的 user 消息 / 后续 assistant
          // 段），它们会随事件重放按原位置重建；不移除会导致同一段运行记录显示两遍。
          if (rebuildContainerIdx < this.messages.length - 1) {
            this.messages.splice(rebuildContainerIdx + 1);
          }
          rebuildContainer.actions = [];
          // 如果事件流中已经有内容事件（tool/thinking/text），说明回复已经开始过，
          // 重放事件会恢复内容，不显示等待提示。
          // 如果还没有内容事件，说明回复还没开始，应该显示等待提示。
          rebuildContainer.awaitingFirstContent = !hasAssistantContentEvent;
          rebuildContainer.generatingLabel = rebuildContainer.generatingLabel || t('appTasks.thinkingLabel');
          // 清理旧的流式标记，确保新事件能正确设置
          if (typeof rebuildContainer.streaming === 'boolean') {
            rebuildContainer.streaming = true;
          }
          // 绑定流式写入索引，确保重放事件追加进该容器（否则 ensureAssistantMessage
          // 会因 currentMessageIndex=-1 新建 assistant，留下空壳并错位）。
          this.currentMessageIndex = rebuildContainerIdx;
        }

        this.streamingMessage = true;
        this.taskInProgress = true;
        if (!hasAssistantContentEvent && !rebuildContainer) {
          this.ensureRunningAssistantPlaceholder?.(runningTask, 'restore:no-visible-content-yet');
        }
        this.$forceUpdate();

        // 重置偏移量为 0，从头获取所有事件来重建 assistant 消息
        taskStore.lastEventIndex = 0;
        debugLog('[TaskPolling] 重置偏移量为 0，从头开始轮询');

        // 标记正在从头重建，用于后续处理
        this._rebuildingFromScratch = true;
        this._rebuildingEventCount = allEvents.length; // 记录当前事件总数

        (window as any).__taskEventHandler = (event: any) => {
          this.handleTaskEvent(event);
        };

        taskStore.startPolling((event: any) => {
          this.handleTaskEvent(event);
        });
        restoreDebugLog('restore:rebuild-polling-started', {
          lastEventIndex: taskStore.lastEventIndex,
          currentConversationId: this.currentConversationId
        });

        // 延迟清除重建标记，确保所有历史事件都处理完毕
        setTimeout(() => {
          debugLog('[TaskPolling] 历史事件处理完毕，清除重建标记');
          this._rebuildingFromScratch = false;
          this._rebuildingEventCount = 0;
        }, 2000);

        return;
      }

      // console.log('[TaskPolling] 分析结果:', {
      //     inThinking,
      //     inText,
      //     totalEvents: allEvents.length,
      //     lastEventType: allEvents[allEvents.length - 1]?.type,
      //     lastEventIdx: allEvents[allEvents.length - 1]?.idx
      // });

      // 恢复思考块状态
      if (lastMessage.actions) {
        // console.log('[TaskPolling] 历史中的 actions 详情:', lastMessage.actions.map((a, idx) => ({
        //     index: idx,
        //     type: a.type,
        //     id: a.id,
        //     hasContent: !!a.content,
        //     contentLength: a.content?.length || 0,
        //     toolName: a.tool?.name,
        //     hasBlockId: !!a.blockId,
        //     blockId: a.blockId,
        //     collapsed: a.collapsed,
        //     streaming: a.streaming
        // })));

        const thinkingActions = lastMessage.actions.filter((a) => a.type === 'thinking');
        // console.log('[TaskPolling] 思考块数量:', thinkingActions.length);

        if (inThinking && thinkingActions.length > 0) {
          // 正在思考中，检查最后一个思考块是否正在流式输出
          const lastThinking = thinkingActions[thinkingActions.length - 1];

          // 只有当思考块正在流式输出时才设置锁定状态（但不展开）
          if (lastThinking.streaming && lastThinking.blockId) {
            // console.log('[TaskPolling] 找到正在流式输出的思考块，设置锁定状态:', lastThinking.blockId);
            this.$nextTick(() => {
              this.chatSetThinkingLock(lastThinking.blockId, true);
            });
          }
        }

        // 确保所有思考块都是折叠状态
        for (const thinking of thinkingActions) {
          if (thinking.blockId) {
            thinking.collapsed = true;
          }
        }

        // 检查思考块状态（在设置之后）（已禁用）
        // thinkingActions.forEach((thinking, idx) => {
        //     console.log(`[TaskPolling] 思考块 ${idx} (设置后):`, {
        //         hasBlockId: !!thinking.blockId,
        //         blockId: thinking.blockId,
        //         collapsed: thinking.collapsed,
        //         contentLength: thinking.content?.length || 0
        //     });
        // });

        // 恢复文本块状态
        const textActions = lastMessage.actions.filter((a) => a.type === 'text');
        // console.log('[TaskPolling] 文本块数量:', textActions.length);

        if (inText && textActions.length > 0) {
          const lastText = textActions[textActions.length - 1];
          // console.log('[TaskPolling] 标记文本块为流式状态');
          lastText.streaming = true;
        }

        // 注册历史中的工具块到 toolActionIndex
        // 这样后续的 update_action 事件可以找到对应的块进行状态更新
        const toolActions = lastMessage.actions.filter((a) => a.type === 'tool');
        // console.log('[TaskPolling] 工具块数量:', toolActions.length);

        for (const toolAction of toolActions) {
          if (toolAction.tool && toolAction.tool.id) {
            // console.log('[TaskPolling] 注册工具块:', {
            //     id: toolAction.tool.id,
            //     name: toolAction.tool.name,
            //     status: toolAction.tool.status
            // });
            // 注册到 toolActionIndex
            this.toolRegisterAction(toolAction, toolAction.tool.id);
            // 如果有 executionId，也注册
            if (toolAction.tool.executionId) {
              this.toolRegisterAction(toolAction, toolAction.tool.executionId);
            }
            // 追踪工具调用
            if (toolAction.tool.name) {
              this.toolTrackAction(toolAction.tool.name, toolAction);
            }
          }
        }
      }

      // 标记状态为进行中
      this.streamingMessage = true;
      this.taskInProgress = true;

      // 设置 currentMessageIndex 指向最后一条 assistant 消息
      // 这样后续添加的 action 会添加到正确的消息中
      const lastMessageIndex = this.messages.length - 1;
      if (lastMessage && lastMessage.role === 'assistant') {
        this.currentMessageIndex = lastMessageIndex;
        // console.log('[TaskPolling] 设置 currentMessageIndex 为:', lastMessageIndex);
      }

      // 强制更新界面
      this.$forceUpdate();

      // 滚动到底部
      this.$nextTick(() => {
        this.conditionalScrollToBottom();
      });

      // 注册事件处理器到全局
      (window as any).__taskEventHandler = (event: any) => {
        this.handleTaskEvent(event);
      };

      // 启动轮询（从当前偏移量开始，只处理新事件）
      debugLog('[TaskPolling] 启动轮询，起始偏移量:', taskStore.lastEventIndex);

      taskStore.startPolling((event: any) => {
        this.handleTaskEvent(event);
      });
      restoreDebugLog('restore:polling-started-follow', {
        lastEventIndex: taskStore.lastEventIndex,
        currentConversationId: this.currentConversationId
      });

      this.uiPushToast({
        title: t('appTasks.taskRestoredTitle'),
        message: t('appTasks.taskRestoredMessage'),
        type: 'info',
        duration: 3000
      });
    } catch (error) {
      restoreDebugLog('restore:error', {
        message: error?.message || String(error)
      });
      console.error('[TaskPolling] 恢复任务状态失败:', error);
    }
  }
};

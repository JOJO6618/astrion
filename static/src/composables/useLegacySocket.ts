// @ts-nocheck
/**
 * WebSocket 连接管理（遗留代码）
 *
 * 注意：聊天流式事件（ai_message_start, thinking_*, text_*, tool_*）已废弃
 * 现在使用 REST API 轮询模式（/api/tasks）处理聊天消息
 *
 * 保留的功能：
 * - 系统事件：connect, disconnect, error
 * - 配额事件：quota_update, quota_notice, quota_exceeded
 * - 对话事件：conversation_changed, conversation_resolved, conversation_loaded
 * - 子智能体事件：sub_agent_waiting
 * - Token统计：token_update
 *
 * 待清理：
 * - 流式文本处理相关代码（streamingState, pendingToolEvents等）
 * - 聊天事件处理器中的复杂逻辑
 */
import { io as createSocketClient } from 'socket.io-client';
import { renderLatexInRealtime } from './useMarkdownRenderer';
import { getMessageVisibility, messageStartsWork } from '../utils/messageVisibility';
import { goalModeDebugLog } from '../app/methods/common';
import { useQuickDockStore } from '../stores/quickDock';
import { useChatStore } from '../stores/chat';

export async function initializeLegacySocket(ctx: any) {
  try {
    const SOCKET_DEBUG_LOGS_ENABLED = false;
    const socketLog = (...args: any[]) => {
      if (!SOCKET_DEBUG_LOGS_ENABLED) {
        return;
      }
      console.log(...args);
    };

    socketLog('初始化WebSocket连接...');

    const socketOptions = {
      transports: ['websocket', 'polling'],
      autoConnect: false
    };

    ctx.socket = createSocketClient('/', socketOptions);

    // 可开关的逐字打印功能，默认关闭
    const STREAMING_ENABLED = false;
    const STREAMING_CHAR_DELAY = 22;
    const STREAMING_FINALIZE_DELAY = 1000;
    const STREAMING_DEBUG = false;
    const STREAMING_DEBUG_HISTORY_LIMIT = 2000;
    const streamingState = {
      buffer: [] as string[],
      timer: null as number | null,
      completionTimer: null as number | null,
      apiCompleted: false,
      pendingCompleteContent: '' as string,
      renderedText: '' as string,
      activeMessageIndex: null as number | null,
      activeTextAction: null as any,
      ignoreThinking: false
    };

    const pendingToolEvents: Array<{ event: string; data: any; handler: () => void }> = [];

    const startIntentTyping = (action: any, intentText: any) => {
      if (!action || !action.tool) {
        return;
      }
      if (typeof intentText !== 'string') {
        return;
      }
      const text = intentText.trim();
      if (!text) {
        return;
      }
      if (action.tool.intent_full === text) {
        if (console && console.debug) {
        }
        return;
      }
      if (typeof console !== 'undefined' && console.debug) {
      }
      if (action.tool.intent_full === text && action.tool.intent_rendered === text) {
        return;
      }
      // 清理旧定时器
      if (action.tool.intent_typing_timer) {
        clearInterval(action.tool.intent_typing_timer);
        action.tool.intent_typing_timer = null;
      }
      action.tool.intent_full = text;
      action.tool.intent_rendered = '';
      const duration = 1000;
      const step = Math.max(10, Math.floor(duration / Math.max(1, text.length)));
      let index = 0;
      action.tool.intent_typing_timer = window.setInterval(() => {
        action.tool.intent_rendered = text.slice(0, index + 1);
        if (index === 0 && console && console.debug) {
        }
        index += 1;
        if (index >= text.length) {
          clearInterval(action.tool.intent_typing_timer);
          action.tool.intent_typing_timer = null;
          action.tool.intent_rendered = text;
          if (console && console.debug) {
          }
        }
        if (typeof ctx?.$forceUpdate === 'function') {
          ctx.$forceUpdate();
        }
      }, step);
    };

    const resetPendingToolEvents = () => {
      pendingToolEvents.length = 0;
    };

    const flushPendingToolEvents = () => {
      if (!pendingToolEvents.length) {
        return;
      }
      const queue = pendingToolEvents.splice(0);
      queue.forEach((item) => {
        try {
          item.handler();
        } catch (error) {
          console.warn('延后工具事件处理失败:', item.event, error);
        }
      });
    };

    const snapshotStreamingState = () => ({
      bufferLength: streamingState.buffer.length,
      timerActive: streamingState.timer !== null,
      completionTimerActive: streamingState.completionTimer !== null,
      apiCompleted: streamingState.apiCompleted,
      pendingLength: (streamingState.pendingCompleteContent || '').length,
      renderedLength: streamingState.renderedText.length,
      currentMessageIndex:
        typeof ctx?.currentMessageIndex === 'number' ? ctx.currentMessageIndex : null,
      messagesLength: Array.isArray(ctx?.messages) ? ctx.messages.length : null,
      streamingMessage: !!ctx?.streamingMessage
    });

    const streamingDebugHistory: Array<any> = [];

    const sanitizeBubbleText = (text: unknown) => {
      if (typeof text !== 'string') {
        return '';
      }
      return text.replace(/\r/g, '');
    };

    const getActiveMessage = () => {
      if (streamingState.activeMessageIndex === null) {
        return null;
      }
      const messages = ctx?.messages;
      if (!Array.isArray(messages)) {
        return null;
      }
      return messages[streamingState.activeMessageIndex] || null;
    };

    const ensureActiveMessageBinding = () => {
      if (
        streamingState.activeMessageIndex === null &&
        typeof ctx?.currentMessageIndex === 'number' &&
        ctx.currentMessageIndex >= 0
      ) {
        streamingState.activeMessageIndex = ctx.currentMessageIndex;
      }
      if (typeof ctx?.currentMessageIndex !== 'number' || ctx.currentMessageIndex < 0) {
        if (streamingState.activeMessageIndex !== null) {
          ctx.currentMessageIndex = streamingState.activeMessageIndex;
        }
      }
      if (!ctx.streamingMessage) {
        ctx.streamingMessage = true;
      }
      const msg = getActiveMessage();
      if (!msg && Array.isArray(ctx?.messages) && ctx.messages.length) {
        streamingState.activeMessageIndex = ctx.messages.length - 1;
      }
      return getActiveMessage();
    };

    const ensureActiveTextAction = () => {
      const msg = getActiveMessage();
      if (!msg || !Array.isArray(msg.actions) || !msg.actions.length) {
        return null;
      }
      const known = streamingState.activeTextAction;
      if (known && msg.actions.includes(known)) {
        return known;
      }
      for (let i = msg.actions.length - 1; i >= 0; i--) {
        const action = msg.actions[i];
        if (action && action.type === 'text') {
          streamingState.activeTextAction = action;
          return action;
        }
      }
      return null;
    };

    const fallbackAppendToActiveMessage = (text: string) => {
      const msg = ensureActiveMessageBinding();
      if (!msg) {
        return null;
      }
      if (typeof msg.streamingText !== 'string') {
        msg.streamingText = '';
      }
      msg.streamingText += text;
      const action = ensureActiveTextAction();
      if (!action) {
        return null;
      }
      if (typeof action.content !== 'string') {
        action.content = '';
      }
      action.content += text;
      action.streaming = action.streaming !== false;
      streamingState.activeTextAction = action;
      ctx.$forceUpdate();
      ctx.conditionalScrollToBottom();
      renderLatexInRealtime();
      return action;
    };

    const completeActiveTextAction = (fullContent: string) => {
      const msg = ensureActiveMessageBinding();
      if (!msg) {
        return false;
      }
      const action = ensureActiveTextAction();
      if (action) {
        action.streaming = false;
        const fallback =
          (typeof fullContent === 'string' && fullContent.length ? fullContent : action.content) ||
          '';
        action.content = fallback;
      }
      msg.streamingText = '';
      msg.currentStreamingType = null;
      return !!action;
    };

    const logStreamingDebug = (event: string, detail?: any) => {
      if (!STREAMING_DEBUG) {
        return;
      }
      const payload = typeof detail === 'undefined' ? snapshotStreamingState() : detail;
      const entry = {
        event,
        detail: payload,
        conversationId: ctx.currentConversationId || null,
        timestamp: Date.now()
      };
      streamingDebugHistory.push(entry);
      if (streamingDebugHistory.length > STREAMING_DEBUG_HISTORY_LIMIT) {
        streamingDebugHistory.shift();
      }
      try {
        window.__streamingDebugLogs = streamingDebugHistory.slice();
      } catch (error) {
        // ignore
      }
      console.log('[streaming-debug]', event, payload);
      try {
        if (ctx.socket && typeof ctx.socket.emit === 'function') {
          ctx.socket.emit('client_stream_debug_log', entry);
        }
      } catch (error) {
        console.warn('上报 streaming debug 日志失败:', error);
      }
    };

    const markStreamingIdleIfPossible = (source: string) => {
      try {
        if (!ctx) {
          return;
        }
        if (typeof ctx.maybeResetStreamingState === 'function') {
          const reset = ctx.maybeResetStreamingState(source);
          if (reset) {
            logStreamingDebug('streaming_idle_reset', { source });
          }
          return;
        }
        if (!ctx.streamingMessage) {
          return;
        }
        const hasPending =
          typeof ctx.hasPendingToolActions === 'function' ? ctx.hasPendingToolActions() : false;
        if (!hasPending) {
          ctx.streamingMessage = false;
          ctx.stopRequested = false;
          logStreamingDebug('streaming_idle_reset:fallback', { source });
        }
      } catch (error) {
        console.warn('自动结束流式状态失败:', error);
      }
    };

    const stopStreamingTimer = () => {
      if (streamingState.timer !== null) {
        clearTimeout(streamingState.timer);
        streamingState.timer = null;
        logStreamingDebug('stopStreamingTimer', snapshotStreamingState());
      }
    };

    const stopCompletionTimer = () => {
      if (streamingState.completionTimer !== null) {
        clearTimeout(streamingState.completionTimer);
        streamingState.completionTimer = null;
        logStreamingDebug('stopCompletionTimer', snapshotStreamingState());
      }
    };

    const finalizeStreamingText = (options?: { force?: boolean; allowIncomplete?: boolean }) => {
      const forceFlush = !!options?.force;
      const allowIncomplete = !!options?.allowIncomplete;
      logStreamingDebug('finalizeStreamingText:start', {
        forceFlush,
        allowIncomplete,
        snapshot: snapshotStreamingState()
      });
      if (!forceFlush) {
        if (streamingState.buffer.length) {
          logStreamingDebug('finalizeStreamingText:blocked-buffer', snapshotStreamingState());
          return false;
        }
        if (!allowIncomplete && !streamingState.apiCompleted) {
          logStreamingDebug(
            'finalizeStreamingText:blocked-api-incomplete',
            snapshotStreamingState()
          );
          return false;
        }
      }
      stopStreamingTimer();
      stopCompletionTimer();
      if (forceFlush && streamingState.buffer.length) {
        const remainder = streamingState.buffer.join('');
        streamingState.buffer.length = 0;
        if (remainder) {
          applyTextChunk(remainder);
          logStreamingDebug('finalizeStreamingText:force-flush-buffer', {
            flushedChars: remainder.length
          });
        }
      }
      const pendingText = streamingState.pendingCompleteContent || '';
      const renderedText = streamingState.renderedText || '';
      let finalText = pendingText || renderedText || '';
      let remainderToAppend = '';
      if (!pendingText) {
        finalText = renderedText;
      } else if (!renderedText) {
        finalText = pendingText;
        remainderToAppend = pendingText;
      } else if (pendingText.length < renderedText.length) {
        // 后端返回的最终内容比已渲染的还短，避免覆盖已展示的字符
        finalText = renderedText;
      } else if (pendingText.startsWith(renderedText)) {
        finalText = pendingText;
        remainderToAppend = pendingText.slice(renderedText.length);
      } else {
        finalText = pendingText;
      }
      logStreamingDebug('finalizeStreamingText:resolved-final-text', {
        pendingLength: pendingText.length,
        renderedLength: renderedText.length,
        remainderToAppendLength: remainderToAppend.length,
        finalLength: finalText.length
      });
      if (remainderToAppend) {
        applyTextChunk(remainderToAppend);
      }
      streamingState.pendingCompleteContent = '';
      streamingState.apiCompleted = false;
      streamingState.renderedText = '';
      ctx.chatCompleteTextAction(finalText || '');
      completeActiveTextAction(finalText || '');
      ctx.$forceUpdate();
      // 注意：不在这里重置 streamingMessage，因为可能还有工具调用在进行
      // streamingMessage 只在 task_complete 事件时重置
      logStreamingDebug('finalizeStreamingText:complete', snapshotStreamingState());
      streamingState.activeMessageIndex = null;
      streamingState.activeTextAction = null;
      markStreamingIdleIfPossible('finalizeStreamingText');
      flushPendingToolEvents();
      return true;
    };

    const scheduleFinalizationAfterDrain = () => {
      if (streamingState.buffer.length) {
        logStreamingDebug(
          'scheduleFinalizationAfterDrain:buffer-not-empty',
          snapshotStreamingState()
        );
        return;
      }
      if (streamingState.completionTimer !== null) {
        logStreamingDebug(
          'scheduleFinalizationAfterDrain:already-scheduled',
          snapshotStreamingState()
        );
        return;
      }
      logStreamingDebug('scheduleFinalizationAfterDrain:scheduled', snapshotStreamingState());
      streamingState.completionTimer = window.setTimeout(() => {
        streamingState.completionTimer = null;
        logStreamingDebug('scheduleFinalizationAfterDrain:timer-fired', snapshotStreamingState());
        finalizeStreamingText({ allowIncomplete: true });
      }, STREAMING_FINALIZE_DELAY);
    };

    const resetStreamingBuffer = () => {
      stopStreamingTimer();
      stopCompletionTimer();
      streamingState.buffer.length = 0;
      streamingState.apiCompleted = false;
      streamingState.pendingCompleteContent = '';
      streamingState.renderedText = '';
      streamingState.activeMessageIndex = null;
      streamingState.activeTextAction = null;
      resetPendingToolEvents();
      logStreamingDebug('resetStreamingBuffer', snapshotStreamingState());
    };

    const applyTextChunk = (text: string) => {
      if (!text) {
        return null;
      }
      ensureActiveMessageBinding();
      let action = ctx.chatAppendTextChunk(text);
      if (action) {
        ctx.$forceUpdate();
        ctx.conditionalScrollToBottom();
        renderLatexInRealtime();
      } else {
        action = fallbackAppendToActiveMessage(text);
      }
      streamingState.renderedText += text;
      const appended = !!action;
      logStreamingDebug('applyTextChunk', {
        chunkLength: text.length,
        appended,
        snapshot: snapshotStreamingState()
      });
      if (!appended) {
        logStreamingDebug('applyTextChunk:missing-target', {
          chunkLength: text.length,
          currentMessageIndex:
            typeof ctx?.currentMessageIndex === 'number' ? ctx.currentMessageIndex : null,
          messagesLength: Array.isArray(ctx?.messages) ? ctx.messages.length : null
        });
      }
      return action;
    };

    const drainStreamingBufferImmediately = () => {
      if (!streamingState.buffer.length) {
        return;
      }
      const chunk = streamingState.buffer.join('');
      streamingState.buffer.length = 0;
      applyTextChunk(chunk);
    };

    const scheduleStreamingFlush = () => {
      const shouldFlushImmediately = typeof document !== 'undefined' && document.hidden === true;
      if (shouldFlushImmediately) {
        stopStreamingTimer();
        drainStreamingBufferImmediately();
        if (streamingState.apiCompleted) {
          finalizeStreamingText({ force: true });
        } else {
          scheduleFinalizationAfterDrain();
        }
        return;
      }
      if (streamingState.timer !== null) {
        logStreamingDebug('scheduleStreamingFlush:timer-exists', snapshotStreamingState());
        return;
      }
      if (!streamingState.buffer.length) {
        logStreamingDebug('scheduleStreamingFlush:no-buffer', snapshotStreamingState());
        return;
      }
      logStreamingDebug('scheduleStreamingFlush:start', snapshotStreamingState());
      const process = () => {
        streamingState.timer = null;
        logStreamingDebug('scheduleStreamingFlush:tick', snapshotStreamingState());
        if (!streamingState.buffer.length) {
          logStreamingDebug('scheduleStreamingFlush:buffer-empty', snapshotStreamingState());
          return;
        }
        const piece = streamingState.buffer.shift();
        if (piece) {
          applyTextChunk(piece);
        }
        if (streamingState.buffer.length) {
          scheduleStreamingFlush();
        } else {
          logStreamingDebug('scheduleStreamingFlush:buffer-drained', snapshotStreamingState());
          scheduleFinalizationAfterDrain();
        }
      };
      streamingState.timer = window.setTimeout(process, STREAMING_CHAR_DELAY);
    };

    const enqueueStreamingContent = (text: string) => {
      if (!text) {
        return;
      }
      stopCompletionTimer();
      if (!STREAMING_ENABLED) {
        // 关闭逐字模式：整块入缓冲并立即刷新，保持与 chunk 顺序一致
        streamingState.pendingCompleteContent = '';
        streamingState.buffer.length = 0;
        streamingState.apiCompleted = false;
        applyTextChunk(text);
        return;
      }
      logStreamingDebug('enqueueStreamingContent', { incomingLength: text.length });
      for (const ch of Array.from(text)) {
        streamingState.buffer.push(ch);
      }
      logStreamingDebug('enqueueStreamingContent:buffered', snapshotStreamingState());
      scheduleStreamingFlush();
    };

    const scheduleHistoryReload = (delay = 0, options: { force?: boolean } = {}) => {
      const { force = false } = options;
      if (!ctx || typeof ctx.fetchAndDisplayHistory !== 'function') {
        return;
      }
      setTimeout(() => {
        const targetConversationId = ctx.currentConversationId;
        if (!targetConversationId || targetConversationId.startsWith('temp_')) {
          return;
        }
        try {
          if (!force) {
            const loadingSame =
              !!ctx.historyLoading && ctx.historyLoadingFor === targetConversationId;
            const historyReady =
              ctx.lastHistoryLoadedConversationId === targetConversationId &&
              Array.isArray(ctx.messages) &&
              ctx.messages.length > 0;
            if (loadingSame || historyReady) {
              socketLog('跳过重复历史加载(scheduleHistoryReload)');
              return;
            }
          }
          ctx.fetchAndDisplayHistory({ force });
          if (typeof ctx.fetchConversationTokenStatistics === 'function') {
            ctx.fetchConversationTokenStatistics();
          }
          if (typeof ctx.updateCurrentContextTokens === 'function') {
            ctx.updateCurrentContextTokens();
          }
        } catch (error) {
          console.warn('重新加载对话历史失败:', error);
        }
      }, delay);
    };

    const assignSocketToken = async () => {
      if (typeof window.requestSocketToken !== 'function') {
        console.warn('缺少 requestSocketToken()，无法获取实时连接凭证');
        return false;
      }
      try {
        const freshToken = await window.requestSocketToken();
        ctx.socket.auth = { socket_token: freshToken };
        return true;
      } catch (error) {
        console.error('获取 WebSocket token 失败:', error);
        return false;
      }
    };

    if (ctx.socket.io && typeof ctx.socket.io.on === 'function') {
      ctx.socket.io.on('reconnect_attempt', async () => {
        await assignSocketToken();
      });
    }

    let hasConnectedOnce = false;

    // 连接事件
    ctx.socket.on('connect', () => {
      const isReconnect = hasConnectedOnce;
      const historyLoadingSame =
        !!ctx.historyLoading && ctx.historyLoadingFor === ctx.currentConversationId;
      const historyReady =
        ctx.lastHistoryLoadedConversationId === ctx.currentConversationId &&
        Array.isArray(ctx.messages) &&
        ctx.messages.length > 0;

      ctx.isConnected = true;
      socketLog('WebSocket已连接');

      // 自动订阅终端事件，确保终端面板数据始终就绪（对话级：带当前 conversation_id）
      ctx.socket.emit('terminal_subscribe', { all: true, conversation_id: ctx.currentConversationId || undefined });

      // 首次连接且历史已在加载/已就绪时，避免重复清空与重复动画
      if (!isReconnect && (historyLoadingSame || historyReady)) {
        socketLog('初次连接已存在历史，跳过重复重置与加载');
        hasConnectedOnce = true;
        return;
      }

      hasConnectedOnce = true;
      ctx.resetAllStates(isReconnect ? 'socket:reconnect' : 'socket:connect');
      scheduleHistoryReload(200, { force: isReconnect });
    });

    ctx.socket.on('disconnect', () => {
      ctx.isConnected = false;
      socketLog('WebSocket已断开');
      // 断线时也重置状态，防止状态混乱
      ctx.resetAllStates('socket:disconnect');
    });

    ctx.socket.on('connect_error', (error) => {
      console.error('WebSocket连接错误:', error.message);
    });

    ctx.socket.on('quota_update', (data) => {
      if (data && data.quotas) {
        ctx.resourceSetUsageQuota({ quotas: data.quotas });
      } else {
        ctx.fetchUsageQuota();
      }
    });

    ctx.socket.on('quota_notice', (data) => {
      ctx.showQuotaToast(data || {});
      ctx.fetchUsageQuota();
    });

    ctx.socket.on('quota_exceeded', (data) => {
      ctx.showQuotaToast(data || {});
      ctx.fetchUsageQuota();
    });

    ctx.socket.on('reconnect_attempt', async () => {
      await assignSocketToken();
    });

    const ready = await assignSocketToken();
    if (!ready) {
      console.error('无法获取实时连接凭证，WebSocket 初始化中止。');
      return;
    }
    ctx.socket.connect();

    // ==========================================
    // Token统计WebSocket事件处理（修复版）
    // ==========================================

    ctx.socket.on('token_update', (data) => {
      socketLog('收到token更新事件:', data);

      // 只处理当前对话的token更新
      if (data.conversation_id === ctx.currentConversationId) {
        // 更新累计统计（使用后端提供的准确字段名）
        ctx.currentConversationTokens.cumulative_input_tokens = data.cumulative_input_tokens || 0;
        ctx.currentConversationTokens.cumulative_output_tokens = data.cumulative_output_tokens || 0;
        ctx.currentConversationTokens.cumulative_total_tokens = data.cumulative_total_tokens || 0;

        socketLog(
          `累计Token统计更新: 输入=${data.cumulative_input_tokens}, 输出=${data.cumulative_output_tokens}, 总计=${data.cumulative_total_tokens}`
        );

        const hasContextTokens = typeof data.current_context_tokens === 'number';
        if (hasContextTokens && typeof ctx.resourceSetCurrentContextTokens === 'function') {
          ctx.resourceSetCurrentContextTokens(data.current_context_tokens);
        } else {
          // 同时更新当前上下文Token（关键修复）
          ctx.updateCurrentContextTokens();
        }

        ctx.$forceUpdate();
      }
    });

    // 上下文过长提示
    ctx.socket.on('context_warning', (data) => {
      const title = data?.title || '上下文过长';
      const message = data?.message || '当前对话上下文接近上限，建议使用压缩功能。';
      if (typeof ctx.uiPushToast === 'function') {
        ctx.uiPushToast({
          title,
          message,
          type: data?.type || 'warning',
          duration: data?.duration || 6000
        });
      }
    });

    ctx.socket.on('todo_updated', (data) => {
      socketLog('收到todo更新事件:', data);
      // todo_updated 是全局广播：子智能体/其他对话的待办也会推到这里，
      // 必须只响应当前对话，否则快捷窗口会串出子智能体的待办。
      // 也不能用事件里的 conversation_id 覆盖当前对话 id —— 那会把
      // 子智能体的对话 id 安到当前会话上（对话 id 同步走 conversation_resolved）。
      if (!data || !data.conversation_id) {
        return;
      }
      if (data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      // 实时事件必须带 live=true：socket 先于任务轮询到达，
      // 若静态消费（live=false）会抢先把状态写掉，等轮询的 live
      // 事件再到时已无差异，快捷窗口的进入/打勾动画永远播不出来。
      ctx.fileSetTodoList(data.todo_list || null, true);
    });

    // 快捷窗口：本次对话编辑/创建文件记录更新
    ctx.socket.on('edited_files_updated', (data) => {
      socketLog('收到编辑文件记录更新事件:', data);
      // 全局广播：与 todo_updated 同款过滤——事件必须属于当前对话；
      // /new 等无对话场景直接丢弃（文件记录一定归属某个对话）。
      if (!data || !data.conversation_id) {
        return;
      }
      if (data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      // 同 todo_updated：socket 是实时通道，带 live=true 让新增文件
      // 播进入动画并滚动露出（静态插入在 5 行可视区外用户看不见）。
      useQuickDockStore().setEditedFiles(data.edited_files || [], true);
    });

    // 编辑摘要卡片：write/edit 成功后后端实时广播合并 diff，
    // 写入发起工作的 user 消息 metadata，卡片随工作推进实时更新。
    ctx.socket.on('edit_summary_updated', (data) => {
      if (!data || !data.conversation_id) {
        return;
      }
      if (data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      useChatStore().updateEditSummaryByMessageId(
        typeof data.message_id === 'string' ? data.message_id : '',
        data.edit_summary || null
      );
    });

    // 系统就绪
    ctx.socket.on('system_ready', (data) => {
      ctx.projectPath = data.project_path || '';
      ctx.agentVersion = data.version || ctx.agentVersion;
      ctx.thinkingMode = !!data.thinking_mode;
      if (data.run_mode) {
        ctx.runMode = data.run_mode;
      } else {
        ctx.runMode = ctx.thinkingMode ? 'thinking' : 'fast';
      }
      socketLog('系统就绪:', data);

      // 系统就绪后立即加载对话列表
      ctx.loadConversationsList();
    });

    ctx.socket.on('tool_settings_updated', (data) => {
      socketLog('收到工具设置更新:', data);
      if (data && Array.isArray(data.categories)) {
        ctx.applyToolSettingsSnapshot(data.categories);
      }
    });

    // ==========================================
    // 对话管理相关Socket事件
    // ==========================================

    // 监听对话变更事件
    ctx.socket.on('conversation_changed', (data) => {
      socketLog('对话已切换:', data);

      // 初始化期间不修改 currentConversationId，避免与 bootstrapRoute 冲突
      if (ctx.initialRouteResolved) {
        // 独立全屏路由（工作流编辑器等）不归对话体系：不接管 id、不清消息、不改 URL，
        // 仅同步下方列表数据（标题更新/置顶/列表刷新）。
        const onIndependentRoute =
          typeof ctx.isConversationIndependentRoute === 'function' &&
          ctx.isConversationIndependentRoute();
        // 用户停留在显式新建对话页（/new、/multiagent/new）时，不被其他对话的
        // 广播事件（如运行中任务的标题生成）拽走视图，仅同步列表数据。
        const stayOnNewRoute =
          !data.cleared &&
          !onIndependentRoute &&
          typeof ctx.isExplicitNewConversationRoute === 'function' &&
          ctx.isExplicitNewConversationRoute();
        if (!stayOnNewRoute && !onIndependentRoute) {
          ctx.currentConversationId = data.conversation_id;
          ctx.currentConversationTitle = data.title || '';
        }

        // 更新对话列表中的标题
        if (data.title && Array.isArray(ctx.conversations)) {
          const conv = ctx.conversations.find((c) => c && c.id === data.conversation_id);
          if (conv) {
            conv.title = data.title;
          }
        }

        ctx.promoteConversationToTop(data.conversation_id);

        if (data.cleared && !onIndependentRoute) {
          // 对话被清空（独立全屏路由下不动对话状态与 URL）
          ctx.logMessageState?.('socket:conversation_changed-clearing', { event: data });
          ctx.messages = [];
          ctx.logMessageState?.('socket:conversation_changed-cleared', { event: data });
          ctx.currentConversationId = null;
          ctx.currentConversationTitle = '';
          // 重置Token统计
          ctx.resetTokenStatistics();
          history.replaceState({}, '', '/new');
        }

        // 刷新对话列表
        ctx.loadConversationsList();
        ctx.fetchTodoList();
        ctx.fetchSubAgents();
      }
    });

    ctx.socket.on('conversation_resolved', (data) => {
      if (!data || !data.conversation_id) {
        return;
      }
      const convId = data.conversation_id;

      // 初始化期间不修改 currentConversationId，避免与 bootstrapRoute 冲突；
      // 独立全屏路由（工作流编辑器等）下不恢复对话、不改写 URL
      const onIndependentRoute =
        typeof ctx.isConversationIndependentRoute === 'function' &&
        ctx.isConversationIndependentRoute();
      if (ctx.initialRouteResolved && !onIndependentRoute) {
        ctx.currentConversationId = convId;
        if (data.title) {
          ctx.currentConversationTitle = data.title;
        }
        ctx.promoteConversationToTop(convId);
        const pathFragment = ctx.stripConversationPrefix(convId);
        const currentPath = window.location.pathname.replace(/^\/+/, '');
        // 对话类型不再是路由概念，统一裸路径 /<id>
        const targetPath = `/${pathFragment}`;
        if (data.created) {
          history.pushState({ conversationId: convId }, '', targetPath);
        } else if (currentPath !== pathFragment) {
          history.replaceState({ conversationId: convId }, '', targetPath);
        }
      }
    });

    // 监听对话加载事件
    ctx.socket.on('conversation_loaded', (data) => {
      socketLog('对话已加载:', data);
      if (ctx.skipConversationLoadedEvent) {
        ctx.skipConversationLoadedEvent = false;
        socketLog('跳过重复的 conversation_loaded 处理');
        return;
      }
      const targetConversationId = (data && data.conversation_id) || ctx.currentConversationId;
      const historyLoadingSame =
        !!targetConversationId &&
        !!ctx.historyLoading &&
        ctx.historyLoadingFor === targetConversationId;
      const historyReady =
        !!targetConversationId &&
        ctx.lastHistoryLoadedConversationId === targetConversationId &&
        Array.isArray(ctx.messages) &&
        ctx.messages.length > 0;
      const forceReload = !!(data && data.force_reload);

      if (!forceReload && (historyLoadingSame || historyReady)) {
        socketLog('conversation_loaded: 历史已加载/加载中，跳过重复刷新');
      } else if (data.clear_ui && targetConversationId === ctx.currentConversationId) {
        // in-place 压缩等同一对话内的刷新：不 resetAllStates（避免重置滚动状态
        // 导致锁死），不重新 fetchAndDisplayHistory（避免消息闪烁）。
        socketLog('conversation_loaded: 同对话 clear_ui，跳过重置以保持滚动状态');
      } else {
        if (data.clear_ui) {
          // 清理当前UI状态，准备显示历史内容
          ctx.resetAllStates('socket:conversation_loaded');
        }
        // 延迟获取并显示历史对话内容
        setTimeout(() => {
          ctx.fetchAndDisplayHistory({ force: forceReload });
        }, 300);
      }

      // 延迟获取Token统计（累计+当前上下文）
      setTimeout(() => {
        ctx.fetchConversationTokenStatistics();
        ctx.updateCurrentContextTokens();
        ctx.fetchTodoList();
      }, 500);
    });

    // 监听对话列表更新事件
    ctx.socket.on('conversation_list_update', (data) => {
      socketLog('对话列表已更新:', data);
      // 刷新对话列表
      ctx.conversationsOffset = 0;
      ctx.hasMoreConversations = false;
      ctx.loadingMoreConversations = false;
      ctx.loadConversationsList();
      // 同时刷新分组侧边栏中已加载的工作区
      try {
        const { useConversationStore } = require('../stores/conversation');
        const conversationStore = useConversationStore();
        conversationStore.workspaceGroups.forEach((group: any) => {
          conversationStore.loadWorkspaceConversations(group.workspaceId, { refresh: true });
        });
      } catch (_err) {
        // ignore
      }
    });

    // 监听状态更新事件
    ctx.socket.on('status_update', (status) => {
      ctx.applyStatusSnapshot(status);
      // 显式新建路由（/new、/multiagent/new）：status 快照反映的是后端 terminal
      // 残留的旧对话上下文，既不能把它设成当前对话（防跳回），也不能让它
      // 覆盖刚应用的个性化默认运行模式。
      const onExplicitNewRoute =
        typeof ctx.isExplicitNewConversationRoute === 'function' &&
        ctx.isExplicitNewConversationRoute();
      // 独立全屏路由（工作流编辑器等）同样不归对话体系，快照不得接管当前对话
      const onIndependentRoute =
        typeof ctx.isConversationIndependentRoute === 'function' &&
        ctx.isConversationIndependentRoute();
      // 只有在初始化完成后，才允许 status_update 修改 currentConversationId
      // 避免与 bootstrapRoute 冲突
      if (status.conversation && status.conversation.current_id) {
        if (ctx.initialRouteResolved && !ctx.currentConversationId && !onExplicitNewRoute && !onIndependentRoute) {
          // 初始化完成且当前没有对话ID，才设置
          ctx.currentConversationId = status.conversation.current_id;
        }
      }
      if (!onExplicitNewRoute) {
        if (typeof status.run_mode === 'string') {
          ctx.runMode = status.run_mode;
        } else if (typeof status.thinking_mode !== 'undefined') {
          ctx.runMode = status.thinking_mode ? 'thinking' : 'fast';
        }
      }
    });

    // 用户消息（后台子智能体完成后自动触发）
    ctx.socket.on('user_message', (data) => {
      const isMultiAgentMessage = !!(
        data?.multi_agent_message ||
        data?.metadata?.multi_agent_message ||
        data?.multi_agent_output ||
        data?.metadata?.multi_agent_output
      );
      goalModeDebugLog('legacy_socket_user_message_entry', {
        usePollingMode: ctx.usePollingMode,
        waitingForSubAgent: ctx.waitingForSubAgent,
        isMultiAgentMessage,
        currentConversationId: ctx.currentConversationId,
        dataConversationId: data?.conversation_id,
        message_source: data?.message_source || data?.metadata?.message_source,
        starts_work: data?.starts_work,
        auto_message_type: data?.auto_message_type || data?.metadata?.auto_message_type,
        taskInProgress: ctx.taskInProgress
      });
      // 多智能体消息在轮询模式下也需要处理，否则主智能体空闲时子智能体
      // 推送的消息无法触发前端恢复轮询，必须刷新页面才能看到。
      if (ctx.usePollingMode && !ctx.waitingForSubAgent && !isMultiAgentMessage) {
        goalModeDebugLog('legacy_socket_user_message_early_return', { reason: 'polling_mode_no_waiting' });
        return;
      }
      if (!data) {
        goalModeDebugLog('legacy_socket_user_message_early_return', { reason: 'no_data' });
        return;
      }
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过user_message(对话不匹配)', data.conversation_id);
        return;
      }
      const message = (data.message || data.content || '').trim();
      if (!message) {
        return;
      }
      const incomingImages = Array.isArray(data.images) ? data.images : [];
      const incomingVideos = Array.isArray(data.videos) ? data.videos : [];
      const incomingMediaRefs = Array.isArray(data.media_refs)
        ? data.media_refs
        : Array.isArray(data.mediaRefs)
          ? data.mediaRefs
          : [];
      const source = String(data?.message_source || data?.metadata?.message_source || 'user')
        .trim()
        .toLowerCase();
      const eventMetadata = {
        ...(data?.metadata || {}),
        message_source: source
      };
      if (Object.prototype.hasOwnProperty.call(data || {}, 'visibility')) {
        eventMetadata.visibility = data.visibility;
      }
      if (Object.prototype.hasOwnProperty.call(data || {}, 'starts_work')) {
        eventMetadata.starts_work = data.starts_work;
      }
      eventMetadata.visibility = getMessageVisibility({ role: 'user', content: message, metadata: eventMetadata, ...data });
      eventMetadata.starts_work = messageStartsWork({ role: 'user', content: message, metadata: eventMetadata, ...data });
      const isAutoUserMessage = !!(
        data?.is_auto_generated ||
        data?.metadata?.is_auto_generated ||
        data?.auto_message_type ||
        data?.metadata?.auto_message_type ||
        data?.sub_agent_notice ||
        data?.background_command_notice
      );
      if (
        isAutoUserMessage &&
        eventMetadata.starts_work === true &&
        typeof ctx.markLatestUserWorkCompleted === 'function'
      ) {
        ctx.markLatestUserWorkCompleted();
      }
      const last =
        Array.isArray(ctx.messages) && ctx.messages.length
          ? ctx.messages[ctx.messages.length - 1]
          : null;
      const isLikelyOptimisticEcho = !!(
        last &&
        last.role === 'user' &&
        String(last.content || '').trim() === message &&
        JSON.stringify(last.images || []) === JSON.stringify(incomingImages || []) &&
        JSON.stringify(last.videos || []) === JSON.stringify(incomingVideos || [])
      );
      if (isLikelyOptimisticEcho) {
        last.media_refs = incomingMediaRefs;
        last.metadata = {
          ...(last.metadata || {}),
          ...eventMetadata,
          media_refs: incomingMediaRefs
        };
        const backendTimestamp = data?.timestamp ?? data?.created_at ?? data?.createdAt ?? null;
        if (backendTimestamp) {
          last.created_at = backendTimestamp;
        }
      } else {
        ctx.chatAddUserMessage(message, incomingImages, incomingVideos, incomingMediaRefs, source, eventMetadata);
      }
      ctx.taskInProgress = true;
      ctx.streamingMessage = false;
      ctx.stopRequested = false;
      if (data?.sub_agent_notice && data?.task_id && ctx.usePollingMode) {
        if (typeof data?.has_running_sub_agents === 'boolean') {
          ctx.waitingForSubAgent = data.has_running_sub_agents;
        } else if (typeof data?.remaining_count === 'number') {
          ctx.waitingForSubAgent = data.remaining_count > 0;
        }
        (async () => {
          try {
            const { useTaskStore } = await import('../stores/task');
            const taskStore = useTaskStore();
            taskStore.resumeTask(data.task_id);
          } catch (error) {
            console.warn('恢复任务轮询失败', error);
          }
        })();
      }
      // 多智能体消息没有 task_id，需要主动查找当前对话的运行中任务并恢复轮询。
      if (isMultiAgentMessage && ctx.usePollingMode && !data?.task_id) {
        goalModeDebugLog('legacy_socket_user_message_ma_polling_start', {
          currentConversationId: ctx.currentConversationId
        });
        (async () => {
          try {
            const { useTaskStore } = await import('../stores/task');
            const taskStore = useTaskStore();
            const runningTask = await taskStore.loadRunningTask(ctx.currentConversationId);
            goalModeDebugLog('legacy_socket_user_message_ma_polling_loaded', {
              found: !!runningTask,
              taskId: runningTask?.task_id,
              taskStatus: runningTask?.status
            });
            if (runningTask?.task_id) {
              taskStore.resumeTask(runningTask.task_id);
              goalModeDebugLog('legacy_socket_user_message_ma_polling_resumed', {
                taskId: runningTask.task_id
              });
            }
          } catch (error) {
            console.warn('恢复多智能体任务轮询失败', error);
            goalModeDebugLog('legacy_socket_user_message_ma_polling_error', {
              error: String(error)
            });
          }
        })();
      }
      goalModeDebugLog('legacy_socket_user_message_end', {
        taskInProgress: ctx.taskInProgress,
        streamingMessage: ctx.streamingMessage
      });
      ctx.$forceUpdate();
      ctx.conditionalScrollToBottom();
    });

    // AI消息开始（已废弃，改用 REST API 轮询）
    ctx.socket.on('ai_message_start', (data) => {
      // 用户级广播：只响应当前对话的 AI 消息开始事件。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过ai_message_start(对话不匹配)', data.conversation_id);
        return;
      }
      // 只处理子智能体模式
      if (ctx.waitingForSubAgent) {
        ctx.waitingForSubAgent = false;
        ctx.taskInProgress = false;
      }
    });

    // API 请求已发出、尚未收到首个响应：状态头像显示「等待 API 响应…」
    ctx.socket.on('api_request_start', (data) => {
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      // 轮询模式下该状态由任务事件流维护
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      ctx.apiRequestPending = true;
    });

    // 思考流开始
    ctx.socket.on('thinking_start', (data) => {
      // 对话定向事件：只响应当前对话，避免运行中对话的流式输出渲染到 /new 等空白页。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过thinking_start(对话不匹配)', data.conversation_id);
        return;
      }
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        socketLog('思考开始 (轮询模式，跳过)');
        return;
      }
      socketLog('思考开始');
      // 响应已开始，清除「等待 API 响应」状态（含 ignoreThinking 分支，故放最前）
      ctx.apiRequestPending = false;
      const ignoreThinking = ctx.runMode === 'fast' || ctx.thinkingMode === false;
      streamingState.ignoreThinking = ignoreThinking;
      if (ignoreThinking) {
        ctx.monitorEndModelOutput();
        return;
      }
      ctx.monitorShowThinking();
      const result = ctx.chatStartThinkingAction();
      if (result && result.blockId) {
        const blockId = result.blockId;
        ctx.chatExpandBlock(blockId);
        // 开始思考时尝试滚动到底部，但不改变用户锁定选择
        ctx.scrollToBottom();
        ctx.chatSetThinkingLock(blockId, true);
        ctx.$forceUpdate();
      }
    });

    // 思考内容块
    ctx.socket.on('thinking_chunk', (data) => {
      // 对话定向事件：只响应当前对话。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      if (streamingState.ignoreThinking) {
        return;
      }
      const thinkingAction = ctx.chatAppendThinkingChunk(data.content);
      if (thinkingAction) {
        ctx.$forceUpdate();
        ctx.$nextTick(() => {
          if (thinkingAction && thinkingAction.blockId) {
            ctx.scrollThinkingToBottom(thinkingAction.blockId);
          }
          ctx.conditionalScrollToBottom();
        });
      }
      ctx.monitorShowThinking();
    });

    // 思考结束
    ctx.socket.on('thinking_end', (data) => {
      // 对话定向事件：只响应当前对话。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过thinking_end(对话不匹配)', data.conversation_id);
        return;
      }
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        socketLog('思考结束 (轮询模式，跳过)');
        return;
      }
      socketLog('思考结束');
      if (streamingState.ignoreThinking) {
        streamingState.ignoreThinking = false;
        return;
      }
      const blockId = ctx.chatCompleteThinkingAction(data.full_content);
      if (blockId) {
        setTimeout(() => {
          ctx.chatCollapseBlock(blockId);
          ctx.chatSetThinkingLock(blockId, false);
          ctx.$forceUpdate();
        }, 1000);
        ctx.$nextTick(() => ctx.scrollThinkingToBottom(blockId));
      }
      ctx.$forceUpdate();
      ctx.monitorEndModelOutput();
    });

    // 文本流开始
    ctx.socket.on('text_start', (data) => {
      // 对话定向事件：只响应当前对话。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过text_start(对话不匹配)', data.conversation_id);
        return;
      }
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        socketLog('文本开始 (轮询模式，跳过)');
        return;
      }
      socketLog('文本开始');
      ctx.apiRequestPending = false;
      logStreamingDebug('socket:text_start');
      finalizeStreamingText({ force: true });
      resetStreamingBuffer();
      const action = ctx.chatStartTextAction();
      streamingState.activeMessageIndex =
        typeof ctx.currentMessageIndex === 'number' ? ctx.currentMessageIndex : null;
      streamingState.activeTextAction = action || ensureActiveTextAction();
      ensureActiveMessageBinding();
      ctx.$forceUpdate();
    });

    // 文本内容块
    ctx.socket.on('text_chunk', (data) => {
      // 对话定向事件：只响应当前对话。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      logStreamingDebug('socket:text_chunk', {
        index: data?.index ?? null,
        elapsed: data?.elapsed ?? null,
        chunkLength: (data?.content || '').length,
        snapshot: snapshotStreamingState()
      });
      try {
        ctx.socket.emit('client_chunk_log', {
          conversation_id: ctx.currentConversationId,
          index: data?.index ?? null,
          elapsed: data?.elapsed ?? null,
          length: (data?.content || '').length,
          ts: Date.now()
        });
      } catch (error) {
        console.warn('上报chunk日志失败:', error);
      }
      if (data && typeof data.content === 'string' && data.content.length) {
        if (STREAMING_ENABLED) {
          enqueueStreamingContent(data.content);
        } else {
          // 关闭逐字模式时，直接追加当前chunk
          applyTextChunk(data.content);
        }
        const speech = sanitizeBubbleText(data.content);
        if (speech) {
          ctx.monitorShowSpeech(speech);
        }
      }
    });

    // 文本结束
    ctx.socket.on('text_end', (data) => {
      // 对话定向事件：只响应当前对话。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过text_end(对话不匹配)', data.conversation_id);
        return;
      }
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        socketLog('文本结束 (轮询模式，跳过)');
        return;
      }
      socketLog('文本结束');
      logStreamingDebug('socket:text_end', {
        finalLength: (data?.full_content || '').length,
        snapshot: snapshotStreamingState()
      });
      if (!STREAMING_ENABLED) {
        // 已逐块追加，这里保证动作收尾
        const full = data?.full_content || '';
        if (full) {
          ctx.chatCompleteTextAction(full);
        } else {
          ctx.chatCompleteTextAction('');
        }
        streamingState.buffer.length = 0;
        streamingState.pendingCompleteContent = '';
        streamingState.renderedText = '';
        streamingState.apiCompleted = false;
        ctx.monitorEndModelOutput();
        flushPendingToolEvents();
        return;
      }
      streamingState.apiCompleted = true;
      streamingState.pendingCompleteContent = data?.full_content || '';
      const hidden = typeof document !== 'undefined' && document.hidden === true;
      if (hidden) {
        stopStreamingTimer();
        drainStreamingBufferImmediately();
        finalizeStreamingText({ force: true });
      } else if (!streamingState.buffer.length) {
        scheduleFinalizationAfterDrain();
      } else {
        scheduleStreamingFlush();
      }
      ctx.monitorEndModelOutput();
      flushPendingToolEvents();
    });

    // 工具提示事件（可选）
    ctx.socket.on('tool_hint', (data) => {
      socketLog('工具提示:', data.name);
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过tool_hint(对话不匹配)', data.conversation_id);
        return;
      }
      // 可以在这里添加提示UI
    });

    // 工具准备中事件 - 实时显示
    ctx.socket.on('tool_preparing', (data) => {
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      if (ctx.dropToolEvents) {
        return;
      }
      socketLog('工具准备中:', data.name);
      if (typeof console !== 'undefined' && console.debug) {
      }
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过tool_preparing(对话不匹配)', data.conversation_id);
        return;
      }
      // 工具流式收集即 API 响应已开始，清除「等待 API 响应」状态
      ctx.apiRequestPending = false;
      const msg = ctx.chatEnsureAssistantMessage();
      if (!msg) {
        return;
      }
      if (msg.awaitingFirstContent) {
        msg.awaitingFirstContent = false;
        msg.generatingLabel = '';
      }
      const action = {
        id: data.id,
        type: 'tool',
        tool: {
          id: data.id,
          name: data.name,
          arguments: {},
          argumentSnapshot: null,
          argumentLabel: '',
          status: 'preparing',
          result: null,
          message: data.message || `准备调用 ${data.name}...`,
          intent_full: data.intent || '',
          intent_rendered: data.intent || ''
        },
        timestamp: Date.now()
      };
      msg.actions.push(action);
      ctx.preparingTools.set(data.id, action);
      ctx.toolRegisterAction(action, data.id);
      ctx.toolTrackAction(data.name, action);
      if (data.intent) {
        startIntentTyping(action, data.intent);
      }
      ctx.$forceUpdate();
      ctx.conditionalScrollToBottom();
      if (ctx.monitorPreviewTool) {
        ctx.monitorPreviewTool(data);
      }
    });

    // 工具意图（流式增量）事件
    ctx.socket.on('tool_intent', (data) => {
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      if (ctx.dropToolEvents) {
        return;
      }
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        return;
      }
      if (typeof console !== 'undefined' && console.debug) {
      }
      const target =
        ctx.toolFindAction(data.id, data.preparing_id, data.execution_id) ||
        ctx.toolGetLatestAction(data.name);
      if (target) {
        startIntentTyping(target, data.intent);
      }
      ctx.$forceUpdate();
    });

    // 工具状态更新事件 - 实时显示详细状态
    ctx.socket.on('tool_status', (data) => {
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      if (ctx.dropToolEvents) {
        return;
      }
      socketLog('工具状态:', data);
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过tool_status(对话不匹配)', data.conversation_id);
        return;
      }
      const target = ctx.toolFindAction(data.id, data.preparing_id, data.execution_id);
      if (target) {
        target.tool.statusDetail = data.detail;
        target.tool.statusType = data.status;
        if (data.intent) {
          startIntentTyping(target, data.intent);
        }
        ctx.$forceUpdate();
        return;
      }
      const fallbackAction = ctx.toolGetLatestAction(data.tool);
      if (fallbackAction) {
        fallbackAction.tool.statusDetail = data.detail;
        fallbackAction.tool.statusType = data.status;
        ctx.toolRegisterAction(fallbackAction, data.execution_id || data.id || data.preparing_id);
        if (data.intent) {
          startIntentTyping(fallbackAction, data.intent);
        }
        ctx.$forceUpdate();
      }
    });

    // 工具开始（从准备转为执行）
    ctx.socket.on('tool_start', (data) => {
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      if (ctx.dropToolEvents) {
        return;
      }
      socketLog('工具开始执行:', data.name);
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过tool_start(对话不匹配)', data.conversation_id);
        return;
      }
      const handler = () => {
        let action = null;
        if (data.preparing_id && ctx.preparingTools.has(data.preparing_id)) {
          action = ctx.preparingTools.get(data.preparing_id);
          ctx.preparingTools.delete(data.preparing_id);
        } else {
          action = ctx.toolFindAction(data.id, data.preparing_id, data.execution_id);
        }
        if (!action) {
          const msg = ctx.chatEnsureAssistantMessage();
          if (!msg) {
            return;
          }
          action = {
            id: data.id,
            type: 'tool',
            tool: {
              id: data.id,
              name: data.name,
              arguments: {},
              argumentSnapshot: null,
              argumentLabel: '',
              status: 'running',
              result: null
            },
            timestamp: Date.now()
          };
          msg.actions.push(action);
        }
        action.tool.status = 'running';
        action.tool.arguments = data.arguments;
        action.tool.argumentSnapshot = ctx.cloneToolArguments(data.arguments);
        action.tool.argumentLabel = ctx.buildToolLabel(action.tool.argumentSnapshot);
        action.tool.message = null;
        action.tool.id = data.id;
        action.tool.executionId = data.id;
        if (data.arguments && data.arguments.intent) {
          startIntentTyping(action, data.arguments.intent);
        }
        ctx.toolRegisterAction(action, data.id);
        ctx.toolTrackAction(data.name, action);
        ctx.$forceUpdate();
        ctx.conditionalScrollToBottom();
        ctx.monitorQueueTool(data);

        if (data.name === 'view_video' && typeof ctx.uiPushToast === 'function') {
          ctx.uiPushToast({
            title: '视频读取中',
            message: '读取视频需要较长时间，请耐心等待',
            type: 'info',
            duration: 5000
          });
        }
      };

      handler();
    });

    // 更新action（工具完成）
    ctx.socket.on('update_action', (data) => {
      // 轮询模式下跳过 WebSocket 事件
      if (ctx.usePollingMode && !ctx.waitingForSubAgent) {
        return;
      }
      if (ctx.dropToolEvents) {
        return;
      }
      socketLog('更新action:', data.id, 'status:', data.status);
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过update_action(对话不匹配)', data.conversation_id);
        return;
      }
      const handler = () => {
        let targetAction = ctx.toolFindAction(data.id, data.preparing_id, data.execution_id);
        if (!targetAction && data.preparing_id && ctx.preparingTools.has(data.preparing_id)) {
          targetAction = ctx.preparingTools.get(data.preparing_id);
        }
        if (!targetAction) {
          outer: for (const message of ctx.messages) {
            if (!message.actions) continue;
            for (const action of message.actions) {
              if (action.type !== 'tool') continue;
              const matchByExecution =
                action.tool.executionId && action.tool.executionId === data.id;
              const matchByToolId = action.tool.id === data.id;
              const matchByPreparingId = action.id === data.preparing_id;
              if (matchByExecution || matchByToolId || matchByPreparingId) {
                targetAction = action;
                break outer;
              }
            }
          }
        }
        if (targetAction) {
          if (data.status) {
            targetAction.tool.status = data.status;
          }
          if (data.result !== undefined) {
            targetAction.tool.result = data.result;
          }
          if (
            targetAction.tool &&
            targetAction.tool.name === 'trigger_easter_egg' &&
            data.result !== undefined
          ) {
            const eggPromise = ctx.handleEasterEggPayload(data.result);
            if (eggPromise && typeof eggPromise.catch === 'function') {
              eggPromise.catch((error) => {
                console.warn('彩蛋处理异常:', error);
              });
            }
          }
          // 个性化设置工具主题切换处理在 taskPolling.ts 中（当前使用轮询模式）
          if (data.message !== undefined) {
            targetAction.tool.message = data.message;
          }
          if (data.arguments && data.arguments.intent) {
            startIntentTyping(targetAction, data.arguments.intent);
          }
          if (data.awaiting_content) {
            targetAction.tool.awaiting_content = true;
          } else if (data.status === 'completed') {
            targetAction.tool.awaiting_content = false;
          }
          if (!targetAction.tool.executionId && (data.execution_id || data.id)) {
            targetAction.tool.executionId = data.execution_id || data.id;
          }
          if (data.arguments) {
            targetAction.tool.arguments = data.arguments;
            targetAction.tool.argumentSnapshot = ctx.cloneToolArguments(data.arguments);
            targetAction.tool.argumentLabel = ctx.buildToolLabel(
              targetAction.tool.argumentSnapshot
            );
            if (data.arguments.intent) {
              startIntentTyping(targetAction, data.arguments.intent);
            }
          }
          ctx.toolRegisterAction(targetAction, data.execution_id || data.id);
          if (
            data.status &&
            ['completed', 'failed', 'error'].includes(data.status) &&
            !data.awaiting_content
          ) {
            ctx.toolUnregisterAction(targetAction);
            if (data.id) {
              ctx.preparingTools.delete(data.id);
            }
            if (data.preparing_id) {
              ctx.preparingTools.delete(data.preparing_id);
            }
          }
          ctx.$forceUpdate();
          ctx.conditionalScrollToBottom();
        }

        if (data.status === 'completed') {
          setTimeout(() => {
            ctx.updateCurrentContextTokens();
          }, 500);
          try {
            ctx.fileFetchTree();
          } catch (error) {
            console.warn('刷新文件树失败', error);
          }
        }
        ctx.monitorResolveTool(data);
      };

      handler();
    });

    ctx.socket.on('append_payload', (data) => {
      if (ctx.dropToolEvents) {
        return;
      }
      socketLog('收到append_payload事件:', data);
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过append_payload(对话不匹配)', data.conversation_id);
        return;
      }
      ctx.chatAddAppendPayloadAction({
        path: data.path || '未知文件',
        forced: !!data.forced,
        success: data.success === undefined ? true : !!data.success,
        lines: data.lines ?? null,
        bytes: data.bytes ?? null
      });
      ctx.$forceUpdate();
      ctx.conditionalScrollToBottom();
    });

    ctx.socket.on('modify_payload', (data) => {
      if (ctx.dropToolEvents) {
        return;
      }
      socketLog('收到modify_payload事件:', data);
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过modify_payload(对话不匹配)', data.conversation_id);
        return;
      }
      ctx.chatAddModifyPayloadAction({
        path: data.path || '未知文件',
        total: data.total ?? null,
        completed: data.completed || [],
        failed: data.failed || [],
        forced: !!data.forced
      });
      ctx.$forceUpdate();
      ctx.conditionalScrollToBottom();
    });

    // 停止请求确认
    ctx.socket.on('stop_requested', (data) => {
      // 用户级广播：只清理当前对话的 pending 工具状态。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过stop_requested(对话不匹配)', data.conversation_id);
        return;
      }
      socketLog('停止请求已接收:', data.message);
      // 可以显示提示信息
      try {
        if (typeof ctx.clearPendingTools === 'function') {
          ctx.clearPendingTools('socket:stop_requested');
        }
      } catch (error) {
        console.warn('清理未完成工具失败', error);
      }
    });

    // 任务停止
    ctx.socket.on('task_stopped', (data) => {
      // 多对话并行运行：任务生命周期事件是用户级广播，只响应当前对话的，
      // 否则其他对话的停止会误清本对话的运行态（侧边栏标识由
      // refreshRunningWorkspaceTasks 独立轮询维护，不受影响）。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过task_stopped(对话不匹配)', data.conversation_id);
        return;
      }
      socketLog('任务已停止:', data.message);
      const hasRunningSubAgents = !!data?.has_running_sub_agents;
      const hasRunningBackgroundCommands = !!data?.has_running_background_commands;
      const hasRunningBackground = hasRunningSubAgents || hasRunningBackgroundCommands;
      const hasRunningMultiAgent = !!data?.has_running_multi_agent;
      // 主对话已被停止。streaming、stopRequested 都重置。
      // 如果仍有后台任务在跑，保留 taskInProgress=true 让对话在列表里仍显示“运行中”
      // （仅影响 UI 标识，不影响输入栏可用性——输入栏只看 streamingMessage）。
      const stillHasBackground = hasRunningBackground || hasRunningMultiAgent;
      ctx.streamingMessage = false;
      ctx.stopRequested = false;
      ctx.waitingForSubAgent = hasRunningSubAgents;
      ctx.waitingForBackgroundCommand = hasRunningBackgroundCommands;
      ctx.taskInProgress = stillHasBackground;
      if (typeof ctx.scheduleResetAfterTask === 'function') {
        ctx.scheduleResetAfterTask('socket:task_stopped', { preserveMonitorWindows: true });
      }
    });

    // 任务完成（重点：更新Token统计）
    ctx.socket.on('task_complete', (data) => {
      // 同 task_stopped：用户级广播，只响应当前对话的任务完成事件。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过task_complete(对话不匹配)', data.conversation_id);
        return;
      }
      socketLog('任务完成', data);

      const hasRunningMultiAgent = !!data?.has_running_multi_agent;
      // 多智能体模式下，若仍有运行中的多智能体实例，保持对话运行态，继续接收后续消息；
      // 但此时主智能体已空闲，不应阻塞输入区（waitingForSubAgent=false）。
      if (hasRunningMultiAgent) {
      } else if (ctx.currentConversationType === 'multi_agent' || !data.has_running_sub_agents) {
        if (ctx.waitingForSubAgent) {
          ctx.waitingForSubAgent = false;
        }
        ctx.taskInProgress = false;
        ctx.scheduleResetAfterTask('socket:task_complete', { preserveMonitorWindows: true });
      } else {
      }

      resetPendingToolEvents();

      // 任务完成后立即更新Token统计（关键修复）
      if (ctx.currentConversationId) {
        ctx.updateCurrentContextTokens();
        ctx.fetchConversationTokenStatistics();
      }
    });

    // 子智能体等待状态
    ctx.socket.on('sub_agent_waiting', (data) => {
      // 用户级广播：只响应当前对话的等待事件，避免把其他对话的
      // waitingForSubAgent 标志安到本对话上（会阻塞 resetAllStates）。
      if (data?.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过sub_agent_waiting(对话不匹配)', data.conversation_id);
        return;
      }
      socketLog('等待子智能体完成:', data);

      // 多智能体对话下，子智能体 idle/running 是常态，不阻塞主对话输入区
      if (ctx.currentConversationType !== 'multi_agent') {
        // 设置标志：有子智能体在运行，阻止状态重置
        ctx.waitingForSubAgent = true;

        // 保持任务进行中状态
        ctx.taskInProgress = true;
        ctx.streamingMessage = false;
        ctx.stopRequested = false;
      }

      // 显示等待提示
      if (typeof ctx.appendSystemAction === 'function') {
        const taskList = data.tasks
          .map((t: any) => `子智能体${t.agent_id} (${t.summary || '无描述'})`)
          .join('、');
        ctx.appendSystemAction(`⏳ 等待 ${data.count} 个后台子智能体完成：${taskList}`);
      }

      ctx.$forceUpdate();
    });

    // 聚焦文件更新
    ctx.socket.on('focused_files_update', (data) => {
      ctx.focusSetFiles(data || {});
      // 聚焦文件变化时更新当前上下文Token（关键修复）
      if (ctx.currentConversationId) {
        setTimeout(() => {
          ctx.updateCurrentContextTokens();
        }, 500);
      }
    });

    // 文件树更新
    ctx.socket.on('file_tree_update', (data) => {
      ctx.fileSetTreeFromResponse(data);
      // 文件树变化时也可能影响上下文
      if (ctx.currentConversationId) {
        setTimeout(() => {
          ctx.updateCurrentContextTokens();
        }, 500);
      }
    });

    // 系统消息
    ctx.socket.on('system_message', (data) => {
      if (!data || !data.content) {
        return;
      }
      // 用户级广播：其他对话的系统提示不应串进当前对话视图。
      if (data.conversation_id && data.conversation_id !== ctx.currentConversationId) {
        socketLog('跳过system_message(对话不匹配)', data.conversation_id);
        return;
      }
      ctx.appendSystemAction(data.content);
    });

    // 错误处理
    ctx.socket.on('error', (data) => {
      const msg = data?.message || '发生未知错误';
      const code = data?.status_code;
      const errType = data?.error_type;
      const errCode = data?.error_code;
      const dumpPath = data?.request_dump;
      const baseUrl = data?.base_url;
      const modelId = data?.model_id;
      const conversationId = data?.conversation_id;
      const taskId = data?.task_id;
      const shouldRetry = Boolean(data?.retry);
      const retryIn = Number(data?.retry_in) || 5;
      const retryAttempt = Number(data?.attempt) || 1;
      const retryMax = Number(data?.max_attempts) || retryAttempt;
      const detailParts = [
        dumpPath ? `请求记录: ${dumpPath}` : '',
        baseUrl ? `接口: ${baseUrl}` : '',
        modelId ? `模型: ${modelId}` : '',
        conversationId ? `对话ID: ${conversationId}` : '',
        taskId ? `任务ID: ${taskId}` : ''
      ].filter(Boolean);
      const detailText = detailParts.length ? `\n${detailParts.join('\n')}` : '';
      if (typeof ctx.uiPushToast === 'function') {
        ctx.uiPushToast({
          title: code ? `API错误 ${code}` : 'API错误',
          message: `${errType ? `${errType}${errCode ? `(${errCode})` : ''}: ${msg}` : msg}${detailText}`,
          type: 'error',
          duration: 6000
        });
        if (shouldRetry) {
          ctx.uiPushToast({
            title: '即将重试',
            message: `将在 ${retryIn} 秒后重试（第 ${retryAttempt}/${retryMax} 次）\n错误：${msg}`,
            type: 'info',
            duration: Math.max(retryIn, 1) * 1000
          });
        }
      }
      if (shouldRetry) {
        // 错误后保持停止按钮态，用户可手动停止或等待自动重试；
        // 重试间隔不算「等待 API 响应」，重试发起时后端会重新发 api_request_start
        ctx.apiRequestPending = false;
        ctx.stopRequested = false;
        ctx.taskInProgress = true;
        ctx.streamingMessage = true;
        return;
      }

      if (typeof ctx.appendSystemAction === 'function') {
        ctx.appendSystemAction(
          `${code ? `[API ${code}] ` : '[API] '}${errType ? `${errType}${errCode ? `(${errCode})` : ''}: ` : ''}${msg}${detailText}`
        );
      }

      // 最后一次报错：恢复输入状态并清理提示动画/流式残留
      // （统一清理 currentStreamingType/activeThinkingId 等字段，
      // 否则 avatarStatus 的 isThinking 永久为真，状态头像卡在「思考中」）
      if (typeof ctx.chatClearStreamingResidualState === 'function') {
        ctx.chatClearStreamingResidualState();
      }
      ctx.apiRequestPending = false;
      if (typeof ctx.chatClearThinkingLocks === 'function') {
        ctx.chatClearThinkingLocks();
      }
      ctx.streamingMessage = false;
      ctx.stopRequested = false;
      ctx.taskInProgress = false;
    });

    // 命令结果
    ctx.socket.on('command_result', (data) => {
      if (data.command === 'clear' && data.success) {
        ctx.logMessageState?.('socket:command_result-clear', { data });
        ctx.messages = [];
        ctx.logMessageState?.('socket:command_result-cleared', { data });
        ctx.currentMessageIndex = -1;
        ctx.chatClearExpandedBlocks();
        // 清除对话时重置Token统计
        ctx.resetTokenStatistics();
      } else if (data.command === 'status' && data.success) {
        ctx.addSystemMessage(`系统状态:\n${JSON.stringify(data.data, null, 2)}`);
      } else if (!data.success) {
        ctx.addSystemMessage(`命令失败: ${data.message}`);
      }
    });

    // === 实时终端事件 ===
    ctx.socket.on('terminal_subscribed', (data) => {
      if (data?.terminals) {
        const sessions: Record<string, { working_dir?: string; shell?: string }> = {};
        for (const t of data.terminals) {
          sessions[t.name] = {
            working_dir: t.working_dir,
            shell: 'bash'
          };
        }
        ctx.setTerminalSessions(sessions);
        if (data.terminals.length > 0 && !ctx.terminalActiveSession) {
          ctx.setTerminalActiveSession(data.terminals[0].name);
        }
      }
    });

    ctx.socket.on('terminal_started', (data) => {
      if (data?.session) {
        const sessions = { ...ctx.terminalSessions };
        sessions[data.session] = {
          working_dir: data.working_dir,
          shell: 'bash'
        };
        ctx.setTerminalSessions(sessions);
        if (!ctx.terminalActiveSession) {
          ctx.setTerminalActiveSession(data.session);
        }
      }
    });

    ctx.socket.on('terminal_list_update', (data) => {
      if (data?.terminals) {
        const sessions: Record<string, { working_dir?: string; shell?: string }> = {};
        for (const t of data.terminals) {
          sessions[t.name] = {
            working_dir: t.working_dir,
            shell: 'bash'
          };
        }
        ctx.setTerminalSessions(sessions);
        if (!ctx.terminalActiveSession && data.terminals.length > 0) {
          ctx.setTerminalActiveSession(data.terminals[0].name);
        }
      }
    });

    ctx.socket.on('terminal_closed', (data) => {
      if (data?.session) {
        const sessions = { ...ctx.terminalSessions };
        delete sessions[data.session];
        ctx.setTerminalSessions(sessions);
        if (ctx.terminalActiveSession === data.session) {
          const names = Object.keys(sessions);
          ctx.setTerminalActiveSession(names.length > 0 ? names[0] : '');
        }
      }
    });

    ctx.socket.on('terminal_reset', (data) => {
      if (data?.session) {
        const logs = { ...ctx.terminalSessionLogs };
        logs[data.session] = '';
        ctx.terminalSessionLogs = logs;
      }
    });

    ctx.socket.on('terminal_output_history', (data) => {
      if (data?.session && data?.output) {
        const logs = { ...ctx.terminalSessionLogs };
        logs[data.session] = (logs[data.session] || '') + data.output;
        ctx.terminalSessionLogs = logs;
      }
    });

    ctx.socket.on('terminal_history', (data) => {
      if (data?.session && data?.output) {
        const logs = { ...ctx.terminalSessionLogs };
        // 历史数据前置（完整替换），因为这是完整的历史记录
        logs[data.session] = data.output;
        ctx.terminalSessionLogs = logs;
        // 标记已水合
        const hydrated = { ...ctx.terminalHydrated };
        hydrated[data.session] = true;
        ctx.terminalHydrated = hydrated;
      }
    });

    ctx.socket.on('terminal_output', (data) => {
      if (data?.session && data?.data) {
        const logs = { ...ctx.terminalSessionLogs };
        logs[data.session] = (logs[data.session] || '') + data.data;
        if (logs[data.session] && logs[data.session].length > 400000) {
          logs[data.session] = logs[data.session].slice(-400000);
        }
        ctx.terminalSessionLogs = logs;
      }
    });

    ctx.socket.on('terminal_input', (data) => {
      if (data?.session && data?.data) {
        const formatted = `\x1b[1;32m➤ ${data.data}\x1b[0m`;
        const logs = { ...ctx.terminalSessionLogs };
        logs[data.session] = (logs[data.session] || '') + formatted;
        if (logs[data.session] && logs[data.session].length > 400000) {
          logs[data.session] = logs[data.session].slice(-400000);
        }
        ctx.terminalSessionLogs = logs;
      }
    });
  } catch (error) {
    console.error('Socket初始化失败:', error);
  }
}

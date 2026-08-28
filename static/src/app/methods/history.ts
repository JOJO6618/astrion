// @ts-nocheck
import { debugLog } from './common';
import { t } from '@/locales';
import { useQuickDockStore } from '../../stores/quickDock';
const jsonDebug = (...args: any[]) => {
};
const RESTORE_DEBUG_PREFIX = '[RESTORE_DEBUG]';
const RESTORE_DEBUG_EVENTS = new Set([
  'history:fetch:start',
  'history:fetch:response',
  'history:render:done'
]);

function isRestoreDebugEnabled() {
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__RESTORE_DEBUG__;
    if (explicit === true || explicit === '1') return true;
    if (explicit === false || explicit === '0') return false;
    const localFlag = window.localStorage?.getItem('restoreDebug');
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

function restoreDebugLog(event: string, payload: Record<string, any> = {}) {
  if (!isRestoreDebugEnabled()) return;
  if (!RESTORE_DEBUG_EVENTS.has(event)) return;
  console.log(RESTORE_DEBUG_PREFIX, event, payload);
}

// 后端子智能体完成消息格式匹配（须与后端 modules/i18n.py 的 zh/en 两种产出一致；\u 转义仅为通过 i18n 审计）
const SUB_AGENT_DONE_PREFIX_RE = /^(?:✅\s*)?(?:\u5b50\u667a\u80fd\u4f53|Sub-agent)\s*#?\s*(\d+)\s*(?:\u4efb\u52a1\u6458\u8981|task summary)[:：]/;
const BG_RUN_COMMAND_DONE_PREFIX_RE = /^\[(?:\u540e\u53f0\s*run_command\s*\u5b8c\u6210|Background\s*run_command\s*finished)\]/;

function parseSubAgentDoneLabel(rawContent: any): string | null {
  const content = (rawContent || '').toString().trim();
  if (!content) return null;
  const match = content.match(SUB_AGENT_DONE_PREFIX_RE);
  // 完成判定双语：zh /已完成/，en /Completed./（后端 modules/i18n.py sub_agent.summary_completed）
  if (!match || !/(?:\u5df2\u5b8c\u6210|Completed\.)/.test(content)) return null;
  return t('appUi.subAgentTaskDone', { agentId: match[1] });
}

function parseBackgroundRunCommandDoneLabel(rawContent: any): string | null {
  const content = (rawContent || '').toString().trim();
  if (!content) return null;
  if (!BG_RUN_COMMAND_DONE_PREFIX_RE.test(content)) return null;
  return t('appUi.backgroundRunCommandDone');
}

function parseSystemNoticeLabel(rawContent: any): string | null {
  const content = (rawContent || '').toString().trim();
  if (!content) return null;
  return parseSubAgentDoneLabel(content) || parseBackgroundRunCommandDoneLabel(content);
}

function isSystemAutoUserMessageMeta(meta: any): boolean {
  if (!meta || typeof meta !== 'object') {
    return false;
  }
  return !!(
    meta.is_auto_generated ||
    meta.auto_message_type ||
    meta.sub_agent_notice ||
    meta.background_command_notice
  );
}

export const historyMethods = {
  // ==========================================
  // 关键功能：获取并显示历史对话内容
  // ==========================================
  async fetchAndDisplayHistory(options = {}) {
    const { force = false } = options as { force?: boolean };
    const targetConversationId = this.currentConversationId;
    if (!targetConversationId || targetConversationId.startsWith('temp_')) {
      debugLog('没有当前对话ID，跳过历史加载');
      this.refreshBlankHeroState();
      return;
    }

    // 若同一对话正在加载，直接复用；若是切换对话则允许并发但后来的请求会赢
    if (this.historyLoading && this.historyLoadingFor === targetConversationId) {
      debugLog('同一对话历史正在加载，跳过重复请求');
      return;
    }

    // 已经有完整历史且非强制刷新时，避免重复加载导致动画播放两次
    const alreadyHydrated =
      !force &&
      this.lastHistoryLoadedConversationId === targetConversationId &&
      Array.isArray(this.messages) &&
      this.messages.length > 0;
    if (alreadyHydrated) {
      debugLog('历史已加载，跳过重复请求');
      this.logMessageState('fetchAndDisplayHistory:skip-duplicate', {
        conversationId: targetConversationId
      });
      return;
    }

    const loadSeq = ++this.historyLoadSeq;
    restoreDebugLog('history:fetch:start', {
      loadSeq,
      targetConversationId,
      currentConversationId: this.currentConversationId,
      force
    });
    jsonDebug('history.fetch:start', {
      loadSeq,
      force,
      targetConversationId,
      currentConversationId: this.currentConversationId,
      currentMessagesCount: Array.isArray(this.messages) ? this.messages.length : -1
    });
    this.historyLoading = true;
    this.historyLoadingFor = targetConversationId;
    try {
      debugLog('开始获取历史对话内容...');
      this.logMessageState('fetchAndDisplayHistory:start', {
        conversationId: this.currentConversationId
      });

      try {
        // 使用专门的API获取对话消息历史
        const messagesResponse = await fetch(`/api/conversations/${targetConversationId}/messages`);

        if (!messagesResponse.ok) {
          console.warn('无法获取消息历史，尝试备用方法');
          // 备用方案：通过状态API获取
          const statusResponse = await fetch('/api/status');
          const status = await statusResponse.json();
          debugLog('系统状态:', status);
          this.applyStatusSnapshot(status);

          // 如果状态中有对话历史字段
          if (status.conversation_history && Array.isArray(status.conversation_history)) {
            this.renderHistoryMessages(status.conversation_history);
            return;
          }

          debugLog('备用方案也无法获取历史消息');
          return;
        }

        // 如果在等待期间用户已切换到其他对话，则丢弃结果
        if (
          loadSeq !== this.historyLoadSeq ||
          this.currentConversationId !== targetConversationId
        ) {
          debugLog('检测到对话已切换，丢弃过期的历史加载结果');
          return;
        }

        const messagesData = await messagesResponse.json();
        const rawMessages = Array.isArray(messagesData?.data?.messages)
          ? messagesData.data.messages
          : [];
        // 快捷窗口：回填本次对话编辑/创建文件记录（后端已过滤不存在文件）
        useQuickDockStore().setEditedFiles(
          Array.isArray(messagesData?.data?.edited_files) ? messagesData.data.edited_files : []
        );
        const lastAssistantRaw = [...rawMessages]
          .reverse()
          .find((m: any) => m?.role === 'assistant');
        restoreDebugLog('history:fetch:response', {
          loadSeq,
          success: !!messagesData?.success,
          rawCount: rawMessages.length,
          responseConversationId: messagesData?.data?.conversation_id,
          lastAssistantContentLength: (lastAssistantRaw?.content || '').length,
          lastAssistantReasoningLength: (lastAssistantRaw?.reasoning_content || '').length,
          lastAssistantHasShowHtml: /<show_html/i.test(lastAssistantRaw?.content || '')
        });
        jsonDebug('history.fetch:response', {
          loadSeq,
          success: !!messagesData?.success,
          responseConversationId: messagesData?.data?.conversation_id,
          messagesCount: Array.isArray(messagesData?.data?.messages)
            ? messagesData.data.messages.length
            : -1
        });
        debugLog('获取到消息数据:', messagesData);

        if (messagesData.success && messagesData.data && messagesData.data.messages) {
          const messages = messagesData.data.messages;
          debugLog(`发现 ${messages.length} 条历史消息`);

          if (messages.length > 0) {
            // 清空当前显示的消息
            this.logMessageState('fetchAndDisplayHistory:before-clear-existing');
            this.messages = [];
            this.logMessageState('fetchAndDisplayHistory:after-clear-existing');

            // 渲染历史消息 - 这是关键功能
            this.renderHistoryMessages(messages);
            jsonDebug('history.fetch:rendered', {
              loadSeq,
              renderedMessagesCount: Array.isArray(this.messages) ? this.messages.length : -1,
              lastRole: this.messages?.[this.messages.length - 1]?.role || null
            });

            // 在 historyLoading 隐藏期内等待 Markdown/表格/卡片等动态高度稳定，再滚到底部。
            // 注意：不要在显示后用多个 timeout 追底，否则会和滚动锚点/异步内容高度变化互相抢导致上下抖动。
            if (typeof this.settleHistoryRenderAndScroll === 'function') {
              await this.settleHistoryRenderAndScroll();
            } else {
              await this.$nextTick();
              this.scrollHistoryToBottomInstant();
            }

            debugLog('历史对话内容显示完成');
          } else {
            debugLog('对话存在但没有历史消息');
            this.logMessageState('fetchAndDisplayHistory:no-history-clear');
            this.messages = [];
            this.logMessageState('fetchAndDisplayHistory:no-history-cleared');
          }
        } else {
          debugLog('消息数据格式不正确:', messagesData);
          this.logMessageState('fetchAndDisplayHistory:invalid-data-clear');
          this.messages = [];
          this.logMessageState('fetchAndDisplayHistory:invalid-data-cleared');
        }
      } catch (error) {
        console.error('获取历史对话失败:', error);
        debugLog('尝试不显示错误弹窗，仅在控制台记录');
        // 不显示alert，避免打断用户体验
        this.logMessageState('fetchAndDisplayHistory:error-clear', {
          error: error?.message || String(error)
        });
        this.messages = [];
        this.logMessageState('fetchAndDisplayHistory:error-cleared');
      }
    } finally {
      // 仅在本次加载仍是最新请求时清除 loading 状态
      if (loadSeq === this.historyLoadSeq) {
        this.historyLoading = false;
        this.historyLoadingFor = null;
      }
      this.refreshBlankHeroState();
    }
  },

  // ==========================================
  // 关键功能：渲染历史消息
  // ==========================================
  renderHistoryMessages(historyMessages) {
    debugLog('开始渲染历史消息...', historyMessages);
    debugLog('历史消息数量:', historyMessages.length);
    this.logMessageState('renderHistoryMessages:start', { historyCount: historyMessages.length });

    if (!Array.isArray(historyMessages)) {
      console.error('历史消息不是数组格式');
      return;
    }

    let currentAssistantMessage = null;
    let historyHasImages = false;
    let historyHasVideos = false;
    let toolArgsParseFailCount = 0;
    let toolResultParseFailCount = 0;

    historyMessages.forEach((message, index) => {
      debugLog(`处理消息 ${index + 1}/${historyMessages.length}:`, message.role, message);
      const meta = message.metadata || {};
      if (message.role === 'user' && (meta.system_injected_image || meta.system_injected_video)) {
        debugLog('跳过系统代发的图片/视频消息（仅用于模型查看，不在前端展示）');
        return;
      }

      if (message.role === 'user') {
        // 用户消息 - 先结束之前的assistant消息
        if (currentAssistantMessage && currentAssistantMessage.actions.length > 0) {
          this.messages.push(currentAssistantMessage);
          currentAssistantMessage = null;
        }
        const images = message.images || (message.metadata && message.metadata.images) || [];
        const videos = message.videos || (message.metadata && message.metadata.videos) || [];
        const mediaRefs =
          message.media_refs || (message.metadata && message.metadata.media_refs) || [];
        if (Array.isArray(images) && images.length) {
          historyHasImages = true;
        }
        if (Array.isArray(videos) && videos.length) {
          historyHasVideos = true;
        }
        const rawCreatedAt = message.created_at ?? message.createdAt ?? message.timestamp ?? null;
        const normalizedCreatedAt =
          typeof rawCreatedAt === 'number' && rawCreatedAt > 0
            ? new Date(rawCreatedAt * 1000).toISOString()
            : rawCreatedAt || null;
        this.messages.push({
          role: 'user',
          content: message.content || '',
          images,
          videos,
          media_refs: Array.isArray(mediaRefs) ? mediaRefs : [],
          metadata: message.metadata || {},
          created_at: normalizedCreatedAt
        });
        debugLog('添加用户消息:', message.content?.substring(0, 50) + '...');
      } else if (message.role === 'assistant') {
        // AI消息 - 如果没有当前assistant消息，创建一个
        if (!currentAssistantMessage) {
          currentAssistantMessage = {
            role: 'assistant',
            actions: [],
            streamingThinking: '',
            streamingText: '',
            currentStreamingType: null,
            activeThinkingId: null,
            awaitingFirstContent: false, // 历史消息不应该显示等待动画
            generatingLabel: ''
          };
        }

        const content = message.content || '';
        const reasoningText = (message.reasoning_content || '').trim();

        if (reasoningText) {
          const blockId = `history-thinking-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          currentAssistantMessage.actions.push({
            id: `history-think-${Date.now()}-${Math.random()}`,
            type: 'thinking',
            content: reasoningText,
            streaming: false,
            collapsed: true,
            timestamp: Date.now(),
            blockId
          });
          debugLog('添加思考内容:', reasoningText.substring(0, 50) + '...');
        }

        // 处理普通文本内容（移除思考标签后的内容）
        const textContent = content.trim();
        if (textContent) {
          currentAssistantMessage.actions.push({
            id: `history-text-${Date.now()}-${Math.random()}`,
            type: 'text',
            content: textContent,
            streaming: false,
            timestamp: Date.now()
          });
          debugLog('添加文本内容:', textContent.substring(0, 50) + '...');
        }

        // 处理工具调用
        if (message.tool_calls && Array.isArray(message.tool_calls)) {
          message.tool_calls.forEach((toolCall, tcIndex) => {
            let arguments_obj = {};
            try {
              arguments_obj =
                typeof toolCall.function.arguments === 'string'
                  ? JSON.parse(toolCall.function.arguments || '{}')
                  : toolCall.function.arguments || {};
            } catch (e) {
              console.warn('解析工具参数失败:', e);
              arguments_obj = {};
              toolArgsParseFailCount += 1;
              jsonDebug('history.render:tool-args-parse-fail', {
                toolName: toolCall?.function?.name,
                toolCallId: toolCall?.id,
                error: e?.message || String(e)
              });
            }

            const action = {
              id: `history-tool-${toolCall.id || Date.now()}-${tcIndex}`,
              type: 'tool',
              tool: {
                id: toolCall.id,
                name: toolCall.function.name,
                arguments: arguments_obj,
                argumentSnapshot: this.cloneToolArguments(arguments_obj),
                argumentLabel: this.buildToolLabel(arguments_obj),
                intent_full: arguments_obj.intent || '',
                intent_rendered: arguments_obj.intent || '',
                status: 'preparing',
                result: null
              },
              timestamp: Date.now()
            };
            // 如果是历史加载的动作且状态仍为进行中，标记为 stale，避免刷新后按钮卡死
            if (['preparing', 'running', 'awaiting_content'].includes(action.tool.status)) {
              action.tool.status = 'stale';
              action.tool.awaiting_content = false;
              action.streaming = false;
            }
            currentAssistantMessage.actions.push(action);
            debugLog('添加工具调用:', toolCall.function.name);
          });
        }
      } else if (message.role === 'tool') {
        // 工具结果 - 更新当前assistant消息中对应的工具
        if (currentAssistantMessage) {
          // 查找对应的工具action - 使用更灵活的匹配
          let toolAction = null;

          // 优先按tool_call_id匹配
          if (message.tool_call_id) {
            toolAction = currentAssistantMessage.actions.find(
              (action) => action.type === 'tool' && action.tool.id === message.tool_call_id
            );
          }

          // 如果找不到，按name匹配最后一个同名工具
          if (!toolAction && message.name) {
            const sameNameTools = currentAssistantMessage.actions.filter(
              (action) => action.type === 'tool' && action.tool.name === message.name
            );
            toolAction = sameNameTools[sameNameTools.length - 1]; // 取最后一个
          }

          if (toolAction) {
            // 解析工具结果（优先使用JSON，其次使用元数据的 tool_payload，以保证搜索结果在刷新后仍可展示）
            let result;
            try {
              result = JSON.parse(message.content);
            } catch (e) {
              toolResultParseFailCount += 1;
              jsonDebug('history.render:tool-result-parse-fail', {
                toolName: message?.name,
                toolCallId: message?.tool_call_id,
                error: e?.message || String(e)
              });
              if (message.metadata && message.metadata.tool_payload) {
                result = message.metadata.tool_payload;
              } else {
                result = {
                  output: message.content,
                  success: true
                };
              }
            }

            toolAction.tool.status = 'completed';
            toolAction.tool.result = result;
            if (result && typeof result === 'object') {
              if (result.error) {
                toolAction.tool.message = result.error;
              } else if (result.message && !toolAction.tool.message) {
                toolAction.tool.message = result.message;
              }
            }
            debugLog(`更新工具结果: ${message.name} -> ${message.content?.substring(0, 50)}...`);
          } else {
            console.warn('找不到对应的工具调用:', message.name, message.tool_call_id);
          }
        }
      } else {
        // 其他类型消息（如system）- 先结束当前assistant消息
        if (currentAssistantMessage && currentAssistantMessage.actions.length > 0) {
          this.messages.push(currentAssistantMessage);
          currentAssistantMessage = null;
        }
        if (message.role === 'system') {
          const rawContent = message.content || '';
          const label = parseSystemNoticeLabel(rawContent);
          if (label) {
            // 历史中的 system 通知转换为 assistant system action，避免被 role=system 过滤掉
            this.messages.push({
              role: 'assistant',
              actions: [
                {
                  id: `history-system-${Date.now()}-${Math.random()}`,
                  type: 'system',
                  content: label,
                  variant: 'sub_agent_done',
                  streaming: false,
                  timestamp: Date.now()
                }
              ],
              streamingThinking: '',
              streamingText: '',
              currentStreamingType: null,
              activeThinkingId: null,
              awaitingFirstContent: false,
              generatingLabel: ''
            });
          } else {
          }
          return;
        }

        debugLog('处理其他类型消息:', message.role);
        this.messages.push({
          role: message.role,
          content: message.content || ''
        });
      }
    });

    // 处理最后一个assistant消息
    if (currentAssistantMessage && currentAssistantMessage.actions.length > 0) {
      this.messages.push(currentAssistantMessage);
    }

    this.conversationHasImages = historyHasImages;
    this.conversationHasVideos = historyHasVideos;

    debugLog(`历史消息渲染完成，共 ${this.messages.length} 条消息`);
    const lastRendered = this.messages?.[this.messages.length - 1] || null;
    const lastRenderedActions = Array.isArray(lastRendered?.actions) ? lastRendered.actions : [];
    const lastRenderedText = lastRenderedActions
      .filter((a: any) => a?.type === 'text')
      .map((a: any) => String(a?.content || ''))
      .join('\n');
    restoreDebugLog('history:render:done', {
      currentConversationId: this.currentConversationId,
      historyInputCount: Array.isArray(historyMessages) ? historyMessages.length : -1,
      finalMessagesCount: Array.isArray(this.messages) ? this.messages.length : -1,
      lastRole: lastRendered?.role || null,
      lastActionsCount: lastRenderedActions.length,
      lastHasThinkingAction: lastRenderedActions.some((a: any) => a?.type === 'thinking'),
      lastHasTextAction: lastRenderedActions.some((a: any) => a?.type === 'text'),
      lastTextLength: lastRenderedText.length,
      lastTextHasShowHtml: /<show_html/i.test(lastRenderedText)
    });
    jsonDebug('history.render:done', {
      historyInputCount: Array.isArray(historyMessages) ? historyMessages.length : -1,
      finalMessagesCount: Array.isArray(this.messages) ? this.messages.length : -1,
      lastRole: this.messages?.[this.messages.length - 1]?.role || null,
      toolArgsParseFailCount,
      toolResultParseFailCount
    });
    this.logMessageState('renderHistoryMessages:after-render');
    this.lastHistoryLoadedConversationId = this.currentConversationId || null;

    // 强制更新视图
    this.$forceUpdate();

    // 确保滚动到底部
    this.$nextTick(() => {
      this.scrollHistoryToBottomInstant();
      setTimeout(() => {
        const blockCount =
          this.$el && this.$el.querySelectorAll
            ? this.$el.querySelectorAll('.message-block').length
            : 'N/A';
        debugLog('[Messages] DOM 渲染统计', {
          blocks: blockCount,
          conversationId: this.currentConversationId
        });
      }, 0);
    });
  }
};

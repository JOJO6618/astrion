// @ts-nocheck
import { debugLog } from './common';
import { t } from '@/locales';
import { displayMessageTime } from './conversation/display';
const jsonDebug = (...args: any[]) => {
  void args;
};
// 后端子智能体完成消息格式匹配（须与后端 modules/i18n.py 的 zh/en 两种产出一致；\u 转义仅为通过 i18n 审计）
const SUB_AGENT_DONE_PREFIX_RE =
  /^(?:✅\s*)?(?:\u5b50\u667a\u80fd\u4f53|Sub-agent)\s*#?\s*(\d+)\s*(?:\u4efb\u52a1\u6458\u8981|task summary)[:：]/;
const BG_RUN_COMMAND_DONE_PREFIX_RE =
  /^\[(?:\u540e\u53f0\s*run_command\s*\u5b8c\u6210|Background\s*run_command\s*finished)\]/;

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

export const historyMethods = {
  // ==========================================
  // 关键功能：获取并显示历史对话内容
  // ==========================================
  async fetchAndDisplayHistory() {
    return this.refreshConversationSnapshot();
  },
  buildHistoryState(historyMessages, conversationId = this.currentConversationId) {
    const target = {
      messages: [],
      currentConversationId: conversationId,
      cloneToolArguments: this.cloneToolArguments.bind(this),
      buildToolLabel: this.buildToolLabel.bind(this),
      logMessageState: () => {},
      $forceUpdate: () => {},
      $nextTick: () => {},
      conversationHasImages: false,
      conversationHasVideos: false
    };
    historyMethods.renderHistoryMessages.call(target, historyMessages);
    return {
      messages: target.messages,
      hasImages: target.conversationHasImages,
      hasVideos: target.conversationHasVideos
    };
  },
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
      const sourceId = message.message_id || `legacy-${index}`;
      const actionId = (kind, suffix = '') => `${sourceId}:${kind}${suffix}`;
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
        const normalizedCreatedAt = displayMessageTime(rawCreatedAt);
        this.messages.push({
          role: 'user',
          id: sourceId,
          message_id: message.message_id,
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
            id: sourceId,
            message_id: message.message_id,
            metadata: { ...meta },
            actions: [],
            streamingThinking: '',
            streamingText: '',
            currentStreamingType: null,
            activeThinkingId: null,
            awaitingFirstContent: false, // 历史消息不应该显示等待动画
            generatingLabel: ''
          };
        }

        // 行内引用：工具循环的多条 assistant 持久化消息会合并成同一条前端消息，
        // citations 需随合并按 id 去重汇聚，否则 chip 无数据可渲染
        const msgCitations = message.metadata?.citations;
        if (Array.isArray(msgCitations) && msgCitations.length) {
          const merged = Array.isArray(currentAssistantMessage.metadata?.citations)
            ? [...currentAssistantMessage.metadata.citations]
            : [];
          const seen = new Set(merged.map((c) => c?.id));
          msgCitations.forEach((c) => {
            if (c && c.id && !seen.has(c.id)) {
              seen.add(c.id);
              merged.push(c);
            }
          });
          currentAssistantMessage.metadata = {
            ...(currentAssistantMessage.metadata || {}),
            citations: merged
          };
        }

        const content = message.content || '';
        const reasoningText = (message.reasoning_content || '').trim();

        if (reasoningText) {
          const blockId = actionId('thinking');
          currentAssistantMessage.actions.push({
            id: blockId,
            type: 'thinking',
            content: reasoningText,
            streaming: false,
            collapsed: true,
            timestamp: Date.now(),
            blockId,
            // 产生该思考内容的模型（codex 对话渲染为「思考摘要」标题）
            modelKey: message.metadata?.model_key || null
          });
          debugLog('添加思考内容:', reasoningText.substring(0, 50) + '...');
        }

        // 处理普通文本内容（移除思考标签后的内容）
        const textContent = content.trim();
        if (textContent) {
          currentAssistantMessage.actions.push({
            id: actionId('text'),
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
              id: actionId('tool', `:${toolCall.id || tcIndex}`),
              type: 'tool',
              toolBatchId: `history-${message.tool_calls[0]?.id || currentAssistantMessage.actions.length - tcIndex}`,
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

          // Tool call identity is authoritative, including concurrent same-name calls.
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
              id: sourceId,
              message_id: message.message_id,
              role: 'assistant',
              actions: [
                {
                  id: actionId('system'),
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
          }
          // 未匹配到系统通知标签时不额外插入提示，直接结束本次消息处理
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
    jsonDebug('history.render:done', {
      historyInputCount: Array.isArray(historyMessages) ? historyMessages.length : -1,
      finalMessagesCount: Array.isArray(this.messages) ? this.messages.length : -1,
      lastRole: this.messages?.[this.messages.length - 1]?.role || null,
      toolArgsParseFailCount,
      toolResultParseFailCount
    });
    this.logMessageState('renderHistoryMessages:after-render');
  }
};

// @ts-nocheck
import { debugLog } from '../common';
import { trackStreamAttempt } from './aiStream';
import { currentConversationSession, ownsConversationSession } from '../conversation/session';

const toolAliases = (data: any) =>
  [
    data?.id,
    data?.preparing_id,
    data?.preparingId,
    data?.execution_id,
    data?.executionId,
    data?.tool_call_id
  ]
    .filter((id) => id != null && id !== '')
    .map(String);

function registerToolAliases(host: any, action: any, data: any) {
  if (data.preparing_id != null) {
    action.tool.preparingId = data.preparing_id;
    action.tool.preparing_id = data.preparing_id;
  }
  if (data.execution_id != null) action.tool.executionId = data.execution_id;
  const aliases = new Set([...toolAliases(data), ...toolAliases(action.tool), String(action.id)]);
  for (const alias of aliases) host.toolRegisterAction(action, alias);
}

function findToolAction(host: any, data: any) {
  const aliases = toolAliases(data);
  for (const alias of aliases) {
    const indexed = host.toolFindAction(alias);
    if (indexed) {
      registerToolAliases(host, indexed, data);
      return indexed;
    }
  }
  return null;
}
import { approvalMethods } from './approvals';

export const toolMethods = {
  handleToolPreparing(data: any, eventIdx: number) {
    debugLog('[TaskPolling] 工具准备中:', data.name);

    if (this.dropToolEvents) {
      return;
    }

    const msg = this.chatEnsureAssistantMessage();
    if (!msg) {
      return;
    }

    if (msg.awaitingFirstContent) {
      msg.awaitingFirstContent = false;
      msg.generatingLabel = '';
    }

    trackStreamAttempt(this, data, eventIdx);
    const existingAction = findToolAction(this, data);
    if (existingAction) {
      if (String(existingAction.tool?.status || '').toLowerCase() === 'preparing') {
        existingAction.tool.message = data.message || existingAction.tool.message;
        existingAction.tool.intent_complete ||= data.intent_complete === true;
        if (data.intent) {
          existingAction.tool.intent_full = data.intent;
          existingAction.tool.intent_rendered = data.intent;
        }
        this.$forceUpdate();
      }
      return;
    }

    const action = {
      id: `${data.task_id}:${eventIdx}:tool`,
      type: 'tool',
      toolBatchId: this._summaryToolBatchId || `tool-${data.id}`,
      tool: {
        id: data.id,
        name: data.name,
        preparingId: data.preparing_id ?? data.id,
        preparing_id: data.preparing_id ?? data.id,
        arguments: {},
        argumentSnapshot: null,
        argumentLabel: '',
        status: 'preparing',
        result: null,
        // 准备态文案由前端按工具名统一出（chatDisplay），不再使用后端下发的 message，
        // 避免服务端语言与界面语言不一致、极简/完整视图两套文案。
        message: '',
        intent_full: data.intent || '',
        intent_rendered: data.intent || '',
        intent_complete: data.intent_complete === true
      },
      timestamp: Date.now()
    };

    msg.actions.push(action);
    this.preparingTools.set(data.id, action);
    registerToolAliases(this, action, data);
    this.toolTrackAction(data.name, action);

    this.$forceUpdate();
    this.conditionalScrollToBottom();

    if (this.monitorPreviewTool) {
      this.monitorPreviewTool(data);
    }
  },
  handleToolStart(data: any, eventIdx: number) {
    debugLog('[TaskPolling] 工具开始:', data.name);

    if (this.dropToolEvents) {
      return;
    }

    // 兜底：工具开始执行时关闭等待动画（正常流程由 tool_preparing 关闭，
    // 快照后的首个增量也可能直接是 tool_start）
    const msgForAwaiting = this.chatEnsureAssistantMessage();
    if (msgForAwaiting && msgForAwaiting.awaitingFirstContent) {
      msgForAwaiting.awaitingFirstContent = false;
      msgForAwaiting.generatingLabel = '';
    }

    trackStreamAttempt(this, data, eventIdx);
    let action = findToolAction(this, data);
    if (!action) {
      const msg = this.chatEnsureAssistantMessage();
      if (!msg) {
        return;
      }
      action = {
        id: `${data.task_id}:${eventIdx}:tool`,
        type: 'tool',
        toolBatchId: this._summaryToolBatchId || `tool-${data.id}`,
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

    for (const [alias, stored] of this.preparingTools) {
      if (stored === action) this.preparingTools.delete(alias);
    }
    if (data.name && action.tool.name !== data.name) {
      this.toolReleaseAction?.(action.tool.name, action);
      action.tool.name = data.name;
    }
    action.tool.status = 'running';
    action.tool.arguments = data.arguments || {};
    action.tool.intent_complete = true;
    if (typeof data.arguments?.intent === 'string') {
      action.tool.intent_full = data.arguments.intent;
    }
    action.tool.argumentSnapshot = this.cloneToolArguments(data.arguments);
    action.tool.argumentLabel = this.buildToolLabel(action.tool.argumentSnapshot);
    action.tool.message = null;
    action.tool.id = data.execution_id ?? data.id;
    action.tool.executionId = data.execution_id ?? data.id;

    registerToolAliases(this, action, data);
    this.toolTrackAction(action.tool.name, action);
    this.$forceUpdate();
    this.conditionalScrollToBottom();

    if (this.monitorQueueTool) {
      this.monitorQueueTool(data);
    }
  },
  handleToolIntent(data: any) {
    debugLog('[TaskPolling] 工具意图:', data.name, data.intent);

    if (this.dropToolEvents) {
      return;
    }

    // 查找对应的工具 action
    const action = findToolAction(this, data);

    if (action && action.tool) {
      const newIntent = data.intent || '';
      const wasComplete = action.tool.intent_complete === true;
      action.tool.intent_complete = wasComplete || data.intent_complete === true;

      // The closing quote may arrive without changing the text. Its completion
      // signal still needs to release the minimal summary entry gate.
      if (action.tool.intent_full === newIntent) {
        if (!wasComplete && action.tool.intent_complete) this.$forceUpdate();
        return;
      }

      // 停止之前的打字机效果
      if (action.tool._intentTyping) {
        action.tool._intentTyping = false;
        if (action.tool._intentTimer) {
          clearTimeout(action.tool._intentTimer);
          action.tool._intentTimer = null;
        }
      }

      // 更新完整 intent
      action.tool.intent_full = newIntent;

      const rendered = String(action.tool.intent_rendered || '');
      action.tool.intent_rendered = newIntent.startsWith(rendered) ? rendered : '';
      action.tool._intentTyping = true;
      const session = currentConversationSession(this);
      const totalDuration = Math.max(500, Math.min(1000, newIntent.length * 50));
      const charInterval = totalDuration / Math.max(1, newIntent.length);
      let charIndex = action.tool.intent_rendered.length;
      const typeNextChar = () => {
        if (session && !ownsConversationSession(this, session)) {
          action.tool._intentTyping = false;
          action.tool._intentTimer = null;
          return;
        }
        if (charIndex < newIntent.length && action.tool._intentTyping) {
          action.tool.intent_rendered += newIntent[charIndex++];
          this.$forceUpdate();
          action.tool._intentTimer = setTimeout(typeNextChar, charInterval);
        } else {
          action.tool._intentTyping = false;
          action.tool.intent_rendered = newIntent;
          action.tool._intentTimer = null;
          this.$forceUpdate();
        }
      };
      action.tool._intentTimer = setTimeout(typeNextChar, 50);

      // 更新 arguments 和 label
      if (action.tool.arguments) {
        action.tool.arguments.intent = newIntent;
        action.tool.argumentSnapshot = this.cloneToolArguments(action.tool.arguments);
        action.tool.argumentLabel = this.buildToolLabel(action.tool.argumentSnapshot);
      }

      this.$forceUpdate();
      debugLog('[TaskPolling] 已更新工具意图:', data.name);
    } else {
      debugLog('[TaskPolling] 未找到对应的工具 action:', data.id);
    }
  },
  handleToolUpdateAction(data: any) {
    if (this.dropToolEvents) {
      return;
    }

    debugLog('[TaskPolling] 更新action:', data.id, 'status:', data.status);

    let targetAction = findToolAction(this, data);
    if (!targetAction && data.preparing_id && this.preparingTools.has(data.preparing_id)) {
      targetAction = this.preparingTools.get(data.preparing_id);
    }

    if (!targetAction) {
      return;
    }

    if (data.status && data.status !== 'preparing') {
      for (const [alias, stored] of this.preparingTools) {
        if (stored === targetAction) this.preparingTools.delete(alias);
      }
    }
    if (data.status) {
      targetAction.tool.status = data.status;
      const terminalStatuses = new Set([
        'completed',
        'failed',
        'timeout',
        'terminated',
        'cancelled',
        'canceled'
      ]);
      if (terminalStatuses.has(String(data.status))) {
        this.refreshProjectGitSummary?.();
        this.fetchTerminalCount();
      }
    }
    if (data.result !== undefined) {
      targetAction.tool.result = data.result;

      // 处理个性化设置工具 - 刷新个人空间数据
      if (targetAction.tool && targetAction.tool.name === 'manage_personalization') {
        let result = data.result;
        if (typeof result === 'string') {
          try {
            result = JSON.parse(result);
          } catch (e) {
            /* ignore */
          }
        }
        // 处理主题变更
        if (result?.theme_changed === true && result.new_theme) {
          const theme = result.new_theme;
          if (typeof window !== 'undefined' && window.localStorage) {
            window.localStorage.setItem('agents_ui_theme', theme);
          }
          document.documentElement.setAttribute('data-theme', theme);
          document.body.setAttribute('data-theme', theme);
        }
        // 任何字段更新后都刷新个人空间数据
        (async () => {
          try {
            const { usePersonalizationStore } = await import('../../../stores/personalization');
            const personalizationStore = usePersonalizationStore();
            // 强制刷新个人空间数据
            await personalizationStore.fetchPersonalization();
          } catch (e) {
            // 静默处理，不影响主流程
          }
        })();
      }
    }
    if (data.message !== undefined) {
      targetAction.tool.message = data.message;
    }
    if (data.content !== undefined) {
      targetAction.tool.content = data.content;
    }

    // 待办工具执行完成后主动刷新左侧待办列表（网页端不再依赖 websocket todo_updated）
    if (data.status === 'completed') {
      const toolName = String(targetAction?.tool?.name || '').toLowerCase();
      if (
        toolName.startsWith('todo_') ||
        toolName === 'todo_create' ||
        toolName === 'todo_update_task'
      ) {
        this.scheduleTodoListRefresh(80);
      }
    }

    this.$forceUpdate();
    this.conditionalScrollToBottom();

    if (this.monitorResolveTool && data.status === 'completed') {
      this.monitorResolveTool(data);
    }
  },
  ...approvalMethods
};

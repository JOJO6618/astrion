// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { usePersonalizationStore } from '../../../stores/personalization';
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

export const toolMethods = {
  handleToolPreparing(data: any) {
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

    // 幂等兜底：同 id 的工具块已存在（重复/重放事件）时复用，不重复 push。
    // 若块已进入 running（tool_start 已处理），不得把状态回退为 preparing。
    let existingAction = null;
    if (data.id && this.preparingTools.has(data.id)) {
      existingAction = this.preparingTools.get(data.id);
    } else if (data.id) {
      existingAction = this.toolFindAction(data.id, data.preparing_id, data.execution_id);
    }
    if (existingAction) {
      if (String(existingAction.tool?.status || '').toLowerCase() === 'preparing') {
        existingAction.tool.message = data.message || existingAction.tool.message;
        if (data.intent) {
          existingAction.tool.intent_full = data.intent;
          existingAction.tool.intent_rendered = data.intent;
        }
        this.$forceUpdate();
      }
      return;
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
    this.preparingTools.set(data.id, action);
    this.toolRegisterAction(action, data.id);
    this.toolTrackAction(data.name, action);

    this.$forceUpdate();
    this.conditionalScrollToBottom();

    if (this.monitorPreviewTool) {
      this.monitorPreviewTool(data);
    }
  },
  handleToolStart(data: any) {
    debugLog('[TaskPolling] 工具开始:', data.name);

    if (this.dropToolEvents) {
      return;
    }

    // 兜底：工具开始执行时关闭等待动画（正常流程由 tool_preparing 关闭，
    // 但从头重建等场景可能跳过 tool_preparing 直接收到 tool_start）
    const msgForAwaiting = this.chatEnsureAssistantMessage();
    if (msgForAwaiting && msgForAwaiting.awaitingFirstContent) {
      msgForAwaiting.awaitingFirstContent = false;
      msgForAwaiting.generatingLabel = '';
    }

    let action = null;
    if (data.preparing_id && this.preparingTools.has(data.preparing_id)) {
      action = this.preparingTools.get(data.preparing_id);
      this.preparingTools.delete(data.preparing_id);
    } else {
      action = this.toolFindAction(data.id, data.preparing_id, data.execution_id);
    }

    if (!action) {
      const msg = this.chatEnsureAssistantMessage();
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
    action.tool.argumentSnapshot = this.cloneToolArguments(data.arguments);
    action.tool.argumentLabel = this.buildToolLabel(action.tool.argumentSnapshot);
    action.tool.message = null;
    action.tool.id = data.id;
    action.tool.executionId = data.id;

    this.toolRegisterAction(action, data.id);
    this.toolTrackAction(data.name, action);
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
    const action = this.toolFindAction(data.id, data.preparing_id, data.execution_id);

    if (action && action.tool) {
      const newIntent = data.intent || '';

      // 如果 intent 没有变化，跳过
      if (action.tool.intent_full === newIntent) {
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

      // 判断是否是历史恢复：只有从头重建时才直接显示
      const isHistoryRestore = this._rebuildingFromScratch;

      if (isHistoryRestore) {
        // 历史恢复，直接显示完整 intent
        action.tool.intent_rendered = newIntent;
        debugLog('[TaskPolling] 历史恢复，直接显示 intent');
      } else {
        // 新工具块，逐字符显示
        debugLog('[TaskPolling] 新工具块，开始打字机效果');
        action.tool.intent_rendered = '';
        action.tool._intentTyping = true;

        // 计算每个字符的间隔时间
        // 总时长1秒，但最少0.5秒
        const totalDuration = Math.max(500, Math.min(1000, newIntent.length * 50));
        const charInterval = totalDuration / newIntent.length;

        let charIndex = 0;
        const typeNextChar = () => {
          if (charIndex < newIntent.length && action.tool._intentTyping) {
            action.tool.intent_rendered += newIntent[charIndex];
            charIndex++;
            this.$forceUpdate();
            action.tool._intentTimer = setTimeout(typeNextChar, charInterval);
          } else {
            action.tool._intentTyping = false;
            action.tool.intent_rendered = newIntent; // 确保完整
            action.tool._intentTimer = null;
            this.$forceUpdate();
          }
        };

        action.tool._intentTimer = setTimeout(typeNextChar, 50); // 延迟50ms开始
      }

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

    let targetAction = this.toolFindAction(data.id, data.preparing_id, data.execution_id);
    if (!targetAction && data.preparing_id && this.preparingTools.has(data.preparing_id)) {
      targetAction = this.preparingTools.get(data.preparing_id);
    }

    if (!targetAction) {
      return;
    }

    if (data.status) {
      targetAction.tool.status = data.status;
      const terminalStatuses = new Set(['completed', 'failed', 'timeout', 'terminated', 'cancelled', 'canceled']);
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
  handleToolApprovalRequired(data: any) {
    const approval = data?.approval;
    if (!approval || !approval.approval_id) {
      return;
    }
    if (!Array.isArray(this.pendingToolApprovals)) {
      this.pendingToolApprovals = [];
    }
    const idx = this.pendingToolApprovals.findIndex(
      (item: any) => item && item.approval_id === approval.approval_id
    );
    if (idx >= 0) {
      this.pendingToolApprovals.splice(idx, 1, approval);
    } else {
      this.pendingToolApprovals.push(approval);
    }
    if (this.approvalAutoCloseTimer) {
      clearTimeout(this.approvalAutoCloseTimer);
      this.approvalAutoCloseTimer = null;
    }
    if ((this.autoApprovalFeedLines || []).length === 0) {
      this.autoApprovalFinalMessage = '';
    }
    // 自动审核模式 + 个人空间开启「隐藏工具审核面板」时，不自动展开审核面板
    const hideApprovalPanel =
      this.currentPermissionMode === 'auto_approval' &&
      usePersonalizationStore().form.hide_tool_approval_panel !== false;
    if (!hideApprovalPanel) {
      this.rightCollapsed = false;
      if (this.rightWidth < this.minPanelWidth) {
        this.rightWidth = this.minPanelWidth;
      }
      if (this.isMobileViewport && this.activeMobileOverlay !== 'approval') {
        this.openMobileOverlay('approval');
      }
    }
    this.$forceUpdate();
  },
  handleUserQuestionsRequired(data: any) {
    const incoming = Array.isArray(data?.questions)
      ? data.questions
      : data?.question
        ? [data.question]
        : [];
    const questions = incoming.filter((item: any) => item && item.question_id);
    if (!questions.length) {
      return;
    }
    if (!Array.isArray(this.pendingUserQuestions)) {
      this.pendingUserQuestions = [];
    }
    questions.forEach((question: any) => {
      const idx = this.pendingUserQuestions.findIndex(
        (item: any) => item && item.question_id === question.question_id
      );
      if (idx >= 0) {
        this.pendingUserQuestions.splice(idx, 1, question);
      } else {
        this.pendingUserQuestions.push(question);
      }
    });
    this.pendingUserQuestions.sort((a: any, b: any) => {
      const batchA = String(a?.batch_id || '');
      const batchB = String(b?.batch_id || '');
      if (batchA && batchB && batchA !== batchB) {
        return Number(a?.created_at || 0) - Number(b?.created_at || 0);
      }
      return Number(a?.batch_index || 0) - Number(b?.batch_index || 0);
    });
    this.userQuestionActiveIndex = Math.min(
      Math.max(0, Number(this.userQuestionActiveIndex || 0)),
      Math.max(0, this.pendingUserQuestions.length - 1)
    );
    this.userQuestionDialogVisible = true;
    this.userQuestionMinimized = false;
    this.notifyUserQuestion(questions[0]);
    this.$forceUpdate();
  },
  handleUserQuestionsResolved(data: any) {
    const ids = Array.isArray(data?.question_ids)
      ? data.question_ids.map((id: any) => String(id || '')).filter(Boolean)
      : data?.question_id
        ? [String(data.question_id)]
        : [];
    if (!ids.length || !Array.isArray(this.pendingUserQuestions)) {
      return;
    }
    this.pendingUserQuestions = this.pendingUserQuestions.filter(
      (item: any) => item && !ids.includes(String(item.question_id || ''))
    );
    if (!this.pendingUserQuestions.length) {
      this.userQuestionDialogVisible = false;
      this.userQuestionMinimized = false;
      this.userQuestionActiveIndex = 0;
      this.restoreUserQuestionTitle();
    } else {
      this.userQuestionActiveIndex = Math.min(
        this.userQuestionActiveIndex,
        this.pendingUserQuestions.length - 1
      );
    }
    this.$forceUpdate();
  },
  handlePlanApprovalRequired(data: any) {
    const approval = data?.approval;
    if (!approval || !approval.approval_id) {
      return;
    }
    if (!Array.isArray(this.pendingPlanApprovals)) {
      this.pendingPlanApprovals = [];
    }
    const idx = this.pendingPlanApprovals.findIndex(
      (item: any) => item && item.approval_id === approval.approval_id
    );
    if (idx >= 0) {
      this.pendingPlanApprovals.splice(idx, 1, approval);
    } else {
      this.pendingPlanApprovals.push(approval);
    }
    this.pendingPlanApprovals.sort(
      (a: any, b: any) => Number(a?.created_at || 0) - Number(b?.created_at || 0)
    );
    this.$forceUpdate();
  },
  handlePlanApprovalResolved(data: any) {
    const id = String(data?.approval_id || '').trim();
    if (!id || !Array.isArray(this.pendingPlanApprovals)) {
      return;
    }
    this.pendingPlanApprovals = this.pendingPlanApprovals.filter(
      (item: any) => item && String(item.approval_id || '') !== id
    );
    // 批准后后端已切换运行模式/恢复权限与执行环境，刷新显示（多标签页同步场景）
    if (String(data?.decision || '') === 'approved') {
      this.fetchWorkMode();
      this.fetchPermissionMode();
      this.fetchExecutionMode();
    }
    this.$forceUpdate();
  },
  notifyUserQuestion(question: any) {
    try {
      if (!this.userQuestionOriginalTitle && typeof document !== 'undefined') {
        this.userQuestionOriginalTitle = document.title || '';
      }
      if (typeof document !== 'undefined') {
        if (this.userQuestionTitleBlinkTimer) {
          clearInterval(this.userQuestionTitleBlinkTimer);
          this.userQuestionTitleBlinkTimer = null;
        }
        this.userQuestionTitleBlinkRed = true;
        const applyTitle = () => {
          const dot = this.userQuestionTitleBlinkRed ? '🔴' : '⚪';
          document.title = `${dot} 需要回答 - Agents`;
          this.userQuestionTitleBlinkRed = !this.userQuestionTitleBlinkRed;
        };
        applyTitle();
        this.userQuestionTitleBlinkTimer = setInterval(applyTitle, 900);
      }
      if (typeof window === 'undefined' || !('Notification' in window)) {
        return;
      }
      const title = '需要你确认一个问题';
      const body = String(question?.question || '').slice(0, 120);
      if (Notification.permission === 'granted') {
        new Notification(title, { body });
      } else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then((permission) => {
          if (permission === 'granted') {
            new Notification(title, { body });
          }
        }).catch(() => undefined);
      }
    } catch (_error) {
      // ignore notification errors
    }
  },
  restoreUserQuestionTitle() {
    try {
      if (this.userQuestionTitleBlinkTimer) {
        clearInterval(this.userQuestionTitleBlinkTimer);
        this.userQuestionTitleBlinkTimer = null;
      }
      if (this.userQuestionOriginalTitle && typeof document !== 'undefined') {
        document.title = this.userQuestionOriginalTitle;
      }
      this.userQuestionOriginalTitle = '';
      this.userQuestionTitleBlinkRed = true;
    } catch (_error) {
      // ignore
    }
  },
  handleToolApprovalResolved(data: any) {
    const approvalId = data?.approval_id;
    if (!approvalId || !Array.isArray(this.pendingToolApprovals)) {
      return;
    }
    this.pendingToolApprovals = this.pendingToolApprovals.filter(
      (item: any) => item && item.approval_id !== approvalId
    );
    const decision = String(data?.decision || '').trim().toLowerCase();
    const reason = String(data?.reason || '').trim();
    if (decision === 'approved' || decision === 'rejected') {
      const decisionText = decision === 'approved' ? '批准通过' : '拒绝';
      this.autoApprovalFinalMessage = `${decisionText}\n原因：${reason || '未提供'}`;
    }
    // 电脑端：审批完成后延迟折叠面板（给用户留出查看结果时间）
    if (!this.pendingToolApprovals.length && !this.isMobileViewport) {
      if (this.approvalAutoCloseTimer) {
        clearTimeout(this.approvalAutoCloseTimer);
      }
      this.approvalAutoCloseTimer = setTimeout(() => {
        this.rightCollapsed = true;
      }, 3000);
    }
    this.$forceUpdate();
  },
  handleAutoApprovalProgress(data: any) {
    const progress = data?.progress;
    if (!progress || typeof progress !== 'object') {
      return;
    }
    if (!Array.isArray(this.autoApprovalFeedLines)) {
      this.autoApprovalFeedLines = [];
    }
    this.autoApprovalTitle = '自动审批记录';
    if (progress.stage === 'start') {
      this.autoApprovalFeedLines = ['自动审批开始'];
      this.autoApprovalFinalMessage = '';
    } else if (progress.stage === 'run_command' && progress.command) {
      this.autoApprovalFeedLines.push(String(progress.command));
    }
    this.autoApprovalFeedLines = this.autoApprovalFeedLines.slice(-20);
    this.$forceUpdate();
  },
  handleAppendPayload(data: any) {
    debugLog('[TaskPolling] 文件追加:', data.path);
    this.chatAddAppendPayloadAction(data);
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  },
  handleModifyPayload(data: any) {
    debugLog('[TaskPolling] 文件修改:', data.path);
    this.chatAddModifyPayloadAction(data);
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  }
};

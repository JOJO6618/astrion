// @ts-nocheck
import { debugLog } from '../common';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const dialogMethods = {
  openPersonalPage() {
    if (this.isPolicyBlocked('block_personal_space', '个人空间已被管理员禁用')) {
      return;
    }
    this.personalizationOpenDrawer();
  },
  minimizeUserQuestionDialog() {
    if (!Array.isArray(this.pendingUserQuestions) || !this.pendingUserQuestions.length) {
      return;
    }
    this.userQuestionDialogVisible = false;
    this.userQuestionMinimized = true;
  },
  restoreUserQuestionDialog() {
    if (!Array.isArray(this.pendingUserQuestions) || !this.pendingUserQuestions.length) {
      return;
    }
    this.userQuestionDialogVisible = true;
    this.userQuestionMinimized = false;
  },
  async fetchPendingUserQuestions() {
    if (!this.currentConversationId) {
      this.pendingUserQuestions = [];
      return;
    }
    try {
      const response = await fetch(
        `/api/user-questions/pending?conversation_id=${encodeURIComponent(this.currentConversationId)}`
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      this.pendingUserQuestions = items;
      if (items.length > 0) {
        this.userQuestionActiveIndex = Math.min(
          Math.max(0, Number(this.userQuestionActiveIndex || 0)),
          Math.max(0, items.length - 1)
        );
      } else {
        this.userQuestionDialogVisible = false;
        this.userQuestionMinimized = false;
        this.userQuestionActiveIndex = 0;
      }
    } catch (_error) {
      // ignore
    }
  },
  approveToolApproval(approvalId) {
    return this.decideToolApproval(approvalId, 'approved');
  },
  async fetchPendingPlanApprovals() {
    if (!this.currentConversationId) {
      this.pendingPlanApprovals = [];
      return;
    }
    try {
      const response = await fetch(
        `/api/plan-approvals/pending?conversation_id=${encodeURIComponent(this.currentConversationId)}`
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        return;
      }
      this.pendingPlanApprovals = Array.isArray(payload.items) ? payload.items : [];
    } catch (_error) {
      // ignore
    }
  },
  async submitPlanApproval(payload) {
    const approvalId = String(payload?.approval_id || '').trim();
    if (!approvalId) {
      return;
    }
    this.answeringPlanApprovalIds = [approvalId];
    try {
      const response = await fetch(
        `/api/plan-approvals/${encodeURIComponent(approvalId)}/answer`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            approved: payload?.approved === true,
            comment: String(payload?.comment || '').trim()
          })
        }
      );
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result?.success) {
        throw new Error(result?.message || result?.error || '提交计划决策失败');
      }
      this.pendingPlanApprovals = (this.pendingPlanApprovals || []).filter(
        (item) => item && String(item.approval_id || '') !== approvalId
      );
      // 批准后后端已自动切换到 execute 并恢复权限与执行环境：立即刷新模式显示
      if (payload?.approved === true) {
        this.fetchWorkMode();
        this.fetchPermissionMode();
        this.fetchExecutionMode();
        this.uiPushToast({
          title: '计划已批准',
          message: '已切换到执行模式，开始实施',
          type: 'success',
          duration: 2200
        });
      } else {
        this.uiPushToast({
          title: '已拒绝计划',
          message: 'AI 将根据你的意见修订后重新提交',
          type: 'info',
          duration: 2200
        });
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '提交计划决策失败');
      this.uiPushToast({ title: '提交计划决策失败', message: msg, type: 'error' });
    } finally {
      this.answeringPlanApprovalIds = [];
    }
  },
  rejectToolApproval(approvalId) {
    return this.decideToolApproval(approvalId, 'rejected');
  },
  openReviewDialog() {
    if (this.isPolicyBlocked('block_conversation_review', '对话引用已被管理员禁用')) {
      return;
    }
    if (!this.isConnected) {
      this.uiPushToast({
        title: '无法使用',
        message: '当前未连接，无法生成回顾文件',
        type: 'warning'
      });
      return;
    }
    this.reviewDialogOpen = true;
    this.reviewSelectedConversationId = null;
    this.reviewPreviewLines = [];
    this.reviewPreviewError = null;
    this.reviewGeneratedPath = null;
    this.closeQuickMenu();
    // 弹窗使用独立列表（仅含有内容的对话），加载完成后自动选中首个可用项
    this.loadReviewConversations();
  },
  autoSelectReviewConversation() {
    if (this.reviewSelectedConversationId) return;
    const fallback = this.reviewConversations.find((c) => c.id !== this.currentConversationId);
    if (fallback && fallback.id) {
      this.reviewSelectedConversationId = fallback.id;
      this.loadReviewPreview(fallback.id);
    }
  },
  closeHeaderMenu() {
    this.headerMenuOpen = false;
  },
  handleReviewSelect(id) {
    if (id === this.currentConversationId) {
      this.uiPushToast({
        title: '无法引用当前对话',
        message: '请选择其他对话生成回顾',
        type: 'warning'
      });
      return;
    }
    this.reviewSelectedConversationId = id;
    this.loadReviewPreview(id);
  },
  confirmAction(options = {}) {
    return this.uiRequestConfirm(options);
  },
  async submitUserQuestionAnswers(answers) {
    const list = Array.isArray(answers) ? answers : [];
    if (!list.length) {
      return;
    }
    const ids = list.map((item) => String(item?.question_id || '')).filter(Boolean);
    this.answeringUserQuestionIds = ids;
    try {
      for (const answer of list) {
        const questionId = String(answer?.question_id || '').trim();
        if (!questionId) {
          continue;
        }
        const response = await fetch(`/api/user-questions/${encodeURIComponent(questionId)}/answer`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            selected_option_id: answer?.selected_option_id || undefined,
            text: answer?.text || '',
            dismissed: answer?.dismissed === true
          })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload?.success) {
          throw new Error(payload?.message || payload?.error || '提交回答失败');
        }
      }
      this.pendingUserQuestions = (this.pendingUserQuestions || []).filter(
        (item) => item && !ids.includes(String(item.question_id || ''))
      );
      if (!this.pendingUserQuestions.length) {
        this.userQuestionDialogVisible = false;
        this.userQuestionMinimized = false;
        this.userQuestionActiveIndex = 0;
        if (typeof this.restoreUserQuestionTitle === 'function') {
          this.restoreUserQuestionTitle();
        }
      } else {
        this.userQuestionActiveIndex = Math.min(this.userQuestionActiveIndex, this.pendingUserQuestions.length - 1);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '提交回答失败');
      this.uiPushToast({ title: '提交回答失败', message: msg, type: 'error' });
    } finally {
      this.answeringUserQuestionIds = [];
    }
  },
  // 用户点击「不回答」：只将当前查看的这个问题标记为 dismissed，其余问题保持待回答。
  // 模型侧会收到「用户没有回答或不想回答」的工具结果并改为在对话中直接提问。
  async dismissUserQuestions(questionId) {
    const id = String(questionId || '').trim();
    if (!id) {
      return;
    }
    await this.submitUserQuestionAnswers([{ question_id: id, dismissed: true }]);
  },
  async answerUserQuestionFromComposer(text) {
    const clean = String(text || '').trim();
    if (!clean || !Array.isArray(this.pendingUserQuestions) || !this.pendingUserQuestions.length) {
      return false;
    }
    const index = Math.min(
      Math.max(0, Number(this.userQuestionActiveIndex || 0)),
      Math.max(0, this.pendingUserQuestions.length - 1)
    );
    const question = this.pendingUserQuestions[index] || this.pendingUserQuestions[0];
    if (!question?.question_id) {
      return false;
    }
    await this.submitUserQuestionAnswers([{ question_id: question.question_id, text: clean }]);
    if (this.pendingUserQuestions.length > 0) {
      this.userQuestionDialogVisible = true;
      this.userQuestionMinimized = false;
    }
    return true;
  },
  async decideToolApproval(approvalId, decision) {
    const id = String(approvalId || '').trim();
    if (!id) {
      return;
    }
    if (!Array.isArray(this.decidingApprovalIds)) {
      this.decidingApprovalIds = [];
    }
    if (!this.decidingApprovalIds.includes(id)) {
      this.decidingApprovalIds.push(id);
    }
    try {
      const response = await fetch(`/api/tool-approvals/${encodeURIComponent(id)}/decision`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ decision })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.message || payload?.error || '提交审批失败');
      }
      this.pendingToolApprovals = (this.pendingToolApprovals || []).filter(
        (item) => item && item.approval_id !== id
      );
      if (!this.pendingToolApprovals.length) {
        this.rightCollapsed = true;
        if (this.isMobileViewport && this.activeMobileOverlay === 'approval') {
          this.closeMobileOverlay();
        }
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '审批失败');
      this.uiPushToast({
        title: '审批失败',
        message: msg,
        type: 'error'
      });
    } finally {
      this.decidingApprovalIds = (this.decidingApprovalIds || []).filter((item) => item !== id);
    }
  }
};

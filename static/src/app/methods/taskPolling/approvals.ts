// @ts-nocheck
import { t } from '@/locales';
import { usePersonalizationStore } from '../../../stores/personalization';
import { invalidateAuxiliaryRequest } from '../auxiliaryOwnership';
import {
  isFullAccessApproval,
  needsHumanDecision,
  normalizeApproval,
  statusFromProgress,
  updateReviewRecords
} from '@/components/input/approvalModel';

export const approvalMethods = {
  handleToolApprovalRequired(data: any) {
    const approval = data?.approval;
    if (!approval?.approval_id) return;
    if (data.conversation_id && data.conversation_id !== this.currentConversationId) return;
    if ((this.resolvedToolApprovalIds || []).includes(approval.approval_id)) return;
    this.approvalSnapshotVersion += 1;
    if (!Array.isArray(this.pendingToolApprovals)) this.pendingToolApprovals = [];
    const idx = this.pendingToolApprovals.findIndex(
      (item) => item?.approval_id === approval.approval_id
    );
    const previous = idx >= 0 ? this.pendingToolApprovals[idx] : undefined;
    const normalized = normalizeApproval(
      approval,
      previous,
      this.currentPermissionMode === 'auto_approval'
    );
    const record = (this.approvalReviewRecords || []).find(
      (entry) => entry.approval_id === approval.approval_id
    );
    if (!normalized.auto_review_progress?.length && record?.progress?.length) {
      normalized.auto_review_progress = record.progress;
      normalized.auto_review_status =
        approval.auto_review_status ||
        statusFromProgress(record.progress[record.progress.length - 1]);
    }
    if (idx >= 0) this.pendingToolApprovals.splice(idx, 1, normalized);
    else this.pendingToolApprovals.push(normalized);
    if (this.approvalAutoCloseTimer) {
      clearTimeout(this.approvalAutoCloseTimer);
      this.approvalAutoCloseTimer = null;
    }
    const hideApprovalPanel =
      this.currentPermissionMode === 'auto_approval' &&
      usePersonalizationStore().form.hide_tool_approval_panel !== false;
    const mandatory = isFullAccessApproval(normalized) && needsHumanDecision(normalized);
    // Updates to the current id preserve an intentional manual collapse.
    if (
      (idx < 0 || (mandatory && !isFullAccessApproval(previous))) &&
      (!hideApprovalPanel || mandatory)
    ) {
      this.restoreToolApprovalPanel();
    }
    this.$forceUpdate();
  },
  handleUserQuestionsRequired(data: any) {
    invalidateAuxiliaryRequest(this, 'user-questions');
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
    invalidateAuxiliaryRequest(this, 'user-questions');
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
    invalidateAuxiliaryRequest(this, 'plan-approvals');
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
    invalidateAuxiliaryRequest(this, 'plan-approvals');
    const id = String(data?.approval_id || '').trim();
    if (!id || !Array.isArray(this.pendingPlanApprovals)) {
      return;
    }
    this.pendingPlanApprovals = this.pendingPlanApprovals.filter(
      (item: any) => item && String(item.approval_id || '') !== id
    );
    // 弹窗被关闭（最小化）期间批准已解决：无待批事项时复位，避免状态栏按钮残留
    if (!this.pendingPlanApprovals.length) {
      this.planApprovalMinimized = false;
    }
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
          document.title = t('appTasks.answerNeededTitle', { dot });
          this.userQuestionTitleBlinkRed = !this.userQuestionTitleBlinkRed;
        };
        applyTitle();
        this.userQuestionTitleBlinkTimer = setInterval(applyTitle, 900);
      }
      if (typeof window === 'undefined' || !('Notification' in window)) {
        return;
      }
      const title = t('appTasks.questionConfirmTitle');
      const body = String(question?.question || '').slice(0, 120);
      if (Notification.permission === 'granted') {
        new Notification(title, { body });
      } else if (Notification.permission !== 'denied') {
        Notification.requestPermission()
          .then((permission) => {
            if (permission === 'granted') {
              new Notification(title, { body });
            }
          })
          .catch(() => undefined);
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
    if (!approvalId || !Array.isArray(this.pendingToolApprovals)) return;
    if (data.conversation_id && data.conversation_id !== this.currentConversationId) return;
    if (
      !['approved', 'rejected', 'expired', 'cancelled', 'timeout'].includes(String(data.decision))
    )
      return;
    if ((this.resolvedToolApprovalIds || []).includes(approvalId)) return;
    this.approvalSnapshotVersion += 1;
    this.resolvedToolApprovalIds = [...(this.resolvedToolApprovalIds || []), approvalId].slice(
      -256
    );
    // A resolved event is terminal. Keep its display snapshot for manual reopening,
    // without turning a completed record into a pending request.
    const approval = this.pendingToolApprovals.find((item) => item?.approval_id === approvalId);
    const records = this.approvalReviewRecords || [];
    const previous = records.find((record) => record.approval_id === approvalId);
    if (approval) {
      const record = {
        ...previous,
        id: previous?.id || `auto-${approvalId}`,
        kind: 'auto',
        approval_id: approvalId,
        progress: previous?.progress || approval.auto_review_progress || [],
        final_decision: ['approved', 'rejected'].includes(data.decision)
          ? data.decision
          : undefined,
        reason: String(data.reason || approval.reason || ''),
        tool_name: approval.tool_name,
        approval: { ...approval, status: String(data.decision) }
      };
      this.approvalReviewRecords = [
        ...records.filter((entry) => entry.approval_id !== approvalId),
        record
      ].slice(-30);
    }
    this.pendingToolApprovals = this.pendingToolApprovals.filter(
      (item) => item?.approval_id !== approvalId
    );
    if (!this.pendingToolApprovals.length) {
      if (this.approvalAutoCloseTimer) clearTimeout(this.approvalAutoCloseTimer);
      this.approvalAutoCloseTimer = null;
      this.approvalPanelCollapsed = true;
    }
    this.$forceUpdate();
  },
  handleAutoApprovalProgress(data: any) {
    const progress = data?.progress;
    if (!progress || typeof progress !== 'object') return;
    if (data.conversation_id && data.conversation_id !== this.currentConversationId) return;
    const approvalId = data.approval_id || this.pendingToolApprovals?.[0]?.approval_id;
    if (approvalId && (this.resolvedToolApprovalIds || []).includes(approvalId)) return;
    this.approvalReviewRecords = updateReviewRecords(this.approvalReviewRecords || [], 'auto', {
      ...data,
      approval_id: approvalId
    });
    if (approvalId) {
      this.pendingToolApprovals = (this.pendingToolApprovals || []).map((item) =>
        item.approval_id === approvalId
          ? {
              ...item,
              auto_review_required: true,
              auto_review_status: statusFromProgress(progress, item.auto_review_status),
              auto_review_progress: [...(item.auto_review_progress || []), { ...progress }].slice(
                -100
              )
            }
          : item
      );
    }
    this.$forceUpdate();
  }
};

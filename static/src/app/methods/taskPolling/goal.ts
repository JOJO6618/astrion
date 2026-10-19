// @ts-nocheck
import { t } from '@/locales';
import { updateReviewRecords } from '@/components/input/approvalModel';
import { ensureMessageSession } from '../message/ownership';
import { ownsConversationSession } from '../conversation/session';

const operations = new WeakMap<object, object>();

export const goalMethods = {
  handleGoalProgress(data: any) {
    if (!data || data.conversation_id !== this.currentConversationId) return;
    if (data.workspace_id && data.workspace_id !== this.currentHostWorkspaceId) return;
    if (Number(data.revision || 0) < Number(this.goalProgress?.revision || 0)) return;
    this.goalProgress = { ...data };
    this.goalRunning = ['running', 'reviewing', 'retrying'].includes(data.status);
    this.goalModeArmed = false;
  },
  handleGoalReviewProgress(data: any) {
    if (data?.conversation_id !== this.currentConversationId) return;
    if (!this.goalRunning) return;
    const progress = data?.progress || data;
    this.approvalReviewRecords = updateReviewRecords(this.approvalReviewRecords || [], 'goal', {
      ...data,
      progress
    });
  },
  handleGoalCompleted(data: any) {
    this.handleGoalProgress({ ...data, status: 'done' });
  },
  handleGoalStopped(data: any) {
    this.handleGoalProgress({ ...data, status: data?.status || 'paused' });
  },
  async handleStartGoal(config: any) {
    return this.submitGoalAction('start', config);
  },
  async handleContinueGoal(limits?: any) {
    if (!['paused', 'blocked'].includes(this.goalProgress?.status)) return false;
    return this.submitGoalAction('continue', limits);
  },
  async submitGoalAction(action: 'start' | 'continue', config?: any) {
    if (operations.has(this) || this.taskInProgress || this.streamingMessage) return false;
    const session = ensureMessageSession(this);
    const operation = {};
    operations.set(this, operation);
    const owns = () => ownsConversationSession(this, session) && operations.get(this) === operation;
    const text =
      action === 'start'
        ? String(config?.goal || '').trim()
        : t('goal.resumeMessage', { goal: this.goalProgress?.goal || '' });
    try {
      const sent = await this.sendMessage({
        presetText: text,
        goalAction: action,
        goalOptions: config
          ? { max_turns: config.max_turns ?? null, max_tokens: config.max_tokens ?? null }
          : undefined
      });
      if (!owns()) return false;
      if (sent) {
        this.goalDialogOpen = false;
        this.currentWorkMode = 'execute';
        if (!['auto_approval', 'unrestricted'].includes(this.currentPermissionMode))
          this.currentPermissionMode = 'auto_approval';
      }
      return sent;
    } finally {
      if (operations.get(this) === operation) operations.delete(this);
    }
  },
  async handleCloseGoal() {
    return this.controlGoal('close');
  },
  async handlePauseGoal() {
    return this.controlGoal('pause');
  },
  async controlGoal(action: 'close' | 'pause') {
    const session = ensureMessageSession(this);
    const progress = this.goalProgress;
    if (!session.conversationId || !progress?.run_id || operations.has(this)) return false;
    const operation = {};
    operations.set(this, operation);
    const owns = () => ownsConversationSession(this, session) && operations.get(this) === operation;
    try {
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(session.conversationId)}/goal`,
        {
          method: 'POST',
          signal: session.controller.signal,
          headers: {
            'Content-Type': 'application/json',
            'X-Astrion-Workspace-Id': session.workspaceId
          },
          body: JSON.stringify({ action, run_id: progress.run_id })
        }
      );
      const result = await response.json();
      if (!owns()) return false;
      if (!response.ok || !result.success) throw new Error(result.error || t('goal.actionFailed'));
      this.handleGoalProgress(result.data);
      return true;
    } catch (error) {
      if (owns() && error?.name !== 'AbortError')
        this.uiPushToast({ title: t('goal.title'), message: error.message, type: 'error' });
      return false;
    } finally {
      if (operations.get(this) === operation) operations.delete(this);
    }
  }
};

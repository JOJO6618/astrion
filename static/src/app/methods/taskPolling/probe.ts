// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { currentConversationSession, ownsConversationSession } from '../conversation/session';

const requests = new WeakMap<object, object>();

export const probeMethods = {
  scheduleTodoListRefresh(delayMs = 120) {
    if (this._todoRefreshTimer) clearTimeout(this._todoRefreshTimer);
    const session = currentConversationSession(this);
    this._todoRefreshTimer = setTimeout(
      () => {
        this._todoRefreshTimer = null;
        if (currentConversationSession(this) !== session) return;
        Promise.resolve(this.fetchTodoList?.()).catch(() => {});
      },
      Math.max(0, Number(delayMs) || 0)
    );
  },
  startRunningStateReconcile() {
    if (this.runningStateReconcileTimer) return;
    this.runningStateReconcileTimer = setInterval(() => {
      void this.reconcileRunningStateOnce();
    }, 2500);
    void this.reconcileRunningStateOnce();
  },
  stopRunningStateReconcile() {
    if (this.runningStateReconcileTimer) clearInterval(this.runningStateReconcileTimer);
    this.runningStateReconcileTimer = null;
    requests.delete(this);
  },
  async reconcileRunningStateOnce() {
    const session = currentConversationSession(this);
    const id = this.currentConversationId;
    if (
      !session ||
      !ownsConversationSession(this, session) ||
      !id ||
      this.historyLoading ||
      session.submission
    )
      return;
    if (requests.get(this) === session) return;
    const submissionVersion = session.submissionVersion;
    requests.set(this, session);
    const owns = () =>
      ownsConversationSession(this, session) &&
      requests.get(this) === session &&
      !session.submission &&
      session.submissionVersion === submissionVersion &&
      this.currentConversationId === id;
    try {
      const query = session.workspaceId
        ? `?workspace_id=${encodeURIComponent(session.workspaceId)}`
        : '';
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(id)}/running-status${query}`,
        {
          signal: session.controller.signal
        }
      );
      const result = await response.json();
      if (!owns() || !response.ok || !result?.success) return;
      const status = result.data || {};
      const task = useTaskStore();
      // A subscribed main task closes through its ordered event stream. The probe
      // never clears messages, rewinds a cursor or competes with that subscription.
      if (task.isPolling && task.currentTaskId === status.main_task_id) return;
      if (task.isPolling && !status.is_main_running) return;
      const needsSubscription = status.is_main_running && status.main_task_id;
      const backgroundChanged =
        !!status.has_running_sub_agents !== !!this.waitingForSubAgent ||
        !!status.has_running_background_commands !== !!this.waitingForBackgroundCommand;
      const activityChanged = !!status.is_truly_active !== !!this.taskInProgress;
      if (needsSubscription || backgroundChanged || activityChanged || this.streamingMessage) {
        await this.refreshConversationSnapshot();
      }
    } catch (error) {
      if (owns() && error?.name !== 'AbortError')
        debugLog('running snapshot unavailable', String(error));
    } finally {
      if (requests.get(this) === session) requests.delete(this);
    }
  },
  stopWaitingTaskProbe() {},
  startWaitingTaskProbe() {
    this.startRunningStateReconcile();
  },
  stopMultiAgentTaskProbe() {},
  startMultiAgentTaskProbe() {
    this.startRunningStateReconcile();
  },
  async restoreSubAgentWaitingState() {
    if (currentConversationSession(this) && this.currentConversationId)
      this.startRunningStateReconcile();
  }
};

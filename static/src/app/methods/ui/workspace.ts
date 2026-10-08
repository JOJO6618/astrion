// @ts-nocheck
import { t } from '@/locales';

// The task index covers every workspace; navigation does not retire its requests.
const requests = new WeakMap<object, AbortController>();
const retiredHosts = new WeakSet<object>();
const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
const terminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'stopped']);

export const workspaceMethods = {
  stopWorkspaceTasksRefresh() {
    retiredHosts.add(this);
    const controller = requests.get(this);
    requests.delete(this);
    controller?.abort();
    if (this.runningWorkspaceTasksRefreshTimer) {
      clearTimeout(this.runningWorkspaceTasksRefreshTimer);
      this.runningWorkspaceTasksRefreshTimer = null;
    }
  },
  async refreshRunningWorkspaceTasks() {
    if (retiredHosts.has(this)) return false;
    const previous = requests.get(this);
    const controller = new AbortController();
    requests.set(this, controller);
    previous?.abort();
    const owns = () => requests.get(this) === controller && !retiredHosts.has(this);
    if (this.runningWorkspaceTasksRefreshTimer) {
      clearTimeout(this.runningWorkspaceTasksRefreshTimer);
      this.runningWorkspaceTasksRefreshTimer = null;
    }
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      this.runningWorkspaceTasks = [];
      requests.delete(this);
      return true;
    }
    let retry = false;
    let waitingForTitle = false;
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const resp = await fetch('/api/tasks', { signal: controller.signal, cache: 'no-store' });
      const payload = await resp.json();
      if (!owns()) return false;
      if (!resp.ok || !payload?.success || !Array.isArray(payload.data)) {
        throw new Error(payload?.error || t('appUi.fetchRunningTasksFailed'));
      }
      const tasks = payload.data;
      for (const task of tasks) {
        if (!task?.conversation_id || !task?.conversation_title) continue;
        this.applyConversationTitleUpdate?.(
          task.conversation_id,
          task.conversation_title,
          'task-index'
        );
        const ageMs = Date.now() - Number(task.updated_at || 0) * 1000;
        if (
          terminalStatuses.has(String(task.status || '')) &&
          ageMs >= 0 &&
          ageMs < 90000 &&
          this.isPlaceholderConversationTitle?.(task.conversation_title)
        ) {
          waitingForTitle = true;
        }
      }
      const trackedTaskIds = this.getStoredWorkspaceTaskIdSet?.('tracked') || new Set();
      const acknowledged = new Set(
        Array.isArray(this.acknowledgedCompletedTaskIds) ? this.acknowledgedCompletedTaskIds : []
      );
      const storedAcknowledged = this.getStoredWorkspaceTaskIdSet?.('acknowledged') || new Set();
      storedAcknowledged.forEach((id: string) => acknowledged.add(id));
      this.acknowledgedCompletedTaskIds = Array.from(acknowledged);
      const previousVisibleIds = new Set(
        (Array.isArray(this.runningWorkspaceTasks) ? this.runningWorkspaceTasks : [])
          .map((task: any) => String(task?.task_id || ''))
          .filter(Boolean)
      );
      const visibleTasks = tasks.filter((task: any) => {
        const taskId = String(task?.task_id || '');
        const status = String(task?.status || '');
        const conversationId = String(task?.conversation_id || '');
        if (!taskId) return false;
        if (activeStatuses.has(status)) {
          trackedTaskIds.add(taskId);
          return true;
        }
        // Completion acknowledgments use the view at commit, not at request start.
        if (terminalStatuses.has(status) && conversationId === this.currentConversationId) {
          if (!acknowledged.has(taskId)) {
            acknowledged.add(taskId);
            this.acknowledgedCompletedTaskIds = [...this.acknowledgedCompletedTaskIds, taskId];
          }
          trackedTaskIds.delete(taskId);
          return false;
        }
        return (
          terminalStatuses.has(status) &&
          (previousVisibleIds.has(taskId) || trackedTaskIds.has(taskId)) &&
          !acknowledged.has(taskId)
        );
      });
      this.runningWorkspaceTasks = visibleTasks;
      visibleTasks.forEach((task: any) => {
        const taskId = String(task?.task_id || '');
        if (taskId) trackedTaskIds.add(taskId);
      });
      acknowledged.forEach((id: string) => trackedTaskIds.delete(id));
      this.setStoredWorkspaceTaskIdSet?.('tracked', trackedTaskIds);
      this.setStoredWorkspaceTaskIdSet?.('acknowledged', acknowledged);
      if (Array.isArray(this.hostWorkspaces)) {
        const counts = visibleTasks.reduce((acc: Record<string, number>, task: any) => {
          const ws = String(task?.workspace_id || '');
          if (ws && activeStatuses.has(String(task?.status || ''))) {
            acc[ws] = (acc[ws] || 0) + 1;
          }
          return acc;
        }, {});
        this.hostWorkspaces = this.hostWorkspaces.map((item: any) => ({
          ...item,
          running_task_count: Number(counts[item.workspace_id] || 0)
        }));
      }
      return true;
    } catch (error) {
      if (!owns()) return false;
      console.warn('刷新运行中任务失败:', error);
      // A transport failure does not prove that tasks are idle. Keep the index
      // and retry, including when the first request failed before finding a task.
      retry = true;
      return false;
    } finally {
      clearTimeout(timeout);
      if (owns()) {
        requests.delete(this);
        const hasActiveTask = (this.runningWorkspaceTasks || []).some((task: any) =>
          activeStatuses.has(String(task?.status || ''))
        );
        if (retry || hasActiveTask || waitingForTitle) {
          this.runningWorkspaceTasksRefreshTimer = window.setTimeout(
            () => {
              this.runningWorkspaceTasksRefreshTimer = null;
              void this.refreshRunningWorkspaceTasks();
            },
            retry ? 3000 : 1500
          );
        }
      }
    }
  },
  async openRunningWorkspaceTask(task) {
    const workspaceId = String(task?.workspace_id || '').trim();
    const conversationId = String(task?.conversation_id || '').trim();
    if (!workspaceId || !conversationId) return;
    await this.handleSelectWorkspaceConversation({ conversationId, workspaceId });
    if (this.currentConversationId !== conversationId) return;
    const taskId = String(task?.task_id || '');
    if (taskId && !activeStatuses.has(String(task?.status || ''))) {
      this.acknowledgeCompletedWorkspaceTask(taskId);
    }
    // enterConversation already hydrated the snapshot and attached its cursor.
  }
};

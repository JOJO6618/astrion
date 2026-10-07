// @ts-nocheck
import { t } from '@/locales';
import { beginAuxiliaryRequest } from '../auxiliaryOwnership';

export const workspaceMethods = {
  async refreshRunningWorkspaceTasks() {
    const owns = beginAuxiliaryRequest(this, 'workspace-tasks');
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      this.runningWorkspaceTasks = [];
      if (this.runningWorkspaceTasksRefreshTimer) {
        clearTimeout(this.runningWorkspaceTasksRefreshTimer);
        this.runningWorkspaceTasksRefreshTimer = null;
      }
      return;
    }
    try {
      const resp = await fetch('/api/tasks');
      const payload = await resp.json().catch(() => ({}));
      if (!owns()) return;
      if (!resp.ok || !payload?.success) {
        throw new Error(payload?.error || t('appUi.fetchRunningTasksFailed'));
      }
      const tasks = Array.isArray(payload.data) ? payload.data : [];
      const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
      const terminalStatuses = new Set(['succeeded', 'failed', 'canceled']);
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
        // 如果完成的是当前正在查看的对话，用户已经“看到了”，不要再进入待查看/显示勾。
        if (terminalStatuses.has(status) && conversationId === this.currentConversationId) {
          if (!acknowledged.has(taskId)) {
            acknowledged.add(taskId);
            this.acknowledgedCompletedTaskIds = [
              ...(Array.isArray(this.acknowledgedCompletedTaskIds)
                ? this.acknowledgedCompletedTaskIds
                : []),
              taskId
            ];
          }
          trackedTaskIds.delete(taskId);
          return false;
        }
        // 刚完成的任务保留为“待查看”状态，直到用户点击进入该对话。
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
      const hasActiveVisibleTask = visibleTasks.some((task: any) =>
        activeStatuses.has(String(task?.status || ''))
      );
      if (this.runningWorkspaceTasksRefreshTimer) {
        clearTimeout(this.runningWorkspaceTasksRefreshTimer);
        this.runningWorkspaceTasksRefreshTimer = null;
      }
      // 如果用户已经切到其他对话/工作区，当前运行任务没有本地轮询事件可触发列表更新。
      // 这里轻量轮询任务列表，使 loader 能在后台任务完成后自动切成“待查看”的勾。
      if (hasActiveVisibleTask) {
        this.runningWorkspaceTasksRefreshTimer = window.setTimeout(() => {
          this.runningWorkspaceTasksRefreshTimer = null;
          void this.refreshRunningWorkspaceTasks?.();
        }, 1500);
      }
      if (Array.isArray(this.hostWorkspaces)) {
        const counts = visibleTasks.reduce((acc: Record<string, number>, task: any) => {
          const ws = String(task?.workspace_id || '');
          const status = String(task?.status || '');
          if (ws && activeStatuses.has(status)) acc[ws] = (acc[ws] || 0) + 1;
          return acc;
        }, {});
        this.hostWorkspaces = this.hostWorkspaces.map((item: any) => ({
          ...item,
          running_task_count: Number(counts[item.workspace_id] || 0)
        }));
      }
    } catch (error) {
      if (!owns()) return;
      console.warn('刷新运行中任务失败:', error);
      this.runningWorkspaceTasks = [];
      if (this.runningWorkspaceTasksRefreshTimer) {
        clearTimeout(this.runningWorkspaceTasksRefreshTimer);
        this.runningWorkspaceTasksRefreshTimer = null;
      }
    }
  },
  async openRunningWorkspaceTask(task) {
    const workspaceId = String(task?.workspace_id || '').trim();
    const conversationId = String(task?.conversation_id || '').trim();
    if (!workspaceId || !conversationId) {
      return;
    }
    await this.handleSelectWorkspaceConversation({ conversationId, workspaceId });
    if (this.currentConversationId !== conversationId) return;
    const taskId = String(task?.task_id || '');
    if (
      taskId &&
      !['pending', 'running', 'cancel_requested'].includes(String(task?.status || ''))
    ) {
      this.acknowledgeCompletedWorkspaceTask(taskId);
      return;
    }
    // loadConversation already atomically hydrated the snapshot and attached its cursor.
    return;
  }
};

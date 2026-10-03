// @ts-nocheck

export const workspaceTaskMethods = {
  getVisibleWorkspaceTaskForConversation(conversationId) {
    const id = String(conversationId || '').trim();
    if (!id) return null;
    const tasks = Array.isArray(this.runningWorkspaceTasks) ? this.runningWorkspaceTasks : [];
    return (
      tasks.find((task: any) => String(task?.conversation_id || '') === id) || null
    );
  },
  getStoredWorkspaceTaskIdSet(kind = 'tracked') {
    if (typeof window === 'undefined') return new Set();
    const key =
      kind === 'acknowledged'
        ? 'agents.hostWorkspaceTasks.acknowledged'
        : 'agents.hostWorkspaceTasks.tracked';
    try {
      const raw = window.localStorage?.getItem(key);
      const values = JSON.parse(raw || '[]');
      return new Set(
        (Array.isArray(values) ? values : [])
          .map((item: any) => String(item || '').trim())
          .filter(Boolean)
      );
    } catch {
      return new Set();
    }
  },
  setStoredWorkspaceTaskIdSet(kind = 'tracked', values) {
    if (typeof window === 'undefined') return;
    const key =
      kind === 'acknowledged'
        ? 'agents.hostWorkspaceTasks.acknowledged'
        : 'agents.hostWorkspaceTasks.tracked';
    try {
      window.localStorage?.setItem(
        key,
        JSON.stringify(Array.from(values || []).map((item: any) => String(item)))
      );
    } catch {
      // ignore
    }
  },
  acknowledgeCompletedWorkspaceTask(taskId) {
    const id = String(taskId || '').trim();
    if (!id) return;
    const current = Array.isArray(this.acknowledgedCompletedTaskIds)
      ? this.acknowledgedCompletedTaskIds
      : [];
    if (!current.includes(id)) {
      this.acknowledgedCompletedTaskIds = [...current, id];
    }
    const acknowledged = this.getStoredWorkspaceTaskIdSet?.('acknowledged') || new Set();
    acknowledged.add(id);
    this.setStoredWorkspaceTaskIdSet?.('acknowledged', acknowledged);
    const tracked = this.getStoredWorkspaceTaskIdSet?.('tracked') || new Set();
    tracked.delete(id);
    this.setStoredWorkspaceTaskIdSet?.('tracked', tracked);
    this.runningWorkspaceTasks = (Array.isArray(this.runningWorkspaceTasks)
      ? this.runningWorkspaceTasks
      : []
    ).filter((item: any) => String(item?.task_id || '') !== id);
  },
  async getRunningTaskConversationId() {
    try {
      const resp = await fetch('/api/tasks');
      if (!resp.ok) {
        return null;
      }
      const payload = await resp.json();
      const tasks = Array.isArray(payload?.data) ? payload.data : [];
      const runningTask = tasks.find(
        (task: any) =>
          task?.status === 'running' &&
          task?.conversation_id &&
          (!(this.versioningHostMode || this.dockerProjectMode) ||
            !this.currentHostWorkspaceId ||
            task?.workspace_id === this.currentHostWorkspaceId)
      );
      return runningTask?.conversation_id || null;
    } catch (error) {
      console.warn('获取运行中任务失败:', error);
      return null;
    }
  }
};

// @ts-nocheck
import { isViewVisible } from './viewVisibility';
import { t } from '@/locales';
import { useConversationStore } from '../../../stores/conversation';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';

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
      // 定时任务／工作流等外部来源创建的对话不会推送到前端；发现列表缺失就补拉。
      this.maybeRefreshConversationListFromTasks?.(tasks);
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
        // 隐藏视图暂停跨对话索引的后续轮询，重新可见时由 resumeOnVisible 补一次。
        if ((retry || hasActiveTask || waitingForTitle) && isViewVisible()) {
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
  },
  /** 空闲轮询入口：已有索引请求在飞或刚刷新过就跳过，避免打断内部续排。 */
  refreshWorkspaceTaskIndexIdle() {
    if (retiredHosts.has(this)) return;
    if (!(this.versioningHostMode || this.dockerProjectMode)) return;
    if (requests.get(this)) return;
    const now = Date.now();
    if (now - Number(this.workspaceTaskIndexRefreshedAt || 0) < 4000) return;
    this.workspaceTaskIndexRefreshedAt = now;
    void this.refreshRunningWorkspaceTasks();
  },
  /**
   * 任务索引里出现侧边栏列表没有的对话（定时任务／工作流／项目 Git 等外部来源新建）时，
   * 静默重拉列表，避免必须关标签重开才能看到新对话。
   */
  maybeRefreshConversationListFromTasks(tasks) {
    if (!Array.isArray(tasks) || !tasks.length) return;
    if (!(this.versioningHostMode || this.dockerProjectMode)) return;
    if (this.searchActive) return;
    const store = useConversationStore();
    // 侧边栏只有在分组开启且确实有工作区时才渲染分组列表。
    const grouped =
      !!this.isGroupedSidebarSearch?.() &&
      Array.isArray(this.hostWorkspaces) &&
      this.hostWorkspaces.length > 0;
    const currentWorkspaceId = String(this.currentHostWorkspaceId || '');
    const listHasConversation = (workspaceId, conversationId) => {
      const cache = store.getWorkspaceCache(workspaceId);
      return (
        Array.isArray(cache?.list) &&
        cache.list.some((item: any) => String(item?.id || '') === conversationId)
      );
    };
    const missing = tasks.some((task: any) => {
      const conversationId = String(task?.conversation_id || '');
      if (!conversationId) return false;
      // 只处理活跃任务与刚结束的任务，避免已删除对话让轮询长期反复拉列表。
      const status = String(task?.status || '');
      const ageMs = Date.now() - Number(task?.updated_at || 0) * 1000;
      if (!activeStatuses.has(status) && !(terminalStatuses.has(status) && ageMs < 300000)) {
        return false;
      }
      const workspaceId = String(task?.workspace_id || '');
      if (grouped) {
        // 未展开的分组没有列表缓存，展开时会自行加载；这里只补已存在的分组。
        if (!store.workspaceGroups.some((group: any) => group.workspaceId === workspaceId))
          return false;
      } else if (workspaceId !== currentWorkspaceId) {
        return false;
      }
      return !listHasConversation(workspaceId, conversationId);
    });
    if (missing) this.refreshConversationListSilently('task-index');
  },
  /** 静默重拉对话列表：任务驱动最短间隔 5s，空闲兜底最短间隔 30s。 */
  refreshConversationListSilently(reason = 'idle') {
    if (retiredHosts.has(this)) return;
    if (!(this.versioningHostMode || this.dockerProjectMode)) return;
    if (!isViewVisible()) return;
    if (this.searchActive) return;
    const minInterval = reason === 'idle' ? 30000 : 5000;
    const now = Date.now();
    if (now - Number(this.silentConversationListRefreshAt || 0) < minInterval) return;
    this.silentConversationListRefreshAt = now;
    const grouped =
      !!this.isGroupedSidebarSearch?.() &&
      Array.isArray(this.hostWorkspaces) &&
      this.hostWorkspaces.length > 0;
    if (grouped) {
      const store = useConversationStore();
      for (const group of store.workspaceGroups || []) {
        if (!group?.workspaceId) continue;
        void store.loadWorkspaceConversations(group.workspaceId, { refresh: true });
      }
      return;
    }
    void this.loadConversationsList({ background: true, autoEnter: false, silent: true });
  }
};

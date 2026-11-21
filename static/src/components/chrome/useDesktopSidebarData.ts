import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { chatApi } from '@/chat/api';
import {
  useConversationStore,
  type ConversationSummary,
  type WorkspaceSearchGroup
} from '@/stores/conversation';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import { usePersonalizationStore } from '@/stores/personalization';
import { fetchConversationPage } from '@/stores/workspaceConversationCache';
import { workspaceMethods } from '@/app/methods/ui/workspace';
import { workspaceTaskMethods } from '@/app/methods/ui/workspaceTask';

export interface DesktopWorkspace {
  workspace_id: string;
  label: string;
  path: string;
  running_task_count: number;
}

export async function sidebarRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const payload = await response.json();
  if (!response.ok || payload.success === false) {
    throw new Error(payload.error || payload.message || response.statusText);
  }
  return payload as T;
}

export function useDesktopSidebarData() {
  const tabs = useConversationTabsStore();
  const list = useConversationStore();
  const personalization = usePersonalizationStore();
  const defaultWorkspace = ref('');
  const chatList = ref<ConversationSummary[]>([]);
  const submittedSearch = ref('');
  const currentTab = computed(() => {
    const active = tabs.activeTab;
    if (active?.kind !== 'page') return active;
    return tabs.mainTabs.find((tab) => tab.kind !== 'page' && tab.key === active.openerKey) || null;
  });
  const chatMode = computed(() => currentTab.value?.conversationKind === 'chat');
  const workspaceId = computed(
    () =>
      tabs.activeTab?.workspaceId ||
      currentTab.value?.workspaceId ||
      tabs.workspaceId ||
      defaultWorkspace.value
  );
  const conversationId = computed(() => tabs.activeTab?.conversationId || '');
  const grouped = computed(
    () => !chatMode.value && Boolean(personalization.form.group_sidebar_by_workspace)
  );
  let catalogRequest: Promise<void> | undefined;
  let chatRequest: Promise<void> | undefined;
  const agentRequests = new Map<string, Promise<void>>();
  const dirtyGroups = new Set<string>();
  let searchRequest: AbortController | undefined;
  let searchTimer = 0;
  let interval = 0;
  let lastCatalogRefresh = 0;
  let mounted = false;

  const taskIndex = reactive({
    ...workspaceTaskMethods,
    runningWorkspaceTasks: [] as any[],
    runningWorkspaceTasksRefreshTimer: null as number | null,
    workspaceTaskIndexRefreshedAt: 0,
    acknowledgedCompletedTaskIds: [] as string[],
    hostWorkspaces: [] as DesktopWorkspace[],
    get currentConversationId() {
      return conversationId.value;
    },
    refreshRunningWorkspaceTasks: workspaceMethods.refreshRunningWorkspaceTasks,
    refreshWorkspaceTaskIndexIdle: workspaceMethods.refreshWorkspaceTaskIndexIdle,
    stopWorkspaceTasksRefresh: workspaceMethods.stopWorkspaceTasksRefresh,
    maybeRefreshConversationListFromTasks: refreshChangedTaskLists
  });

  async function refreshCatalog(afterMutation = false) {
    if (afterMutation && catalogRequest) await catalogRequest;
    if (catalogRequest) return catalogRequest;
    catalogRequest = (async () => {
      try {
        const { data } = await sidebarRequest<{
          data: {
            workspaces: DesktopWorkspace[];
            default_workspace_id?: string;
            current_workspace_id?: string;
          };
        }>('/api/host/workspaces');
        const keep = new Set(data.workspaces.map((workspace) => workspace.workspace_id));
        for (const workspace of taskIndex.hostWorkspaces) {
          if (!keep.has(workspace.workspace_id))
            list.invalidateWorkspaceCache(workspace.workspace_id);
        }
        list.workspaceGroups = list.workspaceGroups.filter((group) => keep.has(group.workspaceId));
        taskIndex.hostWorkspaces = data.workspaces;
        defaultWorkspace.value = data.default_workspace_id || data.current_workspace_id || '';
        lastCatalogRefresh = Date.now();
      } catch (error) {
        console.warn('[desktop-sidebar] Workspace refresh failed', error);
      } finally {
        catalogRequest = undefined;
      }
    })();
    return catalogRequest;
  }

  function syncChatList() {
    if (!chatMode.value) return;
    list.conversations = chatList.value;
    list.conversationsLoading = false;
    list.loadingMoreConversations = false;
    list.hasMoreConversations = false;
  }

  async function refreshChat(afterMutation = false) {
    if (afterMutation && chatRequest) await chatRequest;
    if (chatRequest) return chatRequest;
    chatRequest = (async () => {
      try {
        chatList.value = await chatApi<ConversationSummary[]>('/conversations');
      } catch (error) {
        console.warn('[desktop-sidebar] Chat list refresh failed', error);
      } finally {
        chatRequest = undefined;
        syncChatList();
      }
    })();
    return chatRequest;
  }

  async function refreshAgent(id: string, more = false, afterMutation = false) {
    if (!id) return;
    const pending = agentRequests.get(id);
    if (pending) {
      if (!more && !afterMutation) return pending;
      await pending;
      return refreshAgent(id, more);
    }
    const group = list.workspaceGroups.find((item) => item.workspaceId === id);
    if (grouped.value && group) {
      if (group.loading || group.loadingMore) {
        dirtyGroups.add(id);
        return;
      }
      await list.loadWorkspaceConversations(id, { refresh: true });
      return;
    }
    const request = refreshAgentPage(id, more);
    agentRequests.set(id, request);
    try {
      await request;
    } finally {
      agentRequests.delete(id);
    }
  }

  async function refreshAgentPage(id: string, more: boolean) {
    const cache = list.getWorkspaceCache(id);
    const visible = () => !chatMode.value && !grouped.value && workspaceId.value === id;
    if (visible() && !cache.loaded) list.conversationsLoading = true;
    try {
      await fetchConversationPage(list, id, {
        offset: more ? cache.list.length : 0,
        limit: more
          ? list.conversationsLimit
          : Math.max(list.conversationsLimit, cache.list.length),
        refresh: true
      });
    } catch (error) {
      console.warn('[desktop-sidebar] Agent list refresh failed', error);
    } finally {
      if (visible()) {
        list.conversations = cache.list;
        list.conversationsLoading = false;
        list.hasMoreConversations = cache.hasMore;
        if (more) list.loadingMoreConversations = false;
      }
    }
  }

  function refreshVisibleLists() {
    if (chatMode.value) return refreshChat();
    if (grouped.value) {
      return Promise.all(
        list.workspaceGroups
          .filter((group) => group.expanded)
          .map((group) => refreshAgent(group.workspaceId))
      );
    }
    if (!list.loadingMoreConversations) return refreshAgent(workspaceId.value);
  }

  function loadMore() {
    if (chatMode.value || list.loadingMoreConversations || !list.hasMoreConversations) return;
    list.loadingMoreConversations = true;
    void refreshAgent(workspaceId.value, true);
  }

  async function search() {
    searchRequest?.abort();
    const query = submittedSearch.value.trim();
    list.searchActive = Boolean(query);
    list.searchInProgress = Boolean(query);
    list.searchResults = [];
    list.searchGroups = [];
    if (!query) return;
    const request = new AbortController();
    searchRequest = request;
    const params = new URLSearchParams({ q: query, limit: grouped.value ? '20' : '50' });
    if (!chatMode.value && !workspaceId.value) {
      list.searchInProgress = false;
      return;
    }
    if (!chatMode.value) params.set('workspace_id', workspaceId.value);
    if (grouped.value) params.set('all_workspaces', '1');
    try {
      if (chatMode.value) {
        const rows = await chatApi<ConversationSummary[]>(`/conversations?${params}`, {
          signal: request.signal
        });
        if (!request.signal.aborted) list.searchResults = rows;
      } else {
        const { data } = await sidebarRequest<{
          data: { results?: ConversationSummary[]; groups?: WorkspaceSearchGroup[] };
        }>(`/api/conversations/search?${params}`, { signal: request.signal });
        if (!request.signal.aborted) {
          list.searchResults = data.results || [];
          list.searchGroups = data.groups || [];
        }
      }
    } catch (error) {
      if (!request.signal.aborted) console.warn('[desktop-sidebar] Search failed', error);
    } finally {
      if (searchRequest === request) list.searchInProgress = false;
    }
  }

  function submitSearch(value: string) {
    window.clearTimeout(searchTimer);
    list.searchQuery = value;
    submittedSearch.value = value;
    void search();
  }

  function updateSearch(value: string) {
    list.searchQuery = value;
    if (!chatMode.value) return;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => submitSearch(value), 200);
  }

  function refreshChangedTaskLists(
    tasks: Array<{ workspace_id: string; conversation_id: string; conversation_title: string }>
  ) {
    if (chatMode.value) return;
    const changed = new Set<string>();
    for (const task of tasks) {
      const id = task.workspace_id;
      if (!task.conversation_id || (!grouped.value && id !== workspaceId.value)) continue;
      const group = list.workspaceGroups.find((item) => item.workspaceId === id && item.expanded);
      const cache = grouped.value ? group?.conversations : list.getWorkspaceCache(id).list;
      if (!cache) continue;
      const row = cache.find((entry) => entry.id === task.conversation_id);
      const previouslyObserved = taskTitles.get(task.conversation_id);
      taskTitles.set(task.conversation_id, task.conversation_title);
      if (
        previouslyObserved !== task.conversation_title &&
        (!row || row.title !== task.conversation_title)
      )
        changed.add(id);
    }
    for (const id of changed) void refreshAgent(id);
  }
  const taskTitles = new Map<string, string>();

  watch(conversationId, (id) => list.setCurrentConversationId(id || null));
  watch(
    () =>
      list.workspaceGroups.map(
        (group) => [group.workspaceId, group.loading, group.loadingMore] as const
      ),
    (groups) => {
      for (const [id, loading, loadingMore] of groups) {
        if (!loading && !loadingMore && dirtyGroups.delete(id)) void refreshAgent(id);
      }
    }
  );

  watch([chatMode, workspaceId, grouped], () => {
    if (chatMode.value) syncChatList();
    else if (workspaceId.value) {
      const cache = list.getWorkspaceCache(workspaceId.value);
      list.conversations = cache.list;
      list.hasMoreConversations = cache.hasMore;
      list.loadingMoreConversations = false;
      list.conversationsLoading = !grouped.value && !cache.loaded;
    } else {
      list.conversations = [];
      list.hasMoreConversations = false;
      list.conversationsLoading = false;
    }
    if (mounted) {
      if (submittedSearch.value) void search();
      void refreshVisibleLists();
    }
  });

  watch(
    () =>
      JSON.stringify(
        tabs.tabs
          .filter((tab) => tab.kind === 'conv')
          .map((tab) => [tab.conversationKind, tab.workspaceId, tab.conversationId, tab.title])
          .sort()
      ),
    () => {
      if (mounted) void refreshVisibleLists();
    }
  );

  onMounted(() => {
    mounted = true;
    void refreshCatalog();
    void personalization.fetchPersonalization();
    void refreshVisibleLists();
    taskIndex.refreshWorkspaceTaskIndexIdle();
    interval = window.setInterval(() => {
      taskIndex.refreshWorkspaceTaskIndexIdle();
      if (Date.now() - lastCatalogRefresh >= 30_000) {
        void refreshCatalog();
        void refreshVisibleLists();
        if (!personalization.visible) void personalization.fetchPersonalization();
      }
    }, 5000);
  });
  onBeforeUnmount(() => {
    mounted = false;
    searchRequest?.abort();
    window.clearTimeout(searchTimer);
    window.clearInterval(interval);
    taskIndex.stopWorkspaceTasksRefresh();
  });

  return {
    list,
    tabs,
    personalization,
    taskIndex,
    defaultWorkspace,
    chatMode,
    workspaceId,
    conversationId,
    grouped,
    refreshCatalog,
    refreshVisibleLists,
    loadMore,
    updateSearch,
    submitSearch,
    refreshConversationList: (scope: { chat: boolean; workspaceId: string }) => {
      if (scope.chat) void refreshChat(true);
      else void refreshAgent(scope.workspaceId, false, true);
      if (submittedSearch.value) void search();
    }
  };
}

export type DesktopSidebarData = ReturnType<typeof useDesktopSidebarData>;

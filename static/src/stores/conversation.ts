import { defineStore } from 'pinia';
import { workspaceScopedFetch as fetch } from '../app/methods/workspaceRequestScope';
import { emptyWorkspaceCache, workspaceCacheActions } from './workspaceConversationCache';
import type { WorkspaceListCache } from './workspaceConversationCache';

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string | number;
  total_messages?: number;
  total_tools?: number;
}

export interface WorkspaceConversationGroup {
  workspaceId: string;
  /** 当前工作区列表 */
  conversations: ConversationSummary[];
  loading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  offset: number;
  visibleOffset: number;
  visibleLimit: number;
  bufferLimit: number;
  fetchLimit: number;
  expanded: boolean;
  paging: { offset: number; hasMore: boolean; loaded: boolean };
}

/** 跨工作区搜索结果分组（后端 /api/conversations/search?all_workspaces=1 返回） */
export interface WorkspaceSearchGroup {
  workspace_id: string;
  label: string;
  count: number;
  results: ConversationSummary[];
}

interface ConversationState {
  conversations: ConversationSummary[];
  searchResults: ConversationSummary[];
  searchGroups: WorkspaceSearchGroup[];
  conversationInsertAnimations: Record<string, 'create' | 'duplicate' | 'duplicateSource'>;
  conversationListAnimationMode: 'idle' | 'create' | 'duplicate' | 'delete';
  pendingDeletingConversationIds: string[];
  conversationsLoading: boolean;
  hasMoreConversations: boolean;
  loadingMoreConversations: boolean;
  currentConversationId: string | null;
  currentConversationTitle: string;
  searchQuery: string;
  searchTimer: ReturnType<typeof setTimeout> | null;
  searchActive: boolean;
  searchInProgress: boolean;
  searchMoreAvailable: boolean;
  searchOffset: number;
  searchTotal: number;
  conversationsOffset: number;
  conversationsLimit: number;
  runningWorkspaceTasks: any[];
  acknowledgedCompletedTaskIds: string[];
  workspaceGroups: WorkspaceConversationGroup[];
  /** 每个工作区只有一份列表缓存；请求身份独立于导航。 */
  conversationCacheWorkspaceId: string;
  conversationsByWorkspace: Record<string, WorkspaceListCache>;
  conversationsCache: WorkspaceListCache;
}

export const useConversationStore = defineStore('conversation', {
  state: (): ConversationState => ({
    conversations: [],
    searchResults: [],
    searchGroups: [],
    conversationInsertAnimations: {},
    conversationListAnimationMode: 'idle',
    pendingDeletingConversationIds: [],
    conversationsLoading: false,
    hasMoreConversations: false,
    loadingMoreConversations: false,
    currentConversationId: null,
    currentConversationTitle: '',
    searchQuery: '',
    searchTimer: null,
    searchActive: false,
    searchInProgress: false,
    searchMoreAvailable: false,
    searchOffset: 0,
    searchTotal: 0,
    conversationsOffset: 0,
    conversationsLimit: 20,
    runningWorkspaceTasks: [],
    acknowledgedCompletedTaskIds: [],
    workspaceGroups: [],
    conversationCacheWorkspaceId: '',
    conversationsByWorkspace: {},
    conversationsCache: emptyWorkspaceCache()
  }),
  actions: {
    ...workspaceCacheActions,
    /** 当前对话 id 同步（由 app watcher 在 this.currentConversationId 变化时写入） */
    setCurrentConversationId(id: string | null) {
      this.currentConversationId = id;
    },
    updateConversationTitle(conversationId: string, title: string) {
      const id = String(conversationId || '').trim();
      const value = String(title || '').trim();
      if (!id || !value) return false;
      let changed = false;
      const lists = new Set<ConversationSummary[]>([
        this.conversations,
        this.conversationsCache.list,
        ...Object.values(this.conversationsByWorkspace).map((cache) => cache.list),
        this.searchResults,
        ...this.searchGroups.map((group) => group.results),
        ...this.workspaceGroups.map((group) => group.conversations)
      ]);
      for (const list of lists) {
        for (const item of list) {
          if (item.id === id && item.title !== value) {
            item.title = value;
            changed = true;
          }
        }
      }
      for (const task of this.runningWorkspaceTasks) {
        if (task.conversation_id === id && task.conversation_title !== value) {
          task.conversation_title = value;
          changed = true;
        }
      }
      return changed;
    },
    resetConversations() {
      /* Full reset retires all workspace caches, including pending requests. */
      for (const key of Object.keys(this.conversationsByWorkspace)) {
        this.invalidateWorkspaceCache(JSON.parse(key)[0]);
      }
      this.conversationsByWorkspace = {};
      this.conversationCacheWorkspaceId = '';
      this.activateWorkspaceCache('default');
      this.searchResults = [];
      this.searchGroups = [];
      this.conversationInsertAnimations = {};
      this.conversationListAnimationMode = 'idle';
      this.pendingDeletingConversationIds = [];
      this.searchActive = false;
      this.searchInProgress = false;
      this.searchMoreAvailable = false;
      this.searchOffset = 0;
      this.searchTotal = 0;
      this.hasMoreConversations = false;
      this.loadingMoreConversations = false;
      this.conversationsOffset = 0;
      this.runningWorkspaceTasks = [];
      this.acknowledgedCompletedTaskIds = [];
    },
    /** 切换/删除工作区时使双类型列表缓存整体失效：主列表是当前工作区作用域，
        不同工作区对话集合不同，缓存不可复用；conversations 重指到新空缓存 */
    resetConversationsTypeCache() {
      this.resetConversationsCacheOnly();
    },
    /** 内部共用：仅重置双类型缓存并对齐 conversations 引用 */
    resetConversationsCacheOnly() {
      this.invalidateWorkspaceCache(this.conversationCacheWorkspaceId || 'default');
    },
    cancelSearchTimer() {
      if (this.searchTimer) {
        clearTimeout(this.searchTimer);
        this.searchTimer = null;
      }
    },
    setWorkspaceGroups(groups: WorkspaceConversationGroup[]) {
      this.workspaceGroups = groups;
    },
    setWorkspaceGroupExpanded(workspaceId: string, expanded: boolean) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.expanded = expanded;
      }
    },
    setWorkspaceGroupConversations(workspaceId: string, conversations: ConversationSummary[]) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        /* 原地替换保持数组引用 */
        group.conversations.splice(0, group.conversations.length, ...conversations);
      }
    },
    appendWorkspaceGroupConversations(workspaceId: string, conversations: ConversationSummary[]) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.conversations.push(...conversations);
      }
    },
    setWorkspaceGroupLoading(workspaceId: string, loading: boolean) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.loading = loading;
      }
    },
    setWorkspaceGroupLoadingMore(workspaceId: string, loadingMore: boolean) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.loadingMore = loadingMore;
      }
    },
    setWorkspaceGroupHasMore(workspaceId: string, hasMore: boolean) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.hasMore = hasMore;
      }
    },
    setWorkspaceGroupOffset(workspaceId: string, offset: number) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.offset = offset;
      }
    },
    setWorkspaceGroupVisibleOffset(workspaceId: string, visibleOffset: number) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (group) {
        group.visibleOffset = Math.max(0, visibleOffset);
      }
    },
    ensureWorkspaceGroup(workspaceId: string) {
      if (!workspaceId) return;
      const exists = this.workspaceGroups.some((g) => g.workspaceId === workspaceId);
      if (!exists) {
        this.workspaceGroups.push({
          workspaceId,
          conversations: [],
          paging: { offset: 0, hasMore: false, loaded: false },
          loading: false,
          hasMore: false,
          loadingMore: false,
          offset: 0,
          visibleOffset: 0,
          visibleLimit: 5,
          bufferLimit: 20,
          fetchLimit: 25,
          expanded: true
        });
      }
    },
    async loadWorkspaceConversations(workspaceId: string, { reset = false, refresh = false } = {}) {
      if (!workspaceId) return;
      let index = this.workspaceGroups.findIndex((g) => g.workspaceId === workspaceId);
      if (index === -1) {
        this.ensureWorkspaceGroup(workspaceId);
        index = this.workspaceGroups.length - 1;
      }
      const group = this.workspaceGroups[index];
      // 固定分组对象，响应不会写入其他工作区。
      const listCache = group.conversations;
      const paging = group.paging;
      if (reset) {
        listCache.length = 0;
        paging.offset = 0;
        paging.hasMore = false;
        paging.loaded = false;
        group.visibleOffset = 0;
        group.offset = 0;
        group.hasMore = false;
      }
      if (group.loading || group.loadingMore || (paging.loaded && !refresh && !reset)) return;
      const fetchOffset = refresh ? 0 : paging.offset;
      group.loading = true;
      try {
        const response = await fetch(
          `/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}&limit=${group.fetchLimit}&offset=${fetchOffset}`
        );
        const data = await response.json();
        if (data.success) {
          const items = (data.data?.conversations || []).map((conv: any) => ({
            id: conv.id,
            title: conv.title,
            updated_at: conv.updated_at,
            total_messages: conv.total_messages,
            total_tools: conv.total_tools
          }));
          /* 原地写入缓存数组，保持 group.conversations 引用一致 */
          if (refresh) {
            const tail = listCache.slice(items.length);
            listCache.splice(0, listCache.length, ...items, ...tail);
          } else if (fetchOffset === 0) {
            listCache.splice(0, listCache.length, ...items);
          } else {
            listCache.push(...items);
          }
          paging.hasMore = !!data.data?.has_more;
          paging.loaded = true;
          if (!refresh) {
            paging.offset = fetchOffset + items.length;
          }
          // 同步分页。
          group.hasMore = paging.hasMore;
          group.offset = paging.offset;
        } else {
          console.error('加载工作区对话失败:', data.error);
        }
      } catch (error) {
        console.error('加载工作区对话异常:', error);
      } finally {
        group.loading = false;
      }
    },
    async loadMoreWorkspaceConversations(workspaceId: string) {
      const index = this.workspaceGroups.findIndex((g) => g.workspaceId === workspaceId);
      if (index === -1) return;
      const group = this.workspaceGroups[index];
      if (group.loadingMore) return;
      const hasHidden = group.conversations.length > group.visibleLimit;
      if (!hasHidden && !group.hasMore) return;

      group.loadingMore = true;
      // 先尝试从后端再加载 20 条作为新的缓冲
      if (group.hasMore) {
        try {
          const fetchOffset = group.conversations.length;
          const response = await fetch(
            `/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}&limit=${group.bufferLimit}&offset=${fetchOffset}`
          );
          const data = await response.json();
          if (data.success) {
            const items = (data.data?.conversations || []).map((conv: any) => ({
              id: conv.id,
              title: conv.title,
              updated_at: conv.updated_at,
              total_messages: conv.total_messages,
              total_tools: conv.total_tools
            }));
            group.conversations.push(...items);
            group.hasMore = !!data.data?.has_more;
            // 同步分页。
            const paging = group.paging;
            if (paging) {
              paging.offset = group.conversations.length;
              paging.hasMore = group.hasMore;
            }
          }
        } catch (error) {
          console.error('加载更多工作区对话异常:', error);
        }
      }
      // 把可见区扩大 20 条，让用户直接看到更多对话
      group.visibleLimit += group.bufferLimit;
      group.loadingMore = false;
    }
  }
});

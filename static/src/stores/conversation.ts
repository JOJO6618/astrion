import { defineStore } from 'pinia';

/* 侧边栏对话类型过滤器缓存（普通/多智能体）：记住用户上次查看的列表类型。 */
const SIDEBAR_CONVERSATION_TYPE_STORAGE_KEY = 'agents_sidebar_conversation_type';

const loadSidebarConversationType = (): 'normal' | 'multi_agent' => {
  try {
    const v = window.localStorage.getItem(SIDEBAR_CONVERSATION_TYPE_STORAGE_KEY);
    return v === 'multi_agent' ? 'multi_agent' : 'normal';
  } catch {
    return 'normal';
  }
};

const persistSidebarConversationType = (type: string): void => {
  try {
    window.localStorage.setItem(SIDEBAR_CONVERSATION_TYPE_STORAGE_KEY, type);
  } catch {
    /* ignore */
  }
};

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string | number;
  total_messages?: number;
  total_tools?: number;
}

export interface WorkspaceConversationGroup {
  workspaceId: string;
  /** 当前过滤器类型对应的列表：与 conversationsByType[sidebarConversationType] 同一数组引用 */
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
  /** 双类型常驻缓存：切换过滤器时 conversations 只换引用，不重请求 */
  conversationsByType: { normal: ConversationSummary[]; multi_agent: ConversationSummary[] };
  /** 各类型分页状态（offset/hasMore）与是否已加载过，切换时与扁平字段交换 */
  pagingByType: {
    normal: { offset: number; hasMore: boolean; loaded: boolean };
    multi_agent: { offset: number; hasMore: boolean; loaded: boolean };
  };
}

/** 新建空分组时的双类型缓存初始化 */
export const createEmptyGroupTypeCache = () => ({
  conversationsByType: { normal: [], multi_agent: [] } as {
    normal: ConversationSummary[];
    multi_agent: ConversationSummary[];
  },
  pagingByType: {
    normal: { offset: 0, hasMore: false, loaded: false },
    multi_agent: { offset: 0, hasMore: false, loaded: false }
  }
});

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
  /** 当前打开对话是否为多智能体（由 enterConversation/空对话 watcher 写入，subAgent store 读取） */
  multiAgentMode: boolean;
  /** 侧边栏对话类型过滤器：'normal' 普通对话 | 'multi_agent' 多智能体对话（localStorage 持久化） */
  sidebarConversationType: 'normal' | 'multi_agent';
  /** 双类型列表缓存：conversations 始终是 conversationsCache[sidebarConversationType].list
      的同一数组引用——原地增删（push/splice/unshift）天然同步；重新赋值 conversations 后
      必须同步刷新缓存引用。切换过滤器为纯本地引用交换，零请求零加载态。
      loaded 区分「未加载过」与「加载过但为空」。 */
  conversationsCache: {
    normal: { list: ConversationSummary[]; offset: number; hasMore: boolean; loaded: boolean };
    multi_agent: { list: ConversationSummary[]; offset: number; hasMore: boolean; loaded: boolean };
  };
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
    multiAgentMode: false,
    sidebarConversationType: loadSidebarConversationType(),
    conversationsCache: {
      normal: { list: [], offset: 0, hasMore: false, loaded: false },
      multi_agent: { list: [], offset: 0, hasMore: false, loaded: false }
    }
  }),
  actions: {
    /** 当前对话 id 同步（由 app watcher 在 this.currentConversationId 变化时写入） */
    setCurrentConversationId(id: string | null) {
      this.currentConversationId = id;
    },
    /**
     * 侧边栏对话类型过滤器切换（左右切换控件写入）。
     * 纯本地引用交换：当前分页状态存回缓存 → conversations 指向目标类型缓存数组
     * → 恢复目标分页状态；工作区分组同步换引用。零请求，配合 pane 平移动画即时呈现。
     */
    setSidebarConversationType(type: 'normal' | 'multi_agent') {
      const normalized = type === 'multi_agent' ? 'multi_agent' : 'normal';
      if (this.sidebarConversationType === normalized) return;
      const oldType = this.sidebarConversationType;
      const curCache = this.conversationsCache[oldType];
      curCache.offset = this.conversationsOffset;
      curCache.hasMore = this.hasMoreConversations;
      this.sidebarConversationType = normalized;
      persistSidebarConversationType(normalized);
      const nextCache = this.conversationsCache[normalized];
      this.conversations = nextCache.list;
      this.conversationsOffset = nextCache.offset;
      this.hasMoreConversations = nextCache.hasMore;
      this.loadingMoreConversations = false;
      for (const group of this.workspaceGroups) {
        if (!group || !group.conversationsByType) continue;
        const curPaging = group.pagingByType[oldType];
        curPaging.offset = group.offset;
        curPaging.hasMore = group.hasMore;
        group.conversations = group.conversationsByType[normalized];
        group.offset = group.pagingByType[normalized].offset;
        group.hasMore = group.pagingByType[normalized].hasMore;
        group.loadingMore = false;
      }
    },
    resetConversations() {
      /* 重置双类型缓存并对齐 conversations 引用（核心不变量） */
      this.resetConversationsCacheOnly();
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
      this.conversationsCache = {
        normal: { list: [], offset: 0, hasMore: false, loaded: false },
        multi_agent: { list: [], offset: 0, hasMore: false, loaded: false }
      };
      this.conversations = this.conversationsCache[this.sidebarConversationType].list;
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
        /* 原地替换保持数组引用：group.conversations 与 conversationsByType[当前类型] 是同一引用 */
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
        const typeCache = createEmptyGroupTypeCache();
        this.workspaceGroups.push({
          workspaceId,
          conversations: typeCache.conversationsByType[this.sidebarConversationType],
          ...typeCache,
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
    async loadWorkspaceConversations(
      workspaceId: string,
      { reset = false, refresh = false } = {}
    ) {
      if (!workspaceId) return;
      let index = this.workspaceGroups.findIndex((g) => g.workspaceId === workspaceId);
      if (index === -1) {
        this.ensureWorkspaceGroup(workspaceId);
        index = this.workspaceGroups.length - 1;
      }
      const group = this.workspaceGroups[index];
      /* 锁定发起时类型：响应期间用户可能已切换过滤器，数据始终写入对应类型缓存 */
      const listType = this.sidebarConversationType;
      const listCache = group.conversationsByType[listType];
      const paging = group.pagingByType[listType];
      if (reset) {
        listCache.length = 0;
        paging.offset = 0;
        paging.hasMore = false;
        paging.loaded = false;
        group.visibleOffset = 0;
        if (listType === this.sidebarConversationType) {
          group.offset = 0;
          group.hasMore = false;
        }
      }
      if (group.loading || group.loadingMore) return;
      const fetchOffset = refresh ? 0 : paging.offset;
      group.loading = true;
      try {
        // 列表过滤由请求发起时的侧边栏类型过滤器（普通/多智能体）决定
        const maParam = listType === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
        const response = await fetch(
          `/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}&limit=${group.fetchLimit}&offset=${fetchOffset}${maParam}`
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
          /* 仍是当前显示类型时同步扁平字段（group.conversations 引用已是该缓存） */
          if (listType === this.sidebarConversationType) {
            group.hasMore = paging.hasMore;
            group.offset = paging.offset;
          }
          /* 首页加载成功后后台补载另一类型，保证切换过滤器时零等待 */
          const otherType = listType === 'multi_agent' ? 'normal' : 'multi_agent';
          if (fetchOffset === 0 && !group.pagingByType[otherType].loaded) {
            this.loadWorkspaceConversationTypeCache(workspaceId, otherType).catch(() => {});
          }
        } else {
          console.error('加载工作区对话失败:', data.error);
        }
      } catch (error) {
        console.error('加载工作区对话异常:', error);
      } finally {
        group.loading = false;
      }
    },
    /** 后台补载某工作区指定类型的首页缓存：已加载则跳过，不干扰当前显示 */
    async loadWorkspaceConversationTypeCache(
      workspaceId: string,
      type: 'normal' | 'multi_agent'
    ) {
      const group = this.workspaceGroups.find((g) => g.workspaceId === workspaceId);
      if (!group || group.pagingByType[type].loaded) return;
      const maParam = type === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
      try {
        const response = await fetch(
          `/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}&limit=${group.fetchLimit}&offset=0${maParam}`
        );
        const data = await response.json();
        /* 响应时重新校验：可能已被 reset/补载并发填充 */
        if (!data?.success || group.pagingByType[type].loaded) return;
        const items = (data.data?.conversations || []).map((conv: any) => ({
          id: conv.id,
          title: conv.title,
          updated_at: conv.updated_at,
          total_messages: conv.total_messages,
          total_tools: conv.total_tools
        }));
        group.conversationsByType[type].push(...items);
        group.pagingByType[type].offset = items.length;
        group.pagingByType[type].hasMore = !!data.data?.has_more;
        group.pagingByType[type].loaded = true;
      } catch (error) {
        console.error('补载工作区对话缓存异常:', error);
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
        const listType = this.sidebarConversationType;
        try {
          const fetchOffset = group.conversations.length;
          const maParam = listType === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
          const response = await fetch(
            `/api/conversations?workspace_id=${encodeURIComponent(workspaceId)}&limit=${group.bufferLimit}&offset=${fetchOffset}${maParam}`
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
            /* 同步该类型分页缓存（group.conversations 与该缓存同一引用，原地 push 已同步） */
            const paging = group.pagingByType?.[listType];
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

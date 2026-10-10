// @ts-nocheck
import { debugLog } from '../common';
import './shared';

export const loadMethods = {
  /**
   * 侧边栏对话类型（普通/多智能体）切换：
   * 重置平铺列表分页并重新加载，同时刷新所有已展开的工作区分组。
   * 由 ConversationSidebar 的 conversation-type-change 事件触发；
   * store 中的 sidebarConversationType 已由组件先行写入。
   */
  /**
   * 侧边栏对话类型（普通/多智能体）切换后的补载：
   * 列表引用已由 store setSidebarConversationType 同步交换（零请求切换），
   * 此处仅兜底「目标类型从未加载过」的场景（如登录后首次直接切换），
   * 按需补载当前类型的主列表与工作区分组首页。
   */
  async handleSidebarConversationTypeChange(_type?: 'normal' | 'multi_agent') {
    // _type 为组件事件载荷的签名占位：目标类型以 store 已写入的 sidebarConversationType 为准
    void _type;
    const { useConversationStore } = await import('../../../stores/conversation');
    const conversationStore = useConversationStore();
    const listType = conversationStore.sidebarConversationType;
    if (!conversationStore.conversationsCache[listType]?.loaded) {
      this.conversationsOffset = 0;
      this.hasMoreConversations = false;
      await this.loadConversationsList();
    }
    try {
      const groups = Array.isArray(conversationStore.workspaceGroups)
        ? conversationStore.workspaceGroups
        : [];
      for (const group of groups) {
        if (group && group.workspaceId && !group.pagingByType?.[listType]?.loaded) {
          await conversationStore.loadWorkspaceConversations(group.workspaceId);
        }
      }
    } catch (error) {
      console.error('补载工作区分组对话失败:', error);
    }
  },

  async loadConversationsList() {
    const queryOffset = this.conversationsOffset;
    const queryLimit = this.conversationsLimit;
    const { useConversationStore } = await import('../../../stores/conversation');
    const conversationStore = useConversationStore();
    /* 锁定发起时类型：响应期间用户可能已切换过滤器，数据始终写入对应类型缓存 */
    const listType = conversationStore.sidebarConversationType;
    const refreshToken =
      queryOffset === 0 ? ++this.conversationListRefreshToken : this.conversationListRefreshToken;
    const requestSeq = ++this.conversationListRequestSeq;
    this.conversationsLoading = true;
    try {
      // 列表过滤由请求发起时的侧边栏类型过滤器（普通/多智能体）决定
      const maParam = listType === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
      const response = await fetch(
        `/api/conversations?limit=${queryLimit}&offset=${queryOffset}${maParam}`
      );
      const data = await response.json();

      if (data.success) {
        if (refreshToken < this.conversationListRefreshToken) {
          debugLog('忽略已过期的对话列表响应', {
            requestSeq,
            responseOffset: queryOffset,
            refreshToken,
            currentRefreshToken: this.conversationListRefreshToken
          });
          return;
        }

        /* 原地写入该类型缓存（当前显示类型的缓存与 conversations 同一引用，原地修改即同步显示） */
        const cache = conversationStore.conversationsCache[listType];
        const items = data.data.conversations;
        if (queryOffset === 0) {
          cache.list.splice(0, cache.list.length, ...items);
        } else {
          cache.list.push(...items);
        }
        cache.offset = queryOffset;
        cache.hasMore = data.data.has_more;
        cache.loaded = true;

        /* 仅仍是当前显示类型时同步扁平字段与首对话自动加载 */
        if (listType === conversationStore.sidebarConversationType) {
          this.conversations = cache.list;
          this.hasMoreConversations = cache.hasMore;
          if (this.currentConversationId) {
            this.promoteConversationToTop(this.currentConversationId);
          }
          if (
            queryOffset === 0 &&
            !this.currentConversationId &&
            cache.list.length > 0 &&
            !this.isExplicitNewConversationRoute() &&
            !this.isConversationIndependentRoute()
          ) {
            // 只有在初始化完成后，才自动加载第一个对话
            // 避免与 bootstrapRoute 冲突
            if (this.initialRouteResolved) {
              const latestConversation = cache.list[0];
              if (latestConversation && latestConversation.id) {
                await this.handleSelectWorkspaceConversation({
                  conversationId: String(latestConversation.id || ''),
                  workspaceId: String(this.currentHostWorkspaceId || '')
                });
              }
            }
          }
        }
        debugLog(`已加载 ${cache.list.length} 个对话`);

        /* 首页加载成功后后台补载另一类型，保证切换过滤器时零等待 */
        const otherType = listType === 'multi_agent' ? 'normal' : 'multi_agent';
        if (queryOffset === 0 && !conversationStore.conversationsCache[otherType].loaded) {
          this.loadConversationTypeCache(otherType).catch(() => {});
        }
      } else {
        console.error('加载对话列表失败:', data.error);
      }
    } catch (error) {
      console.error('加载对话列表异常:', error);
    } finally {
      if (refreshToken === this.conversationListRefreshToken) {
        this.conversationsLoading = false;
      }
    }
  },

  /** 后台补载指定类型的首页列表缓存：已加载则跳过；refreshToken 快照防 reset/刷新后旧响应污染 */
  async loadConversationTypeCache(type: 'normal' | 'multi_agent') {
    const { useConversationStore } = await import('../../../stores/conversation');
    const conversationStore = useConversationStore();
    const cache = conversationStore.conversationsCache[type];
    if (!cache || cache.loaded) return;
    const tokenAtStart = this.conversationListRefreshToken;
    const maParam = type === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
    try {
      const response = await fetch(
        `/api/conversations?limit=${this.conversationsLimit}&offset=0${maParam}`
      );
      const data = await response.json();
      if (!data?.success) return;
      /* reset/刷新后旧响应丢弃；并发补载去重 */
      if (tokenAtStart !== this.conversationListRefreshToken || cache.loaded) return;
      const items = data.data?.conversations || [];
      cache.list.push(...items);
      cache.offset = 0;
      cache.hasMore = !!data.data?.has_more;
      cache.loaded = true;
    } catch (error) {
      console.error('补载对话列表缓存异常:', error);
    }
  },
  async loadMoreConversations() {
    if (this.loadingMoreConversations || !this.hasMoreConversations) return;

    this.loadingMoreConversations = true;
    this.conversationsOffset += this.conversationsLimit;
    await this.loadConversationsList();
    this.loadingMoreConversations = false;
  }
  /* loadConversation 已删除（2026-10-10）：全部并入统一入口
     handleSelectWorkspaceConversation（app/methods/ui/hostWorkspace.ts）。
     平铺/分组/移动版/标签条/路由恢复/创建/复制/还原/运行任务跳转统一走该入口。 */
};

/* 工作区分组加载统一走 store 版（stores/conversation.ts 的 loadWorkspaceConversations/
   loadMoreWorkspaceConversations/loadWorkspaceConversationTypeCache），双类型缓存已在那里适配；
   此处不再保留重复实现 */

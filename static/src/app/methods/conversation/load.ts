// @ts-nocheck
import { useConversationStore } from '../../../stores/conversation';
import { fetchConversationPage } from '../../../stores/workspaceConversationCache';

export const loadMethods = {
  async handleSidebarConversationTypeChange() {
    const store = useConversationStore();
    const type = store.sidebarConversationType;
    if (!store.conversationsCache[type].loaded) {
      this.conversationsOffset = 0;
      await this.loadConversationsList();
    }
    for (const group of store.workspaceGroups) {
      if (group.expanded && !group.pagingByType[type].loaded) {
        void store.loadWorkspaceConversations(group.workspaceId);
      }
    }
  },
  async loadConversationsList(options = {}) {
    const store = useConversationStore();
    const workspaceId = String(this.currentHostWorkspaceId || 'default');
    store.activateWorkspaceCache(workspaceId);
    const type = store.sidebarConversationType;
    const cache = store.getWorkspaceCache(workspaceId)[type];
    const offset = options.background ? 0 : Number(this.conversationsOffset || 0);
    const owns = () =>
      store.conversationCacheWorkspaceId === workspaceId &&
      store.sidebarConversationType === type &&
      store.conversationsCache[type] === cache;
    if (owns() && !cache.loaded) this.conversationsLoading = true;
    try {
      await fetchConversationPage(store, workspaceId, type, {
        limit: options.background
          ? Math.max(this.conversationsLimit, cache.list.length)
          : this.conversationsLimit,
        offset,
        refresh: true
      });
      if (!owns()) return;
      this.conversations = cache.list;
      this.hasMoreConversations = cache.hasMore;
      this.conversationsOffset = cache.offset;
      if (this.currentConversationId) this.promoteConversationToTop(this.currentConversationId);
      if (
        options.autoEnter !== false &&
        !options.background &&
        offset === 0 &&
        !this.currentConversationId &&
        cache.list.length &&
        !this.isExplicitNewConversationRoute() &&
        !this.isConversationIndependentRoute() &&
        this.initialRouteResolved
      ) {
        await this.handleSelectWorkspaceConversation({
          conversationId: cache.list[0].id,
          workspaceId
        });
      }
      const other = type === 'multi_agent' ? 'normal' : 'multi_agent';
      if (offset === 0 && !store.getWorkspaceCache(workspaceId)[other].loaded) {
        void this.loadConversationTypeCache(other, workspaceId);
      }
    } catch (error) {
      console.error('Conversation list unavailable:', error);
    } finally {
      if (owns()) this.conversationsLoading = false;
    }
  },
  async loadConversationTypeCache(
    type,
    workspaceId = String(this.currentHostWorkspaceId || 'default')
  ) {
    const store = useConversationStore();
    try {
      await fetchConversationPage(store, workspaceId, type, { limit: this.conversationsLimit });
    } catch (error) {
      console.error('Conversation type cache unavailable:', error);
    }
  },
  async loadMoreConversations() {
    if (this.loadingMoreConversations || !this.hasMoreConversations) return;
    const store = useConversationStore();
    const workspaceId = store.conversationCacheWorkspaceId;
    const type = store.sidebarConversationType;
    const cache = store.conversationsCache[type];
    const owns = () =>
      store.conversationCacheWorkspaceId === workspaceId && store.sidebarConversationType === type;
    this.loadingMoreConversations = true;
    const previous = this.conversationsOffset;
    this.conversationsOffset += this.conversationsLimit;
    await this.loadConversationsList();
    if (owns()) {
      if (cache.offset === previous) this.conversationsOffset = previous;
      this.loadingMoreConversations = false;
    }
  }
};

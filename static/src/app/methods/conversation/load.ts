// @ts-nocheck
import { useConversationStore } from '../../../stores/conversation';
import { fetchConversationPage } from '../../../stores/workspaceConversationCache';

export const loadMethods = {
  async loadConversationsList(options = {}) {
    const store = useConversationStore();
    const workspaceId = String(this.currentHostWorkspaceId || 'default');
    store.activateWorkspaceCache(workspaceId);
    const cache = store.getWorkspaceCache(workspaceId);
    const offset = options.background ? 0 : Number(this.conversationsOffset || 0);
    const owns = () =>
      store.conversationCacheWorkspaceId === workspaceId && store.conversationsCache === cache;
    if (owns() && !cache.loaded) this.conversationsLoading = true;
    try {
      await fetchConversationPage(store, workspaceId, {
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
    } catch (error) {
      console.error('Conversation list unavailable:', error);
    } finally {
      if (owns()) this.conversationsLoading = false;
    }
  },
  async loadMoreConversations() {
    if (this.loadingMoreConversations || !this.hasMoreConversations) return;
    const store = useConversationStore();
    const workspaceId = store.conversationCacheWorkspaceId;
    const cache = store.conversationsCache;
    const owns = () =>
      store.conversationCacheWorkspaceId === workspaceId && store.conversationsCache === cache;
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

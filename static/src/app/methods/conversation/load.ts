// @ts-nocheck
import { useConversationStore } from '../../../stores/conversation';
import { fetchConversationPage } from '../../../stores/workspaceConversationCache';
import { hasDesktopSidebar } from '@/components/chrome/desktopSidebarBridge';
import { useConversationStorageMigrationStore } from '@/stores/conversationStorageMigration';
import { ownsConversationSession } from './session';
import { refreshNavigationAuxiliary } from '../ui/navigationRefresh';
import { t } from '@/locales';

export const loadMethods = {
  async refreshAfterConversationMigration() {
    const store = useConversationStore();
    if (!hasDesktopSidebar()) {
      void this.loadConversationsList({ background: true, silent: true, autoEnter: false });
      for (const group of store.workspaceGroups) {
        if (group.expanded)
          void store.loadWorkspaceConversations(group.workspaceId, { refresh: true });
      }
    }
    const blocked = useConversationStorageMigrationStore().blockedAgent;
    if (!blocked || this.historyLoading) return;
    const { id, workspaceId } = blocked;
    const session = this.beginConversationView(id, workspaceId);
    try {
      const result = await this.enterConversation(id, {
        session,
        workspaceId,
        urlMode: 'none',
        source: 'migration',
        preserveListPosition: true,
        navigationReady: Promise.all([{}, this.prepareComposerDraftState(workspaceId, id)])
      });
      if (!ownsConversationSession(this, session)) return;
      if (result.success) refreshNavigationAuxiliary(this, session, result.conversation_id || id);
      else if (!result.migrationRequired && !result.superseded) throw new Error(result.error || '');
    } catch (error) {
      if (!ownsConversationSession(this, session)) return;
      this.uiPushToast({
        title: t('appMessages.loadConversationErrorTitle'),
        message: String(error?.message || error),
        type: 'error'
      });
    }
  },
  async loadConversationsList(options = {}) {
    if (hasDesktopSidebar()) return;
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
      // 静默刷新（定时任务等外部来源）不重排列表，避免用户没操作时顺序乱跳。
      if (this.currentConversationId && !options.silent) {
        this.promoteConversationToTop(this.currentConversationId);
      }
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

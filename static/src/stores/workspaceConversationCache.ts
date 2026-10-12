import type { ConversationSummary } from './conversation';
import { workspaceScopedFetch as fetch } from '../app/methods/workspaceRequestScope';

export type ConversationListCache = {
  list: ConversationSummary[];
  offset: number;
  hasMore: boolean;
  loaded: boolean;
};
export type WorkspaceListCache = Record<'normal' | 'multi_agent', ConversationListCache>;
export const emptyWorkspaceCache = (): WorkspaceListCache => ({
  normal: { list: [], offset: 0, hasMore: false, loaded: false },
  multi_agent: { list: [], offset: 0, hasMore: false, loaded: false }
});

const requests = new WeakMap<
  ConversationListCache,
  { owner: object; key: string; promise: Promise<void> }
>();

export const workspaceCacheActions = {
  getWorkspaceCache(this: any, workspaceId: string, filter = 'default'): WorkspaceListCache {
    const key = JSON.stringify([workspaceId || 'default', filter]);
    if (!this.conversationsByWorkspace[key])
      this.conversationsByWorkspace[key] = emptyWorkspaceCache();
    return this.conversationsByWorkspace[key];
  },
  activateWorkspaceCache(this: any, workspaceId: string) {
    const id = workspaceId || 'default';
    if (this.conversationCacheWorkspaceId === id) return;
    if (this.conversationCacheWorkspaceId) {
      const old = this.conversationsCache[this.sidebarConversationType];
      old.offset = this.conversationsOffset;
      old.hasMore = this.hasMoreConversations;
    }
    const cache = this.getWorkspaceCache(id);
    this.conversationCacheWorkspaceId = id;
    this.conversationsCache = cache;
    const visible = cache[this.sidebarConversationType];
    this.conversations = visible.list;
    this.conversationsOffset = visible.offset;
    this.hasMoreConversations = visible.hasMore;
    this.conversationsLoading = !visible.loaded;
    this.loadingMoreConversations = false;
  },
  invalidateWorkspaceCache(this: any, workspaceId: string) {
    const id = workspaceId || 'default';
    for (const [key, cache] of Object.entries(this.conversationsByWorkspace) as [
      string,
      WorkspaceListCache
    ][]) {
      if (JSON.parse(key)[0] !== id) continue;
      for (const value of Object.values(cache)) requests.delete(value);
      delete this.conversationsByWorkspace[key];
    }
    if (this.conversationCacheWorkspaceId === id) {
      this.conversationCacheWorkspaceId = '';
      this.activateWorkspaceCache(id);
    }
  }
};

export function fetchConversationPage(
  store: any,
  workspaceId: string,
  type: 'normal' | 'multi_agent',
  { limit = 20, offset = 0, refresh = false, nonEmpty = false } = {}
) {
  const pair: WorkspaceListCache = store.getWorkspaceCache(
    workspaceId,
    nonEmpty ? 'non-empty' : 'default'
  );
  const cache = pair[type];
  if (!refresh && cache.loaded && offset === 0) return Promise.resolve();
  const key = JSON.stringify([limit, offset, nonEmpty]);
  const current = requests.get(cache);
  if (current?.key === key) return current.promise;
  const owner = {};
  const pending = { owner, key, promise: Promise.resolve() };
  requests.set(cache, pending);
  pending.promise = (async () => {
    try {
      const query = new URLSearchParams({
        workspace_id: workspaceId || 'default',
        limit: String(limit),
        offset: String(offset),
        multi_agent_mode: type === 'multi_agent' ? '1' : '0',
        non_empty: nonEmpty ? '1' : '0'
      });
      const response = await fetch(`/api/conversations?${query}`);
      const result = await response.json();
      if (requests.get(cache)?.owner !== owner) return;
      if (!response.ok || !result.success)
        throw new Error(result.error || 'Conversation list unavailable');
      const items: ConversationSummary[] = result.data?.conversations || [];
      if (offset === 0) cache.list.splice(0, cache.list.length, ...items);
      else {
        const ids = new Set(cache.list.map((item) => item.id));
        cache.list.push(...items.filter((item) => !ids.has(item.id)));
      }
      cache.loaded = true;
      cache.offset = offset;
      cache.hasMore = !!result.data?.has_more;
    } finally {
      if (requests.get(cache)?.owner === owner) requests.delete(cache);
    }
  })();
  return pending.promise;
}

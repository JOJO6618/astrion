import type { ConversationSummary } from './conversation';
import { workspaceScopedFetch as fetch } from '../app/methods/workspaceRequestScope';

export type WorkspaceListCache = {
  list: ConversationSummary[];
  offset: number;
  hasMore: boolean;
  loaded: boolean;
};
export const emptyWorkspaceCache = (): WorkspaceListCache => ({
  list: [],
  offset: 0,
  hasMore: false,
  loaded: false
});

const requests = new WeakMap<
  WorkspaceListCache,
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
      this.conversationsCache.offset = this.conversationsOffset;
      this.conversationsCache.hasMore = this.hasMoreConversations;
    }
    const cache = this.getWorkspaceCache(id);
    this.conversationCacheWorkspaceId = id;
    this.conversationsCache = cache;
    this.conversations = cache.list;
    this.conversationsOffset = cache.offset;
    this.hasMoreConversations = cache.hasMore;
    this.conversationsLoading = !cache.loaded;
    this.loadingMoreConversations = false;
  },
  invalidateWorkspaceCache(this: any, workspaceId: string) {
    const id = workspaceId || 'default';
    for (const [key, cache] of Object.entries(this.conversationsByWorkspace) as [
      string,
      WorkspaceListCache
    ][]) {
      if (JSON.parse(key)[0] !== id) continue;
      requests.delete(cache);
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
  { limit = 20, offset = 0, refresh = false, nonEmpty = false } = {}
) {
  const cache: WorkspaceListCache = store.getWorkspaceCache(
    workspaceId,
    nonEmpty ? 'non-empty' : 'default'
  );
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

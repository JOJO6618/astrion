// @ts-nocheck
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { useConversationTabsStore } from '../../../stores/conversationTabs';

const selections = new WeakMap<object, { tail: Promise<any>; selected: string }>();

/** Selection cookies must complete in order, including superseded responses. */
export function selectWorkspaceIdentity(host, workspaceId, owns) {
  // 桌面多视图：每个视图按 URL 的显式工作区＋请求头自带身份，不读写 session 选中工作区，
  // 否则多个（含隐藏）视图会互相覆盖同一份 session 选择。
  if (useConversationTabsStore().enabled)
    return Promise.resolve({ current_workspace_id: workspaceId });
  let state = selections.get(host);
  if (!state) {
    state = { tail: Promise.resolve(), selected: String(host.currentHostWorkspaceId || '') };
    selections.set(host, state);
  }
  const current = state;
  const selection = current.tail
    .catch(() => {})
    .then(async () => {
      if (!owns()) return { superseded: true };
      if (current.selected === workspaceId) return { current_workspace_id: workspaceId };
      const endpoint = '/api/host/workspaces/select';
      // Deliberately don't abort a selection write: the next write waits for its
      // response/cookie before selecting the last still-owned navigation target.
      // Even an unreadable response may have changed its cookie on the server.
      current.selected = '';
      const response = await fetch(`${endpoint}?workspace_id=${encodeURIComponent(workspaceId)}`, {
        method: 'POST',
        headers: { 'X-Astrion-Workspace-Id': workspaceId }
      });
      const result = await response.json();
      if (!response.ok || !result.success)
        throw new Error(result.error || 'Workspace selection failed');
      current.selected = String(result.data?.current_workspace_id || workspaceId);
      return result.data || {};
    });
  current.tail = selection;
  return selection;
}

import type { ConversationTab } from '@/stores/conversationTabs';
import type { BrowserScope } from './types';

interface BrowserNavigationState {
  hydrated: boolean;
  /** 本视图已 attach 且持有标签写权。 */
  ownsView: boolean;
  viewTab: ConversationTab | null;
}

export function browserConversationPage(pathname: string) {
  return !/^\/(?:settings|git|workflows?|workflow-demo)(?:\/|$)/.test(pathname);
}

/** 桌面多视图：浏览器作用域 = 本视图固定绑定的对话/空白标签（独立页面标签无浏览器）。 */
export function browserScopeForNavigation(
  state: BrowserNavigationState,
  pathname: string
): BrowserScope | null {
  const tab = state.viewTab;
  if (!state.hydrated || !state.ownsView || !browserConversationPage(pathname)) return null;
  if (!tab || tab.kind === 'page') return null;
  // The conversation id owns browser tabs so the model's tabs follow the conversation;
  // /new tabs use the stable key until the first message creates the conversation.
  return {
    mode: tab.conversationKind,
    workspace: tab.workspaceId,
    conversation: tab.conversationId || tab.key,
    key: tab.key
  };
}

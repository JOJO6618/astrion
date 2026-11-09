import type { ConversationTab, DesktopNavigation } from '@/stores/conversationTabs';
import type { BrowserScope } from './types';

interface BrowserNavigationState {
  hydrated: boolean;
  attached: boolean;
  rendererId: string;
  localRendererId: string;
  activeKey: string;
  activeTab: ConversationTab | null;
  tabs: ConversationTab[];
  navigation: DesktopNavigation | null;
}

export function browserConversationPage(pathname: string, navigation: DesktopNavigation | null) {
  return (
    !navigation?.page && !/^\/(?:settings|git|workflows?|workflow-demo)(?:\/|$)/.test(pathname)
  );
}

export function browserScopeForNavigation(
  state: BrowserNavigationState,
  pathname: string,
  search = ''
): BrowserScope | null {
  if (
    !state.hydrated ||
    !state.attached ||
    !state.rendererId ||
    state.rendererId !== state.localRendererId ||
    !browserConversationPage(pathname, state.navigation) ||
    state.navigation?.status === 'pending'
  )
    return null;

  let tab = state.activeTab;
  if (tab) {
    if (state.navigation && state.navigation.status !== 'committed') return null;
  } else {
    // A new Agent view can fail before commit when no model is configured.
    // Manual browsing may use that explicit existing target; never commit it,
    // fabricate a desktop tab, or borrow a previous renderer's ownership.
    if (
      state.activeKey ||
      state.navigation?.status !== 'error' ||
      !/^\/(?:new\/?|chat\/new\/?)?$/.test(pathname)
    )
      return null;
    const target = state.tabs.find((item) => item.key === state.navigation?.key);
    const mode = pathname.startsWith('/chat/') ? 'chat' : 'agent';
    const workspace = new URLSearchParams(search).get('workspace_id');
    if (
      !target ||
      target.kind !== 'new' ||
      target.conversationKind !== mode ||
      (mode === 'agent' && workspace !== null && workspace !== target.workspaceId)
    )
      return null;
    tab = target;
  }
  // The conversation id owns browser tabs so the model's tabs follow the conversation;
  // /new tabs use the stable key until the first message creates the conversation.
  return {
    mode: tab.conversationKind,
    workspace: tab.workspaceId,
    conversation: tab.conversationId || tab.key,
    key: tab.key
  };
}

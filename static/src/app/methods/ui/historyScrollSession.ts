interface HistoryScrollState {
  followState: 'locked' | 'escaped';
  scrollEscapeVersion: number;
}

interface HistoryScrollController {
  getStickState(): HistoryScrollState;
  scrollToBottom(options: { behavior: 'auto'; force?: boolean }): unknown;
}

interface HistoryScrollHost {
  currentConversationId: string | null;
  getChatAreaController(): HistoryScrollController | null;
  getMessagesAreaElement(): HTMLElement | null;
  chatSetScrollState(state: { userScrolling: boolean }): void;
}

const activeSessions = new WeakMap<object, symbol>();

/** 初始回底只强制一次；用户脱锁或导航后，所有延迟写入永久失效。 */
export function createHistoryScrollSession(host: HistoryScrollHost) {
  const controller = host.getChatAreaController();
  const area = host.getMessagesAreaElement();
  if (!controller || !area) return null;
  const conversationId = host.currentConversationId;
  const token = Symbol('history-scroll');
  activeSessions.set(host, token);
  const escapeVersion = controller.getStickState().scrollEscapeVersion;
  let cancelled = false;

  const isActive = () => {
    const state = controller.getStickState();
    cancelled ||=
      activeSessions.get(host) !== token ||
      host.currentConversationId !== conversationId ||
      host.getChatAreaController() !== controller ||
      host.getMessagesAreaElement() !== area ||
      state.scrollEscapeVersion !== escapeVersion ||
      state.followState !== 'locked';
    return !cancelled;
  };

  // 同步完成初始定位，后续 await/rAF 绝不重新强制锁定。
  controller.scrollToBottom({ behavior: 'auto', force: true });
  host.chatSetScrollState({ userScrolling: false });

  return {
    jump() {
      if (!isActive()) return false;
      controller.scrollToBottom({ behavior: 'auto' });
      return true;
    }
  };
}

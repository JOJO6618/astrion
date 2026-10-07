import { useConversationStore } from '../../stores/conversation';
import {
  conversationViewEpoch,
  currentConversationSession,
  ownsConversationSession
} from './conversation/session';

type Host = object & {
  currentConversationId?: string | null;
  currentHostWorkspaceId?: string;
};
const requests = new WeakMap<object, Map<string, number>>();
const viewHosts = new WeakMap<object, Host>();

export function bindAuxiliaryConversationHost(host: Host) {
  viewHosts.set(useConversationStore(), host);
}

export function captureConversationView(host: Host) {
  bindAuxiliaryConversationHost(host);
  const epoch = conversationViewEpoch(host);
  const session = currentConversationSession(host);
  const id = host.currentConversationId || null;
  const workspace = host.currentHostWorkspaceId || '';
  return () =>
    conversationViewEpoch(host) === epoch &&
    currentConversationSession(host) === session &&
    (!session || ownsConversationSession(host, session)) &&
    (host.currentConversationId || null) === id &&
    (host.currentHostWorkspaceId || '') === workspace;
}

export function invalidateAuxiliaryRequest(host: object, key: string) {
  let slots = requests.get(host);
  if (!slots) {
    slots = new Map();
    requests.set(host, slots);
  }
  const generation = (slots.get(key) || 0) + 1;
  slots.set(key, generation);
  return generation;
}

export function beginAuxiliaryRequest(host: Host, key: string) {
  const ownsView = captureConversationView(host);
  const generation = invalidateAuxiliaryRequest(host, key);
  return () => ownsView() && requests.get(host)?.get(key) === generation;
}

export function beginAuxiliaryStoreRequest(
  store: object,
  key: string,
  targetId: string | null = useConversationStore().currentConversationId,
  ownsCaller?: () => boolean
) {
  const conversation = useConversationStore();
  const host = viewHosts.get(conversation);
  const ownsView = host ? captureConversationView(host) : undefined;
  const generation = invalidateAuxiliaryRequest(store, key);
  return () =>
    requests.get(store)?.get(key) === generation &&
    viewHosts.get(conversation) === host &&
    conversation.currentConversationId === targetId &&
    (!ownsView || ownsView()) &&
    (!ownsCaller || ownsCaller());
}

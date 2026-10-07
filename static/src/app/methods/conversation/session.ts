export interface ConversationSession {
  conversationId: string;
  workspaceId: string;
  controller: AbortController;
  generation: number;
}

const sessions = new WeakMap<object, ConversationSession>();
const epochs = new WeakMap<object, number>();
let generation = 0;

export function conversationViewEpoch(host: object) {
  return epochs.get(host) || 0;
}

export function beginConversationSession(
  host: object,
  conversationId: string,
  workspaceId = ''
): ConversationSession {
  sessions.get(host)?.controller.abort();
  const session = {
    conversationId,
    workspaceId,
    controller: new AbortController(),
    generation: ++generation
  };
  sessions.set(host, session);
  epochs.set(host, session.generation);
  return session;
}

export function currentConversationSession(host: object) {
  return sessions.get(host);
}

export function ownsConversationSession(host: object, session: ConversationSession) {
  return sessions.get(host) === session && !session.controller.signal.aborted;
}

export function leaveConversationSession(host: object) {
  sessions.get(host)?.controller.abort();
  sessions.delete(host);
  epochs.set(host, ++generation);
}

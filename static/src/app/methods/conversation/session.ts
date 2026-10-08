export interface ConversationSession {
  conversationId: string;
  workspaceId: string;
  controller: AbortController;
  generation: number;
  submission: object | null;
  submissionVersion: number;
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
    generation: ++generation,
    submission: null as object | null,
    submissionVersion: 0
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

export function isConversationSubmitting(host: object) {
  return !!currentConversationSession(host)?.submission;
}

export function beginConversationSubmission(session: ConversationSession) {
  if (session.submission) return null;
  const submission = {};
  session.submission = submission;
  session.submissionVersion++;
  return submission;
}

export function endConversationSubmission(session: ConversationSession, submission: object) {
  if (session.submission !== submission) return;
  session.submission = null;
  session.submissionVersion++;
}

export function leaveConversationSession(host: object) {
  sessions.get(host)?.controller.abort();
  sessions.delete(host);
  epochs.set(host, ++generation);
}

import type { ChatDocument, ChatMessage } from './types';

export interface Handoff {
  id: string;
  message_id: string;
  status: string;
  reason?: string;
  sequence: number;
  tag_index?: number;
  present?: boolean;
  leaf_id?: string;
  countdown_enabled?: boolean;
  presentation_id?: string;
  deadline?: number | null;
  server_time?: number;
  error?: string;
  agent_conversation_id?: string;
  workspace_id?: string;
  agent_url?: string;
}
export const handledHandoff = (state: Handoff) =>
  ['completed', 'declined', 'superseded'].includes(state.status);

export function visibleHandoffs(document: ChatDocument | null, path: ChatMessage[]): Handoff[] {
  if (!document) return [];
  const ids = new Set(path.map((message) => message.message_id));
  const states = Object.values(document.metadata.chat.handoffs || {}) as Handoff[];
  return states
    .filter(
      (state) =>
        !handledHandoff(state) &&
        state.present !== false &&
        ids.has(state.message_id) &&
        state.leaf_id === document.metadata.chat.latest_leaf_id
    )
    .sort((a, b) => a.sequence - b.sequence);
}

export interface MediaRef {
  media_id: string;
  kind: 'image' | 'video';
  mime_type: string;
  size?: number;
}
export interface ChatMessage {
  message_id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string | Array<{ type: string; text?: string }>;
  reasoning_content?: string;
  thinking_summary?: { text: string; revision: number; generation: string };
  media_refs?: MediaRef[];
  tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>;
  tool_call_id?: string;
  metadata?: Record<string, any> & { chat?: { parent_id: string | null; turn_id: string | null } };
  live?: boolean;
}
export interface ChatTurn {
  id: string;
  parent_id: string;
  tip_id: string | null;
  status: string;
}
export interface ChatDocument {
  id: string;
  title: string;
  messages: ChatMessage[];
  token_statistics?: Record<string, any>;
  metadata: Record<string, any> & {
    chat: {
      revision: number;
      latest_leaf_id: string | null;
      active_turn_id: string | null;
      turns: ChatTurn[];
      handoffs?: Record<string, import('./handoff').Handoff>;
      handoff_count?: number;
      handoff_presentation_id?: string | null;
    };
  };
}
export interface ChatView {
  document: ChatDocument;
  display: {
    messages: Array<{ actions?: Array<Record<string, any>> }>;
    state: Record<string, any>;
    task: { task_id: string; status: string } | null;
    next_event_idx: number;
  };
}
export interface ChatModel {
  key: string;
  name: string;
  supports_thinking: boolean;
  supports_images?: boolean;
  supports_videos?: boolean;
  provider_id?: string;
  provider_name?: string;
  context_window: number;
}
export interface ChatDefaults {
  chat_default_model: string;
  chat_default_thinking_mode: boolean;
  chat_default_reasoning_effort: string | null;
  chat_network_enabled: boolean;
  chat_instructions: string;
  chat_deep_compress_trigger_tokens: number;
}
export interface ChatSettings {
  settings: ChatDefaults;
  models: ChatModel[];
  username: string;
  theme?: string;
  personalization?: Record<string, unknown>;
}
export const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
export const textOf = (message: ChatMessage): string =>
  typeof message.content === 'string'
    ? message.content
    : message.content
        .filter((part) => part.type === 'text')
        .map((part) => part.text || '')
        .join('\n');
export function pathOf(document: ChatDocument, leaf?: string | null): ChatMessage[] {
  const byId = new Map(document.messages.map((message) => [message.message_id, message]));
  let id = leaf === undefined || leaf === null ? document.metadata.chat.latest_leaf_id : leaf;
  const path: ChatMessage[] = [];
  const seen = new Set<string>();
  while (id) {
    if (seen.has(id)) throw new Error('Invalid Chat branch');
    seen.add(id);
    const message = byId.get(id);
    if (!message) throw new Error('Chat branch unavailable');
    path.push(message);
    id = message.metadata?.chat?.parent_id || null;
  }
  return path.reverse();
}

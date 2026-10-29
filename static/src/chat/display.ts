import type { ChatMessage } from './types';
import { textOf } from './types';

function parsed(value: unknown): any {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
const visibleText = (content: string) => content.replace(/<suggest_agent\b[^>]*\/>/gi, '');

function toolAliases(action: any): string[] {
  const tool = action.tool || {};
  return [action.id, tool.id, tool.preparingId, tool.executionId, tool.tool_call_id].filter(
    (id): id is string => typeof id === 'string' && id.length > 0
  );
}

function streamState(actions: any[]) {
  const active = [...actions]
    .reverse()
    .find((action) => action.streaming && (action.type === 'thinking' || action.type === 'text'));
  return {
    currentStreamingType: active?.type || null,
    activeThinkingId: active?.type === 'thinking' ? active.id : null
  };
}

/** Adapt the tree's path without discarding the shared task projection's UI state. */
export function toDisplayMessages(
  messages: ChatMessage[],
  projected: any[] = [],
  identities = new Map<string, string>()
) {
  const identity = (key: string, fallback: string) => {
    const value = identities.get(key) || fallback;
    identities.set(key, value);
    return value;
  };
  const results = new Map(
    messages.filter((m) => m.role === 'tool').map((m) => [m.tool_call_id, m])
  );
  const liveTools = new Map<string, any>();
  const liveContent = new Map<string, any[]>();
  const responseOwners = new Map<string, string>();
  const toolOwners = new Map<string, string>();
  for (const message of projected) {
    for (const action of message.actions || []) {
      if (action.type === 'tool') {
        for (const alias of toolAliases(action)) {
          liveTools.set(alias, action);
          toolOwners.set(alias, message.id);
        }
      }
      if (action.responseMessageId && ['text', 'thinking'].includes(action.type)) {
        responseOwners.set(action.responseMessageId, message.id);
        const items = liveContent.get(action.responseMessageId) || [];
        items.push(action);
        liveContent.set(action.responseMessageId, items);
      }
    }
  }
  const displayed = messages
    .filter((m) => m.role !== 'tool' && m.metadata?.visibility !== 'hidden')
    .map((message) => {
      // Persistence must not replace the projection's component identity: doing
      // so remounts the shared summary and loses the user's expanded state.
      const projectedId =
        responseOwners.get(message.message_id) ||
        (message.tool_calls || []).map((call) => toolOwners.get(call.id)).find(Boolean);
      const base = {
        role: message.role,
        messageId: message.message_id,
        backendMessageId: message.message_id,
        id: identity(`message:${message.message_id}`, projectedId || message.message_id),
        metadata: message.metadata || {},
        raw: message,
        timestamp: (message as any).timestamp,
        created_at: (message as any).created_at || (message as any).timestamp,
        content: textOf(message),
        images: (message.media_refs || []).filter((m) => m.kind === 'image').map((m) => m.media_id),
        videos: (message.media_refs || []).filter((m) => m.kind === 'video').map((m) => m.media_id),
        mediaRefs: message.media_refs || [],
        streaming: !!message.live
      };
      if (message.role === 'user') return { ...base, actions: [] };
      const actions: any[] = [];
      const projectedThinking = liveContent.get(message.message_id)?.find(
        (action) => action.type === 'thinking'
      );
      const thinkingId = identity(
        `thinking:${message.message_id}`,
        projectedThinking?.id || `${message.message_id}:thinking`
      );
      const runtimeContent = message.live && liveContent.get(message.message_id);
      if (runtimeContent) {
        // Preserve start events, show_html boundaries, and actual thinking/text transitions.
        actions.push(
          ...runtimeContent.map((action) => ({
            ...action,
            ...(action === projectedThinking ? { id: thinkingId, blockId: thinkingId } : {}),
            content: action.type === 'text' ? visibleText(action.content || '') : action.content
          }))
        );
      } else {
        if (message.reasoning_content)
          actions.push({
            id: thinkingId,
            type: 'thinking',
            content: message.reasoning_content,
            thinkingSummary: message.thinking_summary?.text || projectedThinking?.thinkingSummary || '',
            thinkingSummaryRevision:
              message.thinking_summary?.revision || projectedThinking?.thinkingSummaryRevision || 0,
            thinkingGeneration: message.thinking_summary?.generation || projectedThinking?.thinkingGeneration,
            streaming: !!message.live,
            blockId: thinkingId,
            responseMessageId: message.message_id
          });
        const content = visibleText(textOf(message)).trim();
        if (content)
          actions.push({
            id: `${message.message_id}:text`,
            type: 'text',
            content,
            streaming: !!message.live,
            responseMessageId: message.message_id
          });
      }
      for (const call of message.tool_calls || []) {
        const result = results.get(call.id);
        const args = parsed(call.function.arguments);
        const projectedTool = liveTools.get(call.id);
        const toolId = identity(`tool:${call.id}`, projectedTool?.id || call.id);
        const resultData = result ? parsed(textOf(result)) : null;
        const failed =
          result?.metadata?.cancelled ||
          resultData?.success === false ||
          ['cancelled', 'failed', 'error'].includes(resultData?.status);
        actions.push(
          !result && projectedTool
            ? { ...projectedTool, id: toolId, blockId: toolId }
            : {
                id: toolId,
                blockId: toolId,
                toolBatchId: projectedTool?.toolBatchId || message.message_id,
                type: 'tool',
                content: '',
                tool: {
                  ...projectedTool?.tool,
                  id: call.id,
                  name: call.function.name,
                  arguments: args,
                  intent_full: args?.intent || '',
                  intent_rendered: args?.intent || '',
                  result: resultData,
                  status: result ? (failed ? 'failed' : 'completed') : 'running'
                },
                streaming: !result
              }
        );
      }
      return {
        ...base,
        actions,
        ...streamState(actions),
        awaitingFirstContent: !!message.live && !actions.length
      };
    });
  const representedMessages = new Set(displayed.map((message) => message.messageId));
  const representedTools = new Set(
    displayed
      .flatMap((message) => message.actions)
      .filter((action) => action.type === 'tool')
      .flatMap(toolAliases)
  );
  for (const message of projected) {
    if (message.role !== 'assistant') continue;
    const actions = (message.actions || [])
      .filter((action: any) => {
        if (action.responseMessageId) return !representedMessages.has(action.responseMessageId);
        if (action.type === 'tool')
          return !toolAliases(action).some((id) => representedTools.has(id));
        return true;
      })
      .map((action: any) => ({
        ...action,
        content: action.type === 'text' ? visibleText(action.content || '') : action.content
      }));
    if (!message.awaitingFirstContent && !actions.length) continue;
    // The server creates this placeholder before any response ID/text exists. Keep it,
    // and preparing tools, just as Agent does; it is a view node, never a tree edit target.
    const id = message.id;
    const raw: ChatMessage = { message_id: id, role: 'assistant', content: '', live: true };
    displayed.push({
      ...message,
      ...streamState(actions),
      actions,
      raw,
      id,
      messageId: id,
      backendMessageId: id,
      content: '',
      metadata: message.metadata || {},
      images: [],
      videos: [],
      mediaRefs: [],
      streaming: true,
      created_at: message.timestamp
    });
  }
  return displayed;
}

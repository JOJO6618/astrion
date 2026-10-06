/**
 * Join consecutive visible assistant messages without modifying conversation history.
 * Hidden user messages have already been removed by the display filter.
 */
export function mergeAssistantDisplayRuns(messages: any[]): any[] {
  const result: any[] = [];
  for (const message of messages) {
    const previous = result[result.length - 1];
    if (message?.role !== 'assistant' || previous?.role !== 'assistant') {
      result.push(message);
      continue;
    }

    const sources = previous.displaySourceMessages || [previous];
    result[result.length - 1] = {
      ...message,
      actions: [...(previous.actions || []), ...(message.actions || [])],
      // Keep source identities for stable virtual-list keys and per-message citations.
      displaySourceMessages: [...sources, message]
    };
  }
  return result;
}

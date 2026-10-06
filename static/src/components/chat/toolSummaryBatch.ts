export interface ToolBatchAction {
  type: string;
  toolBatchId?: string;
}

/** Select the latest model response's tools, including completed members. */
export function getLatestToolBatch<T extends ToolBatchAction>(actions: T[]): T[] {
  let lastToolIndex = -1;
  for (let i = actions.length - 1; i >= 0; i--) {
    if (actions[i].type === 'tool') {
      lastToolIndex = i;
      break;
    }
    if (actions[i].type === 'thinking') break;
  }
  if (lastToolIndex < 0) return [];

  const batchId = actions[lastToolIndex].toolBatchId;
  let startIndex = lastToolIndex;
  while (startIndex > 0 && actions[startIndex - 1].type === 'tool') {
    if (actions[startIndex - 1].toolBatchId !== batchId) break;
    startIndex--;
  }
  return actions.slice(startIndex, lastToolIndex + 1);
}

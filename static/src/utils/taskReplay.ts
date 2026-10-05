export interface TaskReplayBoundary {
  taskId: string;
  conversationId: string;
  lastEventIndex: number;
}

export function isHistoricalTaskEvent(
  boundary: TaskReplayBoundary | null | undefined,
  event: { idx?: number; data?: { task_id?: string; conversation_id?: string } },
  taskId: string | null,
  conversationId: string | null
): boolean {
  return Boolean(
    boundary &&
    boundary.taskId === taskId &&
    boundary.conversationId === conversationId &&
    (!event.data?.task_id || event.data.task_id === boundary.taskId) &&
    (!event.data?.conversation_id || event.data.conversation_id === boundary.conversationId) &&
    typeof event.idx === 'number' &&
    event.idx <= boundary.lastEventIndex
  );
}

// Event handlers run synchronously. Suppression is scoped to one historical
// event, so new events in the same polling batch can still notify immediately.
let notificationSuppressionDepth = 0;
export function withoutReplayNotifications<T>(handle: () => T): T {
  notificationSuppressionDepth++;
  try {
    return handle();
  } finally {
    notificationSuppressionDepth--;
  }
}

export function replayNotificationsSuppressed(): boolean {
  return notificationSuppressionDepth > 0;
}

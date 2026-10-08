// @ts-nocheck
import { t } from '@/locales';
import { useTaskStore } from '../../../stores/task';
import { createTaskPayload } from '../../../stores/taskPolling';
import { currentConversationSession, ownsConversationSession } from '../conversation/session';

export function ensureMessageSession(host: any) {
  const conversationId = host.currentConversationId || '';
  const workspaceId = host.currentHostWorkspaceId || '';
  const current = currentConversationSession(host);
  if (
    current &&
    ownsConversationSession(host, current) &&
    current.conversationId === conversationId &&
    current.workspaceId === workspaceId
  ) {
    return current;
  }
  const session = host.beginConversationView(conversationId, workspaceId);
  host.clearLocalTaskUiState?.('message-session');
  host.historyLoading = false;
  host.historyLoadingFor = null;
  return session;
}

// Creation and its optimistic input share one owner, including late POST results.
export async function createOwnedMessageTask(
  host: any,
  session: any,
  optimisticUser: any,
  message: string,
  images: any[],
  videos: any[],
  conversationId: string,
  options = {},
  optimisticAssistant = host.messages[host.currentMessageIndex]
) {
  const owns = () => ownsConversationSession(host, session);
  if (!owns()) return null;
  const response = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: session.controller.signal,
    body: JSON.stringify(createTaskPayload(message, images, videos, conversationId, options))
  });
  const result = await response.json();
  if (!owns()) return null;
  if (!response.ok || !result.success) {
    throw new Error(result.error || result.message || t('appMessages.createTaskFailedMessage'));
  }
  if (optimisticAssistant?.role === 'assistant') optimisticAssistant.taskId = result.data.task_id;
  let inputBound = false;
  useTaskStore().attachSnapshot(result.data, 0, (event: any) => {
    if (!owns()) return;
    const data = event.data || {};
    const messageId = data.message_id ?? data.metadata?.message_id;
    if (
      !inputBound &&
      optimisticUser &&
      event.type === 'user_message' &&
      data.is_task_input === true &&
      messageId != null
    ) {
      optimisticUser.id = messageId;
      optimisticUser.message_id = messageId;
      inputBound = true;
    }
    host.handleTaskEvent(event);
  });
  return result.data;
}

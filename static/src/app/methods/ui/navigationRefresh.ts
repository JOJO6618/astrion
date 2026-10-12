// @ts-nocheck
import { ownsConversationSession } from '../conversation/session';

/** A committed navigation owns exactly one refresh of auxiliary state. */
export function refreshNavigationAuxiliary(host, session, conversationId) {
  if (!ownsConversationSession(host, session)) return;
  const task = host.getVisibleWorkspaceTaskForConversation?.(conversationId);
  if (task && ['succeeded', 'failed', 'canceled', 'stopped'].includes(String(task.status || ''))) {
    host.acknowledgeCompletedWorkspaceTask?.(task.task_id);
  }
  // The global task index has its own page lifetime and request ownership.
  void host.refreshRunningWorkspaceTasks?.();
  void host.subAgentFetch?.();
  void host.fetchTerminalCount?.();
  void host.fetchConversationTokenStatistics?.();
  void host.fetchVersioningStatus?.(conversationId, { silent: true });
  void host.refreshProjectGitSummary?.();
}

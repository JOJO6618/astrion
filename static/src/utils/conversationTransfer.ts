import { downloadApplicationFile } from '@/utils/downloadFile';

export interface TransferScope {
  chatMode?: boolean;
  workspaceId?: string;
}
export interface TransferDestination {
  conversation_id: string;
  workspace_id: string | null;
}
export class TransferError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message);
  }
}
export function transferUrl(path: string, scope: TransferScope): string {
  const params = new URLSearchParams();
  if (scope.chatMode) params.set('kind', 'chat');
  else if (scope.workspaceId) params.set('workspace_id', scope.workspaceId);
  const query = params.toString();
  return path + (query ? `?${query}` : '');
}
export async function transferRequest<T>(
  path: string,
  scope: TransferScope,
  options: RequestInit = {}
): Promise<T> {
  if (options.method && options.method !== 'GET') await (window as any).ensureCsrfToken?.();
  const response = await fetch(transferUrl(path, scope), options);
  const value = await response.json();
  if (!response.ok || !value.success)
    throw new TransferError(value.error || response.statusText, response.status);
  return value.data as T;
}
export async function exportConversationFile(conversationId: string, scope: TransferScope) {
  const file = await transferRequest<{ url: string; filename: string }>(
    `/api/conversations/${encodeURIComponent(conversationId)}/export`,
    scope,
    { method: 'POST' }
  );
  const outcome = await downloadApplicationFile(file.url, file.filename);
  if (outcome.status === 'failed') throw new Error(outcome.error);
  return outcome;
}

import type { ChatView, MediaRef } from './types';

export class ChatApiError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message);
  }
}
export async function chatApi<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const globals = window as typeof window & { ensureCsrfToken?: () => Promise<unknown> };
  if (options.method && options.method !== 'GET') await globals.ensureCsrfToken?.();
  const headers = new Headers(options.headers);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api/chat${path}`, {
    ...options,
    headers,
    credentials: 'same-origin'
  });
  const value = await response.json();
  if (!response.ok || value.success === false)
    throw new ChatApiError(value.error || response.statusText, response.status);
  return value.data as T;
}
export const command = <T = any>(path: string, data: Record<string, unknown>, method = 'POST') =>
  chatApi<T>(path, { method, body: JSON.stringify(data) });
export const operationId = () => crypto.randomUUID();
export const loadView = (id: string, presentationId?: string) =>
  presentationId
    ? command<ChatView>(`/conversations/${encodeURIComponent(id)}/presentation`, {
        presentation_id: presentationId
      })
    : chatApi<ChatView>(`/conversations/${encodeURIComponent(id)}`);
export const releasePresentation = (id: string, presentationId: string) =>
  chatApi(`/conversations/${encodeURIComponent(id)}/presentation`, {
    method: 'POST',
    keepalive: true,
    body: JSON.stringify({ presentation_id: presentationId, release: true })
  });
export async function uploadMedia(file: File): Promise<MediaRef> {
  const data = new FormData();
  data.append('media', file);
  return chatApi<MediaRef>('/media', { method: 'POST', body: data });
}

// @ts-nocheck
// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { getMessageVisibility, messageStartsWork } from '../../../utils/messageVisibility';

export const debugNotifyLog = (...args: any[]) => {
  void args;
};
export const keyNotifyLog = (...args: any[]) => {
};
export const jsonDebug = (...args: any[]) => {
};
export const userMDebug = (...args: any[]) => {
};
export const RESTORE_DEBUG_PREFIX = '[RESTORE_DEBUG]';
export let restoreDebugCount = 0;
export const RESTORE_DEBUG_MAX = 800;
export const RESTORE_DEBUG_EVENTS = new Set([
  'restore:start',
  'restore:running-task-found',
  'restore:history-empty',
  'restore:task-detail-events',
  'restore:rebuild-decision',
  'restore:rebuild-polling-started',
  'restore:polling-started-follow',
  'restore:thinking-chunk-auto-start',
  'restore:text-chunk-auto-start',
  'restore:error',
  'event:drop-duplicate',
  'event:drop-conversation-mismatch'
]);

export function isRestoreDebugEnabled() {
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__RESTORE_DEBUG__;
    if (explicit === true || explicit === '1') return true;
    if (explicit === false || explicit === '0') return false;
    const localFlag = window.localStorage?.getItem('restoreDebug');
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

export function restoreDebugLog(event: string, payload: Record<string, any> = {}) {
  if (!isRestoreDebugEnabled()) return;
  if (!RESTORE_DEBUG_EVENTS.has(event)) return;
  if (restoreDebugCount >= RESTORE_DEBUG_MAX) return;
  restoreDebugCount += 1;
  if (restoreDebugCount === RESTORE_DEBUG_MAX) {
    console.warn(RESTORE_DEBUG_PREFIX, 'log-limit-reached', { max: RESTORE_DEBUG_MAX });
    return;
  }
  console.log(RESTORE_DEBUG_PREFIX, event, payload);
}

export function isSystemAutoUserMessagePayload(data: any): boolean {
  if (!data || typeof data !== 'object') {
    return false;
  }
  const meta = data.metadata || {};
  return !!(
    data.is_auto_generated ||
    meta.is_auto_generated ||
    data.auto_message_type ||
    meta.auto_message_type ||
    data.sub_agent_notice ||
    meta.sub_agent_notice ||
    data.background_command_notice ||
    meta.background_command_notice ||
    data.runtime_guidance ||
    meta.runtime_guidance ||
    data.runtime_mode_notice ||
    meta.runtime_mode_notice
  );
}

export function isRuntimeModeNoticePayload(data: any): boolean {
  if (!data || typeof data !== 'object') {
    return false;
  }
  const meta = data.metadata || {};
  return !!(data.runtime_mode_notice || meta.runtime_mode_notice);
}

export function resolveUserMessageSource(data: any): string {
  if (!data || typeof data !== 'object') {
    return 'user';
  }
  const meta = data.metadata || {};
  const explicit = String(data.message_source || meta.message_source || '')
    .trim()
    .toLowerCase();
  if (explicit) {
    return explicit;
  }
  if (data.runtime_mode_notice || meta.runtime_mode_notice) {
    return 'notify';
  }
  if (data.runtime_guidance || meta.runtime_guidance) {
    return 'guidance';
  }
  if (data.background_command_notice || meta.background_command_notice) {
    return 'background_command';
  }
  if (data.sub_agent_notice || meta.sub_agent_notice) {
    return 'sub_agent';
  }
  return 'user';
}

export function resolveUserMessageMetadata(data: any, source: string, message: string): Record<string, any> {
  const base = data && typeof data.metadata === 'object' && data.metadata ? { ...data.metadata } : {};
  const metadata: Record<string, any> = {
    ...base,
    message_source: source
  };
  if (data && Object.prototype.hasOwnProperty.call(data, 'visibility')) {
    metadata.visibility = data.visibility;
  }
  if (data && Object.prototype.hasOwnProperty.call(data, 'starts_work')) {
    metadata.starts_work = data.starts_work;
  }
  metadata.visibility = getMessageVisibility({ role: 'user', content: message, metadata, ...data });
  metadata.starts_work = messageStartsWork({ role: 'user', content: message, metadata, ...data });
  return metadata;
}

export function isEmptyAssistantPlaceholderMessage(message: any): boolean {
  if (!message || message.role !== 'assistant') {
    return false;
  }
  const actions = Array.isArray(message.actions) ? message.actions : [];
  return actions.length === 0 && !!message.awaitingFirstContent;
}

export function getOptimisticUserEchoTarget(messages: any[]): any | null {
  if (!Array.isArray(messages) || messages.length === 0) {
    return null;
  }
  const lastIndex = messages.length - 1;
  const last = messages[lastIndex];
  if (last?.role === 'user') {
    return last;
  }
  if (isEmptyAssistantPlaceholderMessage(last) && lastIndex > 0) {
    const prev = messages[lastIndex - 1];
    if (prev?.role === 'user') {
      return prev;
    }
  }
  return null;
}

export function findRecentMatchingUserMessage(messages: any[], message: string, images: any[] = [], videos: any[] = [], source = ''): any | null {
  if (!Array.isArray(messages) || !message) {
    return null;
  }
  const normalizedSource = String(source || '').trim().toLowerCase();
  for (let i = messages.length - 1, seen = 0; i >= 0 && seen < 12; i -= 1) {
    const item = messages[i];
    if (!item || item.role !== 'user') {
      continue;
    }
    seen += 1;
    const itemSource = String(item?.metadata?.message_source || 'user').trim().toLowerCase();
    if (
      String(item.content || '').trim() === message &&
      JSON.stringify(item.images || []) === JSON.stringify(images || []) &&
      JSON.stringify(item.videos || []) === JSON.stringify(videos || []) &&
      (!normalizedSource || itemSource === normalizedSource)
    ) {
      return item;
    }
  }
  return null;
}

/**
 * 任务轮询事件处理器
 * 将从 REST API 轮询获取的事件转换为前端状态更新
 */

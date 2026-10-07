// @ts-nocheck
import { getMessageVisibility, messageStartsWork } from '../../../utils/messageVisibility';

export const debugNotifyLog = (...args: any[]) => {
  void args;
};
export const keyNotifyLog = (...args: any[]) => {
  void args;
};
export const jsonDebug = (...args: any[]) => {
  void args;
};
export const userMDebug = (...args: any[]) => {
  void args;
};
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

export function resolveUserMessageMetadata(
  data: any,
  source: string,
  message: string
): Record<string, any> {
  const base =
    data && typeof data.metadata === 'object' && data.metadata ? { ...data.metadata } : {};
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

/**
 * 任务轮询事件处理器
 * 将从 REST API 轮询获取的事件转换为前端状态更新
 */

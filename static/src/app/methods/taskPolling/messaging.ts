// @ts-nocheck
import { debugLog } from '../common';
import { parseSystemNoticeLabel } from '../ui/shared';
import { displayMessageTime } from '../conversation/display';
import {
  debugNotifyLog,
  keyNotifyLog,
  isSystemAutoUserMessagePayload,
  resolveUserMessageSource,
  resolveUserMessageMetadata,
  isEmptyAssistantPlaceholderMessage
} from './shared';

export const messagingMethods = {
  handleUserMessage(data: any) {
    const message = (data?.message || data?.content || '').trim();
    if (!message) {
      debugNotifyLog('[DEBUG_NOTIFY][ui] handleUserMessage:empty', { data });
      return;
    }
    debugLog('[TaskPolling] 收到用户消息事件');
    debugNotifyLog('[DEBUG_NOTIFY][ui] handleUserMessage', {
      messagePreview: message.slice(0, 120),
      sub_agent_notice: !!data?.sub_agent_notice,
      background_command_notice: !!data?.background_command_notice,
      task_id: data?.task_id,
      has_running_sub_agents: data?.has_running_sub_agents,
      has_running_background_commands: data?.has_running_background_commands
    });
    keyNotifyLog('[DEBUG_NOTIFY_KEY][ui] handleUserMessage', {
      messagePreview: message.slice(0, 80),
      task_id: data?.task_id,
      sub_agent_notice: !!data?.sub_agent_notice,
      background_command_notice: !!data?.background_command_notice
    });
    const isAutoUserMessage = isSystemAutoUserMessagePayload(data);
    const source = resolveUserMessageSource(data);
    const eventMetadata = resolveUserMessageMetadata(data, source, message);
    const incomingImages = Array.isArray(data?.images) ? data.images : [];
    const incomingVideos = Array.isArray(data?.videos) ? data.videos : [];
    const incomingMediaRefs = Array.isArray(data?.media_refs)
      ? data.media_refs
      : Array.isArray(data?.mediaRefs)
        ? data.mediaRefs
        : [];
    const messageId = data?.message_id ?? data?.metadata?.message_id;
    const target =
      messageId == null
        ? null
        : (this.messages || []).find(
            (item: any) =>
              item?.role === 'user' &&
              item.message_id != null &&
              String(item.message_id) === String(messageId)
          );
    const upsert = () => {
      if (!target) this.chatClearStreamingResidualState?.();
      const userMessage =
        target ||
        this.chatAddUserMessage(
          message,
          incomingImages,
          incomingVideos,
          incomingMediaRefs,
          source,
          eventMetadata
        );
      userMessage.content = message;
      userMessage.images = incomingImages;
      userMessage.videos = incomingVideos;
      userMessage.media_refs = incomingMediaRefs;
      if (messageId != null) {
        userMessage.id = messageId;
        userMessage.message_id = messageId;
      }
      userMessage.metadata = {
        ...(userMessage.metadata || {}),
        ...eventMetadata,
        media_refs: incomingMediaRefs,
        message_source: source
      };
      const backendTimestamp = data?.timestamp ?? data?.created_at ?? data?.createdAt;
      if (backendTimestamp != null) userMessage.created_at = displayMessageTime(backendTimestamp);
    };
    if (!isAutoUserMessage) {
      const restorePlaceholder =
        !target && this.moveTrailingEmptyAssistantPlaceholderAfterUserInsert('user_message');
      upsert();
      if (restorePlaceholder) {
        this.chatStartAssistantMessage();
        this.messages[this.currentMessageIndex].taskId = data.task_id;
      }
      this.taskInProgress = true;
      this.streamingMessage =
        isEmptyAssistantPlaceholderMessage(this.messages?.[this.messages.length - 1]) ||
        !!this.streamingMessage;
      this.stopRequested = false;
    } else {
      if (!target && eventMetadata.starts_work === true) this.markLatestUserWorkCompleted();
      const restorePlaceholder =
        !target &&
        this.moveTrailingEmptyAssistantPlaceholderAfterUserInsert('auto_user_message') &&
        (this.taskInProgress || this.streamingMessage);
      upsert();
      if (restorePlaceholder) {
        this.chatStartAssistantMessage();
        this.messages[this.currentMessageIndex].taskId = data.task_id;
        this.taskInProgress = true;
        this.streamingMessage = true;
        this.stopRequested = false;
      }
    }
    if (data?.sub_agent_notice) {
      if (typeof data?.has_running_sub_agents === 'boolean') {
        this.waitingForSubAgent = data.has_running_sub_agents;
      } else if (typeof data?.remaining_count === 'number') {
        this.waitingForSubAgent = data.remaining_count > 0;
      }
      if (typeof data?.has_running_background_commands === 'boolean') {
        this.waitingForBackgroundCommand = data.has_running_background_commands;
      } else if (data?.background_command_notice) {
        this.waitingForBackgroundCommand = this.waitingForSubAgent;
      }
      if (this.waitingForSubAgent) {
        this.startWaitingTaskProbe();
      } else {
        this.stopWaitingTaskProbe();
      }
    }
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  },
  handleSystemMessage(data: any, eventIdx: number) {
    const label = parseSystemNoticeLabel(data?.content || data?.message);
    if (!label) return;
    this.cleanupTrailingEmptyAssistantPlaceholder('before_system_message_append');
    this.chatAddSystemMessage(label, { variant: 'sub_agent_done' });
    const message = this.messages[this.currentMessageIndex];
    message.id = `${data.task_id}:${eventIdx}:assistant`;
    message.taskId = data.task_id;
    const action = message.actions[message.actions.length - 1];
    action.id = `${data.task_id}:${eventIdx}:system`;
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  }
};

// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { getMessageVisibility, messageStartsWork } from '../../../utils/messageVisibility';
import {
  debugNotifyLog,
  keyNotifyLog,
  jsonDebug,
  userMDebug,
  isRestoreDebugEnabled,
  restoreDebugLog,
  isSystemAutoUserMessagePayload,
  isRuntimeModeNoticePayload,
  resolveUserMessageSource,
  resolveUserMessageMetadata,
  isEmptyAssistantPlaceholderMessage,
  getOptimisticUserEchoTarget,
  findRecentMatchingUserMessage,
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
    const isRuntimeModeNotice = isRuntimeModeNoticePayload(data);
    const source = resolveUserMessageSource(data);
    const eventMetadata = resolveUserMessageMetadata(data, source, message);
    const incomingImages = Array.isArray(data?.images) ? data.images : [];
    const incomingVideos = Array.isArray(data?.videos) ? data.videos : [];
    const incomingMediaRefs = Array.isArray(data?.media_refs)
      ? data.media_refs
      : Array.isArray(data?.mediaRefs)
        ? data.mediaRefs
        : [];
    if (!isAutoUserMessage) {
      const last = getOptimisticUserEchoTarget(this.messages || []);
      const recentMatch = findRecentMatchingUserMessage(
        this.messages || [],
        message,
        incomingImages,
        incomingVideos,
        source
      );
      const isLikelyOptimisticEcho = !!(
        last &&
        last.role === 'user' &&
        String(last.content || '').trim() === message &&
        JSON.stringify(last.images || []) === JSON.stringify(incomingImages || []) &&
        JSON.stringify(last.videos || []) === JSON.stringify(incomingVideos || [])
      );
      if (isLikelyOptimisticEcho || recentMatch) {
        const target = isLikelyOptimisticEcho ? last : recentMatch;
        target.media_refs = incomingMediaRefs;
        target.metadata = {
          ...(target.metadata || {}),
          ...eventMetadata,
          media_refs: incomingMediaRefs
        };
        const backendTimestamp = data?.timestamp ?? data?.created_at ?? data?.createdAt ?? null;
        if (backendTimestamp) {
          target.created_at = backendTimestamp;
        }
      } else {
        this.chatAddUserMessage(
          message,
          incomingImages,
          incomingVideos,
          incomingMediaRefs,
          source,
          eventMetadata
        );
      }
      this.taskInProgress = true;
      // 恢复“已发送但尚未收到任何回复”的运行中对话时，前端会先补一个
      // awaitingFirstContent assistant 占位。重放 user_message 不能把这个等待态清掉，
      // 否则输入区会丢失停止按钮。
      this.streamingMessage = isEmptyAssistantPlaceholderMessage(this.messages?.[this.messages.length - 1])
        ? true
        : false;
      this.stopRequested = false;
    } else {
      // 仅在对话运行期间展示 runtime_mode_notice；其余自动消息按来源正常显示，
      // 保持与后端历史重建一致（guidance/notify/sub_agent/background_command）。
      if (isRuntimeModeNotice && !(this.taskInProgress || this.streamingMessage)) {
        debugLog('[TaskPolling] 空闲态跳过 runtime_mode_notice 用户通知', {
          messagePreview: message.slice(0, 80)
        });
      } else {
        if (eventMetadata.starts_work === true) {
          this.markLatestUserWorkCompleted();
        }
        const shouldRestoreWaitingPlaceholder =
          this.moveTrailingEmptyAssistantPlaceholderAfterUserInsert('auto_user_message') &&
          (this.taskInProgress || this.streamingMessage);
        const recentMatch = findRecentMatchingUserMessage(
          this.messages || [],
          message,
          incomingImages,
          incomingVideos,
          source
        );
        if (recentMatch) {
          recentMatch.media_refs = incomingMediaRefs;
          recentMatch.metadata = {
            ...(recentMatch.metadata || {}),
            ...eventMetadata,
            media_refs: incomingMediaRefs,
            message_source: source
          };
          const backendTimestamp = data?.timestamp ?? data?.created_at ?? data?.createdAt ?? null;
          if (backendTimestamp) {
            recentMatch.created_at = backendTimestamp;
          }
        } else {
          this.chatAddUserMessage(
            message,
            incomingImages,
            incomingVideos,
            incomingMediaRefs,
            source,
            eventMetadata
          );
        }
        if (shouldRestoreWaitingPlaceholder) {
          this.chatStartAssistantMessage();
          this.taskInProgress = true;
          this.streamingMessage = true;
          this.stopRequested = false;
        }
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
  handleSystemMessage(data: any) {
    const content = (data?.content || data?.message || '').trim();
    userMDebug('taskPolling.handleSystemMessage:incoming', {
      raw: data,
      normalizedContent: content,
      currentConversationId: this.currentConversationId,
      currentMessagesLength: Array.isArray(this.messages) ? this.messages.length : -1
    });
    if (!content) {
      userMDebug('taskPolling.handleSystemMessage:empty-skip');
      return;
    }
    this.cleanupTrailingEmptyAssistantPlaceholder('before_system_message_append');
    this.appendSystemAction(content);
    userMDebug('taskPolling.handleSystemMessage:after-append', {
      currentMessagesLength: Array.isArray(this.messages) ? this.messages.length : -1,
      lastRole: this.messages?.[this.messages.length - 1]?.role || null,
      lastActions:
        this.messages?.[this.messages.length - 1]?.actions?.map((a: any) => a?.type) || []
    });
  }
};

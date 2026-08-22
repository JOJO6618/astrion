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

export const syncMethods = {
  handleRuntimeQueueSync(data: any) {
    const messages = Array.isArray(data?.messages) ? data.messages : [];
    const buildSnapshotKey =
      typeof this.buildRuntimeQueueSnapshotKey === 'function'
        ? this.buildRuntimeQueueSnapshotKey
        : (list: any[]) =>
            JSON.stringify(
              (Array.isArray(list) ? list : []).map((item: any) => [
                String(item?.id || ''),
                String(item?.text || ''),
                Number(item?.createdAt ?? item?.created_at ?? 0)
              ])
            );
    const incomingPreview = (Array.isArray(messages) ? messages : []).map((item: any) => ({
      id: String(item?.id || '').trim(),
      text: String(item?.text || '').trim(),
      createdAt: Number(item?.created_at ?? item?.createdAt ?? 0)
    }));
    const incomingKey = buildSnapshotKey(incomingPreview);
    const now = Date.now();
    const lockKey = String(this.runtimeQueueSyncLockKey || '');
    const lockUntil = Number(this.runtimeQueueSyncLockUntil || 0);
    if (lockKey && now < lockUntil && incomingKey !== lockKey) {
      debugLog('[RuntimeQueue] 忽略可能过期的轮询同步', {
        now,
        lockUntil,
        incomingSize: incomingPreview.length
      });
      return;
    }
    if (lockKey && incomingKey === lockKey) {
      this.runtimeQueueSyncLockKey = '';
      this.runtimeQueueSyncLockUntil = 0;
    }
    if (typeof this.applyRuntimeQueuedMessages === 'function') {
      this.applyRuntimeQueuedMessages(messages);
      return;
    }
    const limit = Math.max(1, Number(this.runtimeQueueLimit || 5));
    this.runtimeQueuedMessages = messages
      .map((item: any) => ({
        id: String(item?.id || '').trim(),
        text: String(item?.text || '').trim(),
        createdAt: Number(item?.created_at ?? item?.createdAt ?? Date.now())
      }))
      .filter((item: any) => item.id && item.text)
      .slice(0, limit);
  },
  handleTokenUpdate(data: any) {
    if (data.conversation_id === this.currentConversationId) {
      this.currentConversationTokens.cumulative_input_tokens = data.cumulative_input_tokens || 0;
      this.currentConversationTokens.cumulative_output_tokens = data.cumulative_output_tokens || 0;
      this.currentConversationTokens.cumulative_total_tokens = data.cumulative_total_tokens || 0;

      if (typeof data.current_context_tokens === 'number') {
        this.resourceSetCurrentContextTokens(data.current_context_tokens);
      } else {
        this.updateCurrentContextTokens();
      }

      this.$forceUpdate();
    }
  },
  handleConversationChanged(data: any, eventIdx: number) {
    debugLog('[TaskPolling] 对话标题已更新, idx:', eventIdx, data);

    if (data && data.conversation_id === this.currentConversationId) {
      // 更新当前对话标题
      if (data.title) {
        this.currentConversationTitle = data.title;
        debugLog('[TaskPolling] 更新当前对话标题:', data.title);
      }

      // 更新对话列表中的标题
      if (data.title && Array.isArray(this.conversations)) {
        const conv = this.conversations.find((c) => c && c.id === data.conversation_id);
        if (conv) {
          conv.title = data.title;
          debugLog('[TaskPolling] 更新对话列表中的标题:', data.title);
        }
      }

      this.$forceUpdate();
    }
  },
  handleConversationResolved(data: any) {
    // 独立全屏路由（工作流编辑器等）下不恢复对话、不改写 URL
    if (this.isConversationIndependentRoute()) return;
    if (data && data.conversation_id) {
      this.currentConversationId = data.conversation_id;
      if (data.title) {
        this.currentConversationTitle = data.title;
      }
      if (data.title && Array.isArray(this.runningWorkspaceTasks)) {
        const taskId = String(data.task_id || '').trim();
        const sourceConversationId = String(data.source_conversation_id || '').trim();
        const targetConversationId = String(data.conversation_id || '').trim();
        this.runningWorkspaceTasks = this.runningWorkspaceTasks.map((task: any) => {
          const sameTask = taskId && String(task?.task_id || '') === taskId;
          const sameSourceConversation =
            sourceConversationId && String(task?.conversation_id || '') === sourceConversationId;
          const sameTargetConversation =
            targetConversationId && String(task?.conversation_id || '') === targetConversationId;
          if (!sameTask && !sameSourceConversation && !sameTargetConversation) {
            return task;
          }
          return {
            ...task,
            conversation_id: targetConversationId || task?.conversation_id,
            conversation_title: data.title
          };
        });
      }
      this.promoteConversationToTop(data.conversation_id);

      const pathFragment = this.stripConversationPrefix(data.conversation_id);
      const currentPath = window.location.pathname.replace(/^\/+/, '');
      // 对话类型不再是路由概念，统一裸路径 /<id>
      const targetPath = `/${pathFragment}`;
      if (data.created) {
        history.pushState({ conversationId: data.conversation_id }, '', targetPath);
      } else if (currentPath !== pathFragment) {
        history.replaceState({ conversationId: data.conversation_id }, '', targetPath);
      }
    }
  }
};

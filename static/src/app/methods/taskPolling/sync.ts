// @ts-nocheck
import { debugLog } from '../common';
import { useResourceStore } from '../../../stores/resource';
import { invalidateAuxiliaryRequest } from '../auxiliaryOwnership';

export const syncMethods = {
  handleRuntimeQueueSync(data: any) {
    if (data?.paused) this.runtimeQueuePaused = true;
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
      invalidateAuxiliaryRequest(useResourceStore(), 'token-statistics');
      this.currentConversationTokens.cumulative_input_tokens = data.cumulative_input_tokens || 0;
      this.currentConversationTokens.cumulative_output_tokens = data.cumulative_output_tokens || 0;
      this.currentConversationTokens.cumulative_total_tokens = data.cumulative_total_tokens || 0;
      this.currentConversationTokens.cumulative_cached_input_tokens =
        data.cumulative_cached_input_tokens || 0;
      this.currentConversationTokens.cache_exempt_input_tokens =
        data.cache_exempt_input_tokens || 0;
      this.currentConversationTokens.costs = data.costs || null;

      if (typeof data.current_context_tokens === 'number') {
        this.resourceSetCurrentContextTokens(data.current_context_tokens);
      } else {
        this.updateCurrentContextTokens();
      }

      // 工作段结束：本段 tokens/s 汇总挂到铺点 user 消息，就地渲染速度行
      if (data?.token_speed && Number(data.token_speed.avg_tps) > 0) {
        this.applyWorkTokenSpeed(data.token_speed);
      }

      this.$forceUpdate();
    }
  },
  /**
   * 把后端下发的本段 tokens/s 汇总写回本地消息 metadata。
   * 优先按 message_id 定位，旧消息无 id 时回退到「最后一条开启工作的 user 消息」
   * （与 markLatestUserWorkCompleted 同一铺点规则）。
   */
  applyWorkTokenSpeed(speed: any) {
    if (!Array.isArray(this.messages) || this.messages.length === 0) return;
    const messageId = String(speed?.message_id || '').trim();
    const summary: Record<string, any> = { ...speed };
    delete summary.message_id;
    let target: any = null;
    if (messageId) {
      for (let i = this.messages.length - 1; i >= 0; i -= 1) {
        const msg = this.messages[i];
        if (msg?.role === 'user' && String(msg.message_id || '') === messageId) {
          target = msg;
          break;
        }
      }
    }
    if (!target) {
      for (let i = this.messages.length - 1; i >= 0; i -= 1) {
        const msg = this.messages[i];
        if (msg?.role === 'user' && msg.metadata?.work_timer) {
          target = msg;
          break;
        }
      }
    }
    if (!target) return;
    target.metadata = { ...(target.metadata || {}), token_speed: summary };
    this.$forceUpdate();
  },
  handleConversationChanged(data: any, eventIdx: number) {
    debugLog('[TaskPolling] 对话标题已更新, idx:', eventIdx, data);

    if (data?.conversation_id && data?.title) {
      this.applyConversationTitleUpdate(data.conversation_id, data.title, 'task-event');
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

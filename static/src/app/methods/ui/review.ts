// @ts-nocheck
import { debugLog } from '../common';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const reviewMethods = {
  handleCompressConversationClick() {
    if (this.compressing || this.streamingMessage || !this.isConnected) {
      return;
    }
    if (this.compressionActiveForCurrentConversation) {
      this.uiPushToast({
        title: '对话自动压缩中',
        message: '当前对话正在压缩，请稍后再试',
        type: 'warning'
      });
      return;
    }
    if (this.isPolicyBlocked('block_compress_conversation', '压缩对话已被管理员禁用')) {
      return;
    }
    this.compressConversation();
  },
  async loadReviewConversations() {
    if (this.reviewListLoading) return;
    this.reviewListLoading = true;
    this.reviewListOffset = 0;
    try {
      const limit = this.reviewListLimit;
      const resp = await fetch(`/api/conversations?limit=${limit}&offset=0&non_empty=1`);
      const data = await resp.json().catch(() => ({}));
      if (data?.success) {
        this.reviewConversations = data.data.conversations || [];
        this.reviewListHasMore = Boolean(data.data.has_more);
        this.autoSelectReviewConversation();
      } else {
        this.reviewConversations = [];
        this.reviewListHasMore = false;
      }
    } catch (error) {
      console.error('加载回顾对话列表异常:', error);
      this.reviewConversations = [];
      this.reviewListHasMore = false;
    } finally {
      this.reviewListLoading = false;
    }
  },
  async loadMoreReviewConversations() {
    if (this.reviewListLoadingMore || !this.reviewListHasMore) return;
    this.reviewListLoadingMore = true;
    try {
      const limit = this.reviewListLimit;
      const offset = this.reviewListOffset + limit;
      const resp = await fetch(`/api/conversations?limit=${limit}&offset=${offset}&non_empty=1`);
      const data = await resp.json().catch(() => ({}));
      if (data?.success) {
        // 追加到现有列表，不整体替换，避免滚动位置复位
        this.reviewConversations.push(...(data.data.conversations || []));
        this.reviewListHasMore = Boolean(data.data.has_more);
        this.reviewListOffset = offset;
        this.autoSelectReviewConversation();
      }
    } catch (error) {
      console.error('加载更多回顾对话异常:', error);
    } finally {
      this.reviewListLoadingMore = false;
    }
  },
  async handleConfirmReview() {
    if (this.reviewSubmitting) return;
    if (!this.reviewSelectedConversationId) {
      this.uiPushToast({
        title: '请选择对话',
        message: '请选择要生成回顾的对话记录',
        type: 'info'
      });
      return;
    }
    if (this.reviewSelectedConversationId === this.currentConversationId) {
      this.uiPushToast({
        title: '无法引用当前对话',
        message: '请选择其他对话生成回顾',
        type: 'warning'
      });
      return;
    }
    if (!this.currentConversationId) {
      this.uiPushToast({
        title: '无法发送',
        message: '当前没有活跃对话，无法自动发送提示消息',
        type: 'warning'
      });
      return;
    }

    this.reviewSubmitting = true;
    try {
      const { path, char_count } = await this.generateConversationReview(
        this.reviewSelectedConversationId
      );
      if (!path) {
        throw new Error('未获取到生成的文件路径');
      }
      const count = typeof char_count === 'number' ? char_count : 0;
      this.reviewGeneratedPath = path;
      const suggestion =
        count && count <= 10000 ? '建议直接完整阅读。' : '建议使用 read 工具进行搜索或分段阅读。';
      if (this.reviewSendToModel) {
        const message = `帮我继续这个任务，对话文件在 ${path}，文件长 ${count || '未知'} 字符，${suggestion} 请阅读文件了解后，不要直接继续工作，而是向我汇报你的理解，然后等我做出指示。`;
        const sent = await this.sendAutoUserMessage(message);
        if (sent) {
          this.reviewDialogOpen = false;
        }
      } else {
        this.uiPushToast({
          title: '回顾文件已生成',
          message: path,
          type: 'success'
        });
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '生成失败');
      this.uiPushToast({
        title: '生成回顾失败',
        message: msg,
        type: 'error'
      });
    } finally {
      this.reviewSubmitting = false;
    }
  },
  async loadReviewPreview(conversationId) {
    this.reviewPreviewLoading = true;
    this.reviewPreviewError = null;
    this.reviewPreviewLines = [];
    try {
      const resp = await fetch(
        `/api/conversations/${conversationId}/review_preview?limit=${this.reviewPreviewLimit}`
      );
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload?.success) {
        const msg = payload?.message || payload?.error || '获取预览失败';
        throw new Error(msg);
      }
      this.reviewPreviewLines = payload?.data?.preview || [];
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || '获取预览失败');
      this.reviewPreviewError = msg;
    } finally {
      this.reviewPreviewLoading = false;
    }
  },
  async generateConversationReview(conversationId) {
    const response = await fetch(`/api/conversations/${conversationId}/review`, {
      method: 'POST'
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload?.success) {
      const msg = payload?.message || payload?.error || '生成失败';
      throw new Error(msg);
    }
    const data = payload.data || payload;
    return {
      path: data.path || data.file_path || data.relative_path,
      char_count: data.char_count ?? data.length ?? data.size ?? 0
    };
  }
};

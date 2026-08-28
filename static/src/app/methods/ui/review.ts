// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
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
        title: t('appUi.conversationAutoCompressing'),
        message: t('appUi.compressingNowPleaseWait'),
        type: 'warning'
      });
      return;
    }
    if (this.isPolicyBlocked('block_compress_conversation', t('appUi.policyBlockedCompress'))) {
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
        title: t('appUi.selectConversation'),
        message: t('appUi.selectConversationForReview'),
        type: 'info'
      });
      return;
    }
    if (this.reviewSelectedConversationId === this.currentConversationId) {
      this.uiPushToast({
        title: t('appUi.cannotReferenceCurrentConversation'),
        message: t('appUi.chooseOtherConversationForReview'),
        type: 'warning'
      });
      return;
    }
    if (!this.currentConversationId) {
      this.uiPushToast({
        title: t('appUi.cannotSend'),
        message: t('appUi.noActiveConversationMessage'),
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
        throw new Error(t('appUi.reviewPathMissing'));
      }
      const count = typeof char_count === 'number' ? char_count : 0;
      this.reviewGeneratedPath = path;
      const suggestion =
        count && count <= 10000 ? t('appUi.reviewSuggestReadFull') : t('appUi.reviewSuggestReadBySearch');
      if (this.reviewSendToModel) {
        const message = t('appUi.reviewAutoMessage', {
          path,
          count: count || t('appUi.unknown'),
          suggestion
        });
        const sent = await this.sendAutoUserMessage(message);
        if (sent) {
          this.reviewDialogOpen = false;
        }
      } else {
        this.uiPushToast({
          title: t('appUi.reviewFileGenerated'),
          message: path,
          type: 'success'
        });
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.generateFailed'));
      this.uiPushToast({
        title: t('appUi.generateReviewFailed'),
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
        const msg = payload?.message || payload?.error || t('appUi.fetchPreviewFailed');
        throw new Error(msg);
      }
      this.reviewPreviewLines = payload?.data?.preview || [];
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error || t('appUi.fetchPreviewFailed'));
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
      const msg = payload?.message || payload?.error || t('appUi.generateFailed');
      throw new Error(msg);
    }
    const data = payload.data || payload;
    return {
      path: data.path || data.file_path || data.relative_path,
      char_count: data.char_count ?? data.length ?? data.size ?? 0
    };
  }
};

// @ts-nocheck
import { t } from '@/locales';
import { useTaskStore } from '../../../stores/task';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { ensureMessageSession } from './ownership';
import {
  beginConversationSubmission,
  endConversationSubmission,
  ownsConversationSession
} from '../conversation/session';

export const guidanceMethods = {
  async sendRuntimeGuidance() {
    if (this.historyLoading || this.hostWorkspaceSwitching || this.mediaUploading) return false;
    const session = ensureMessageSession(this);
    const submission = beginConversationSubmission(session);
    if (!submission) return false;
    const draft = this.captureComposerDraftSubmission();
    const owns = () => ownsConversationSession(this, session);
    try {
      const taskId = useTaskStore().currentTaskId;
      if (!taskId) throw new Error(t('appMessages.noActiveTaskMessage'));
      const prepared = this.getInputComposerRef?.()?.prepareMessageForSend?.(this.inputMessage);
      const text = String(prepared?.message ?? this.inputMessage ?? '').trim();
      const images = [...(this.selectedImages || [])];
      const videos = [...(this.selectedVideos || [])];
      const files = [...(this.selectedFiles || [])];
      if (!text && !images.length && !videos.length && !files.length) return false;
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/runtime_guidance`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Astrion-Workspace-Id': session.workspaceId
        },
        body: JSON.stringify({
          message: text,
          images,
          videos,
          files,
          media_paths_only: true,
          conversation_id: session.conversationId
        })
      });
      const result = await response.json();
      // Acceptance clears only this submitted version, even if navigation happened meanwhile.
      if (!response.ok || !result.success)
        throw new Error(result.error || t('appMessages.submitGuideFailed'));
      this.completeComposerDraftSubmission(draft, owns());
      if (owns())
        this.uiPushToast?.({
          title: t('appMessages.guideSetTitle'),
          message: t('appMessages.guideSetMessage'),
          type: 'success',
          duration: 1800
        });
      return true;
    } catch (error) {
      if (owns())
        this.uiPushToast?.({
          title: t('appMessages.guideFailedTitle'),
          message: String(error?.message || error),
          type: 'error'
        });
      return false;
    } finally {
      endConversationSubmission(session, submission);
    }
  }
};

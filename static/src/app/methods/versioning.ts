// @ts-nocheck
import { debugLog } from './common';
import { t } from '@/locales';
import { persistWorkspaceMode } from '../state';

const normalizeTrackingMode = (value: any): 'workspace_and_conversation' | 'conversation_only' => {
  return String(value || '').toLowerCase() === 'conversation_only'
    ? 'conversation_only'
    : 'workspace_and_conversation';
};

export const versioningMethods = {
  async fetchVersioningStatus(conversationId = null, options = {}) {
    const targetId = conversationId || this.currentConversationId;
    const { silent = false } = options as { silent?: boolean };
    if (!targetId) return null;
    try {
      const resp = await fetch(`/api/conversations/${targetId}/versioning`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.success) {
        if (!silent) {
          throw new Error(data?.error || t('appMessages.versioningFetchStatusFailed'));
        }
        return null;
      }
      const payload = data.data || {};
      this.versioningHostMode = !!payload.host_mode;
      persistWorkspaceMode(!!payload.host_mode);
      this.versioningEnabled = !!payload.enabled;
      this.versioningMode = 'overwrite';
      this.versioningRestoreMode = 'overwrite';
      return payload;
    } catch (error) {
      if (!silent) {
        this.uiPushToast({
          title: t('common.versioning'),
          message: error?.message || t('appMessages.versioningFetchStatusFailed'),
          type: 'error'
        });
      }
      return null;
    }
  },

  async fetchVersioningCheckpoints(conversationId = null) {
    const targetId = conversationId || this.currentConversationId;
    if (!targetId) return [];
    this.versioningLoading = true;
    try {
      const resp = await fetch(`/api/conversations/${targetId}/versioning/checkpoints`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.success) {
        throw new Error(data?.error || t('appMessages.versioningLoadCheckpointsFailed'));
      }
      const items = Array.isArray(data?.data?.items) ? data.data.items : [];
      this.versioningCheckpoints = items;
      if (!items.length) {
        this.versioningSelectedSeq = null;
        this.versioningSelectedDetail = null;
      }
      return items;
    } catch (error) {
      this.uiPushToast({
        title: t('common.versioning'),
        message: error?.message || t('appMessages.versioningLoadCheckpointsFailed'),
        type: 'error'
      });
      return [];
    } finally {
      this.versioningLoading = false;
    }
  },

  async openVersioningDialog() {
    this.versioningRestoreMode = 'overwrite';
    this.versioningTrackingMode = 'conversation_only';
    this.versioningDialogOpen = true;
    this.versioningSelectedSeq = null;
    this.versioningSelectedDetail = null;
    this.versioningCheckpoints = [];
    if (!this.currentConversationId) {
      this.versioningEnabled = false;
      return;
    }
    await this.fetchVersioningStatus(this.currentConversationId);
    await this.fetchVersioningCheckpoints(this.currentConversationId);
  },

  async refreshVersioningDialog() {
    await this.fetchVersioningStatus(this.currentConversationId);
    await this.fetchVersioningCheckpoints(this.currentConversationId);
  },

  async toggleConversationVersioning(enabled: boolean) {
    if (!this.currentConversationId) {
      this.versioningEnabled = !!enabled;
      this.uiPushToast({
        title: t('common.versioning'),
        message: enabled ? t('appMessages.versioningEnabledForNext') : t('appMessages.versioningDisabledForNext'),
        type: 'success'
      });
      return;
    }
    this.versioningLoading = true;
    try {
      const resp = await fetch(`/api/conversations/${this.currentConversationId}/versioning`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: !!enabled,
          mode: 'overwrite'
        })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.success) {
        throw new Error(data?.error || t('appMessages.versioningToggleFailed'));
      }
      this.versioningEnabled = !!data?.data?.enabled;
      this.versioningMode = 'overwrite';
      this.versioningRestoreMode = 'overwrite';
      this.uiPushToast({
        title: t('common.versioning'),
        message: this.versioningEnabled ? t('appMessages.versioningOn') : t('appMessages.versioningOff'),
        type: 'success'
      });
      if (this.versioningEnabled) {
        await this.fetchVersioningCheckpoints(this.currentConversationId);
      } else {
        this.versioningCheckpoints = [];
        this.versioningSelectedSeq = null;
        this.versioningSelectedDetail = null;
      }
    } catch (error) {
      this.uiPushToast({
        title: t('common.versioning'),
        message: error?.message || t('appMessages.versioningSwitchFailed'),
        type: 'error'
      });
    } finally {
      this.versioningLoading = false;
    }
  },

  async selectVersioningCheckpoint(seq: number) {
    if (!this.currentConversationId || seq === null || seq === undefined || Number.isNaN(Number(seq))) return;
    const targetSeq = Number(seq);
    this.versioningSelectedSeq = targetSeq;
    this.versioningDetailLoading = true;
    try {
      const resp = await fetch(
        `/api/conversations/${this.currentConversationId}/versioning/checkpoints/${encodeURIComponent(String(targetSeq))}`
      );
      const text = await resp.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (parseErr: any) {
        // eslint-disable-next-line no-console
        console.error('[VersioningDetail] parse failed:', parseErr?.message, 'status=', resp.status, 'text=', text);
        throw new Error(`${t('appMessages.versioningDetailParseFailed')}: ${parseErr?.message || t('common.unknownError')}`);
      }
      // eslint-disable-next-line no-console
      if (!resp.ok || !data?.success) {
        throw new Error(data?.error || t('appMessages.versioningLoadDetailFailed'));
      }
      this.versioningSelectedDetail = data.data || null;
      // eslint-disable-next-line no-console
      // eslint-disable-next-line no-console
      // eslint-disable-next-line no-console
    } catch (error: any) {
      // eslint-disable-next-line no-console
      console.error('[VersioningDetail] error:', error);
      this.uiPushToast({
        title: t('common.versioning'),
        message: error?.message || t('appMessages.versioningLoadDetailFailed'),
        type: 'error'
      });
    } finally {
      this.versioningDetailLoading = false;
    }
  },

  async confirmVersioningRestore() {
    if (
      !this.currentConversationId ||
      this.versioningSelectedSeq === null ||
      this.versioningSelectedSeq === undefined
    ) return;

    const restoreMode = this.versioningRestoreMode || 'overwrite';
    const trackingMode = normalizeTrackingMode(this.versioningTrackingMode);
    const scopeLabel =
      trackingMode === 'conversation_only'
        ? t('appMessages.versioningScopeConversationOnly')
        : t('appMessages.versioningScopeConversationAndWorkspace');
    const modeLabel =
      restoreMode === 'copy' ? t('appMessages.versioningModeCopy') : t('appMessages.versioningModeOverwrite');
    const confirmed = await this.confirmAction({
      title: t('appMessages.versioningRestoreConfirmTitle'),
      message: t('appMessages.versioningRestoreConfirmMessage', {
        scope: scopeLabel,
        mode: modeLabel,
        seq: this.versioningSelectedSeq
      }),
      confirmText: t('appMessages.versioningRestoreConfirmText'),
      cancelText: t('common.cancel')
    });
    if (!confirmed) return;

    this.versioningRestoring = true;
    try {
      const resp = await fetch(`/api/conversations/${this.currentConversationId}/versioning/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          seq: this.versioningSelectedSeq,
          mode: restoreMode,
          tracking_mode: trackingMode
        })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.success) {
        throw new Error(data?.error || t('appMessages.versioningRestoreFailed'));
      }
      const targetConversationId = data?.data?.conversation_id || this.currentConversationId;
      this.versioningDialogOpen = false;
      await this.loadConversation(targetConversationId, { force: true });
      // 立即刷新侧边栏对话列表，避免依赖 socket 事件延迟或丢失
      this.conversationsOffset = 0;
      await this.loadConversationsList();
      // copy 模式下给侧边栏一个即时占位，随后列表刷新会补齐真实数据
      if (restoreMode === 'copy' && !this.conversations.some((c) => c && c.id === targetConversationId)) {
        this.conversations.splice(
          0,
          this.conversations.length,
          { id: targetConversationId, title: t('appMessages.versioningRestoreConversationTitle'), updated_at: new Date().toISOString(), total_messages: 0, total_tools: 0 },
          ...this.conversations.filter((c) => c && c.id !== targetConversationId)
        );
      }
      this.uiPushToast({
        title: t('common.versioning'),
        message: restoreMode === 'copy' ? t('appMessages.versioningRestoreCopyDone') : t('appMessages.versioningRestoreDone'),
        type: 'success'
      });
    } catch (error) {
      this.uiPushToast({
        title: t('common.versioning'),
        message: error?.message || t('appMessages.versioningRestoreFailed'),
        type: 'error'
      });
    } finally {
      this.versioningRestoring = false;
    }
  },

};

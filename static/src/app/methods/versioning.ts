// @ts-nocheck
import { debugLog } from './common';
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
          throw new Error(data?.error || '获取版本状态失败');
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
          title: '版本管理',
          message: error?.message || '获取版本状态失败',
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
        throw new Error(data?.error || '加载版本点失败');
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
        title: '版本管理',
        message: error?.message || '加载版本点失败',
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
        title: '版本管理',
        message: enabled ? '已为下一次新对话开启版本管理' : '已取消下一次新对话的版本管理',
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
        throw new Error(data?.error || '切换版本管理失败');
      }
      this.versioningEnabled = !!data?.data?.enabled;
      this.versioningMode = 'overwrite';
      this.versioningRestoreMode = 'overwrite';
      this.uiPushToast({
        title: '版本管理',
        message: this.versioningEnabled ? '已开启' : '已关闭',
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
        title: '版本管理',
        message: error?.message || '切换失败',
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
        throw new Error(`详情响应解析失败: ${parseErr?.message || '未知错误'}`);
      }
      // eslint-disable-next-line no-console
      if (!resp.ok || !data?.success) {
        throw new Error(data?.error || '加载详情失败');
      }
      this.versioningSelectedDetail = data.data || null;
      // eslint-disable-next-line no-console
      // eslint-disable-next-line no-console
      // eslint-disable-next-line no-console
    } catch (error: any) {
      // eslint-disable-next-line no-console
      console.error('[VersioningDetail] error:', error);
      this.uiPushToast({
        title: '版本管理',
        message: error?.message || '加载详情失败',
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
    const scopeLabel = trackingMode === 'conversation_only' ? '仅回溯对话' : '回溯对话和工作区';
    const modeLabel = restoreMode === 'copy' ? '复制对话' : '覆盖当前对话';
    const confirmed = await this.confirmAction({
      title: '确认回溯',
      message: `将${scopeLabel}到输入 #${this.versioningSelectedSeq} 对应的状态，并${modeLabel}。是否继续？`,
      confirmText: '回溯',
      cancelText: '取消'
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
        throw new Error(data?.error || '回溯失败');
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
          { id: targetConversationId, title: '版本回溯对话', updated_at: new Date().toISOString(), total_messages: 0, total_tools: 0 },
          ...this.conversations.filter((c) => c && c.id !== targetConversationId)
        );
      }
      this.uiPushToast({
        title: '版本管理',
        message: restoreMode === 'copy' ? '已复制并回溯到新对话' : '回溯完成',
        type: 'success'
      });
    } catch (error) {
      this.uiPushToast({
        title: '版本管理',
        message: error?.message || '回溯失败',
        type: 'error'
      });
    } finally {
      this.versioningRestoring = false;
    }
  },

};

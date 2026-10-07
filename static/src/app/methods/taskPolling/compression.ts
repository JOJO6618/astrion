// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';

export const compressionMethods = {
  handleCompressionState(data: any) {
    if (!data || typeof data !== 'object' || data.conversation_id !== this.currentConversationId) {
      return;
    }
    const wasInProgress = !!this.compressionInProgress;
    this.compressionInProgress = !!data.in_progress;
    // 归属必须来自事件，禁止把其他对话的状态绑到当前页面。
    this.compressionConversationId = this.compressionInProgress ? data.conversation_id : null;
    this.compressionMode = data.mode || '';
    this.compressionStage = data.stage || '';
    if (this.compressionInProgress && !wasInProgress) {
      const modeLabel =
        this.compressionMode === 'manual'
          ? t('appTasks.compressionManual')
          : t('appTasks.compressionAuto');
      if (this.compressionToastId) {
        this.uiDismissToast(this.compressionToastId);
        this.compressionToastId = null;
      }
      this.compressionToastId = this.uiPushToast({
        title: t('appTasks.compressing'),
        message: t('appTasks.compressingMessage', { mode: modeLabel }),
        type: 'info',
        duration: null,
        closable: false
      });
    }
    if (!this.compressionInProgress) {
      this.compressionError = data.error || '';
      if (this.compressionToastId) {
        this.uiDismissToast(this.compressionToastId);
        this.compressionToastId = null;
      }
    }
  },
  handleShallowCompression(data: any, eventIdx?: number) {
    const count = Number(data?.compressed_count || 0);
    if (count <= 0) {
      return;
    }
    debugLog('[TaskPolling] 自动浅层压缩触发, idx:', eventIdx, data);
    this.uiPushToast({
      title: t('appTasks.shallowCompressionTitle'),
      message: t('appTasks.shallowCompressedMessage', { n: count }),
      type: 'info',
      duration: 2500
    });
  },
  async handleCompressionFinished(data: any) {
    if (data?.conversation_id !== this.currentConversationId) return;
    if (this.compressionToastId) {
      this.uiDismissToast(this.compressionToastId);
      this.compressionToastId = null;
    }
    this.compressionInProgress = false;
    this.compressionConversationId = null;
    this.compressionMode = '';
    this.compressionStage = '';
    this.compressionError = '';
    // 压缩提交已更新快照水位；自动与手动压缩都重新接管快照后的增量。
    const refresh = this.refreshConversationSnapshot();
    Promise.resolve(refresh).catch((error) => {
      debugLog('[TaskPolling] compression snapshot unavailable', String(error));
    });
    this.uiPushToast({
      title: t('appTasks.compressionComplete'),
      message: t('appTasks.compressedEarlierContent'),
      type: 'success',
      duration: 2400
    });
  }
};

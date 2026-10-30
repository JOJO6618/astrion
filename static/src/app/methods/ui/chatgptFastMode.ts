// @ts-nocheck
import { t } from '@/locales';
import { beginAuxiliaryRequest } from '../auxiliaryOwnership';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';

export const chatgptFastModeMethods = {
  async toggleChatgptFastMode() {
    if (
      !this.isConnected ||
      this.streamingMessage ||
      this.chatgptFastModeSaving ||
      !this.isChatgptModel ||
      !this.supportsChatgptFastMode
    )
      return;
    const enabled = !this.chatgptFastMode;
    if (!this.currentConversationId) {
      this.chatgptFastMode = enabled;
      return;
    }
    const owns = beginAuxiliaryRequest(this, 'chatgpt-fast-mode');
    this.chatgptFastModeSaving = true;
    try {
      const response = await fetch('/api/chatgpt-fast-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled, conversation_id: this.currentConversationId })
      });
      const payload = await response.json();
      if (!owns()) return;
      if (!response.ok || !payload.success)
        throw new Error(payload.error || t('appUi.settingFailed'));
      this.chatgptFastMode = payload.data.chatgpt_fast_mode === true;
    } catch (error) {
      if (owns())
        this.uiPushToast({
          title: t('appUi.settingFailed'),
          message: error instanceof Error ? error.message : String(error),
          type: 'error'
        });
    } finally {
      if (owns()) this.chatgptFastModeSaving = false;
    }
  }
};

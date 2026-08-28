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

export const modelMethods = {
  toggleModelMenu() {
    if (!this.isConnected || this.streamingMessage) {
      return;
    }
    const next = !this.modelMenuOpen;
    this.modelMenuOpen = next;
    if (next) {
      this.modeMenuOpen = false;
      this.inputSetToolMenuOpen(false);
      this.inputSetSettingsOpen(false);
      if (!this.quickMenuOpen) {
        this.inputOpenQuickMenu();
      }
    }
  },
  async handleModelSelect(key) {
    if (!this.isConnected || this.streamingMessage) {
      return;
    }
    const policyStore = usePolicyStore();
    if (policyStore.isModelDisabled(key)) {
      this.uiPushToast({
        title: t('appUi.modelDisabled'),
        message: t('appUi.forceDisabledByAdmin'),
        type: 'warning'
      });
      return;
    }
    const modelStore = useModelStore();
    const targetModel = modelStore.models.find((m) => m.key === key);
    if (this.conversationHasImages && !targetModel?.supportsImage) {
      this.uiPushToast({
        title: t('appUi.switchFailed'),
        message: t('appUi.conversationHasImagesMessage'),
        type: 'error'
      });
      return;
    }
    if (this.conversationHasVideos && !targetModel?.supportsVideo) {
      this.uiPushToast({
        title: t('appUi.switchFailed'),
        message: t('appUi.conversationHasVideosMessage'),
        type: 'error'
      });
      return;
    }
    const prev = this.currentModelKey;
    try {
      // 携带当前对话 id：对话级隔离后，后端需要把模型设置到该对话专属的
      // 对话级 terminal 并保存到正确的对话文件，避免写到工作区级 terminal
      // 的陈旧对话（否则重启后模型回变为默认）。
      const resp = await fetch('/api/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_key: key,
          conversation_id: this.currentConversationId || undefined
        })
      });
      const payload = await resp.json();
      if (!resp.ok || !payload.success) {
        throw new Error(payload.error || payload.message || t('appUi.switchFailed'));
      }
      const data = payload.data || {};
      modelStore.setModel(data.model_key || key);
      if (data.run_mode) {
        this.runMode = data.run_mode;
        this.thinkingMode = data.thinking_mode ?? data.run_mode !== 'fast';
      } else {
        // 前端兼容策略：根据模型特性自动调整运行模式
        const currentModel = modelStore.currentModel;
        if (currentModel?.thinkingOnly) {
          this.runMode = 'thinking';
          this.thinkingMode = true;
        } else if (currentModel?.fastOnly) {
          this.runMode = 'fast';
          this.thinkingMode = false;
        } else {
          this.thinkingMode = this.runMode !== 'fast';
        }
      }
      this.uiPushToast({
        title: t('appUi.modelSwitched'),
        message: modelStore.currentModel?.label || key,
        type: 'success'
      });
    } catch (error) {
      modelStore.setModel(prev);
      const msg = error instanceof Error ? error.message : String(error || t('appUi.switchFailed'));
      this.uiPushToast({
        title: t('appUi.switchModelFailed'),
        message: msg,
        type: 'error'
      });
    } finally {
      this.modelMenuOpen = false;
      this.inputCloseMenus();
      this.inputSetQuickMenuOpen(false);
    }
  },
  async handleHeaderModelSelect(key, disabled) {
    if (disabled) return;
    await this.handleModelSelect(key);
    this.closeHeaderMenu();
  }
};

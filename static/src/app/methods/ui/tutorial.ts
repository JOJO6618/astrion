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

export const tutorialMethods = {
  async checkTutorialPrompt() {
    this.tutorialPromptVisible = false;
    this.tutorialPromptUsername = '';
    try {
      const resp = await fetch('/api/tutorial-status', { credentials: 'same-origin' });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.success) {
        return;
      }
      const payload = data.data || {};
      if (!payload.applicable || !payload.should_prompt) {
        return;
      }
      this.tutorialPromptUsername = payload.username || '';
      this.tutorialPromptVisible = true;
    } catch (error) {
      console.warn('获取新手教程提示状态失败:', error);
    }
  },
  async updateTutorialPromptStatus(completed = true) {
    this.tutorialPromptLoading = true;
    try {
      const resp = await fetch('/api/tutorial-status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ tutorial_completed: !!completed })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.success) {
        throw new Error(data?.error || '更新新手教程状态失败');
      }
      const tutorialStore = useTutorialStore();
      tutorialStore.markCompleted();
      this.tutorialPromptVisible = false;
      return true;
    } catch (error) {
      console.warn('更新新手教程提示状态失败:', error);
      this.uiPushToast({
        title: '提示',
        message: '保存新手教程状态失败，请稍后重试',
        type: 'warning'
      });
      return false;
    } finally {
      this.tutorialPromptLoading = false;
    }
  },
  async handleNewUserTutorialStart() {
    const ok = await this.updateTutorialPromptStatus(true);
    if (!ok) {
      return;
    }
    const personalStore = usePersonalizationStore();
    personalStore.closeDrawer();
    const tutorialStore = useTutorialStore();
    window.setTimeout(() => {
      tutorialStore.startTutorial();
    }, 220);
  },
  async handleNewUserTutorialSkip() {
    await this.updateTutorialPromptStatus(true);
  }
};

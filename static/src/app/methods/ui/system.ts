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

export const systemMethods = {
  addSystemMessage(content) {
    userMDebug('ui.addSystemMessage:incoming', { content });
    const systemNoticeLabel = parseSystemNoticeLabel(content);
    if (!systemNoticeLabel) {
      // 其他 system 消息全部隐藏，且不进入渲染链路，避免出现空白块
      userMDebug('ui.addSystemMessage:blocked', { content });
      return;
    }
    userMDebug('ui.addSystemMessage:accepted', {
      original: content,
      normalizedLabel: systemNoticeLabel
    });
    this.chatAddSystemMessage(systemNoticeLabel, { variant: 'sub_agent_done' });
    this.$forceUpdate();
    this.conditionalScrollToBottom();
  },
  appendSystemAction(content) {
    this.addSystemMessage(content);
  },
  startTitleTyping(title: string, options: { animate?: boolean } = {}) {
    if (this.titleTypingTimer) {
      clearInterval(this.titleTypingTimer);
      this.titleTypingTimer = null;
    }
    const target = (title || '').trim();
    const animate = options.animate ?? true;
    if (!animate) {
      this.titleTypingTarget = target;
      this.titleTypingText = target;
      return;
    }
    const previous = (this.titleTypingText || '').trim();
    if (previous === target) {
      this.titleTypingTarget = target;
      this.titleTypingText = target;
      return;
    }
    this.titleTypingTarget = target;
    this.titleTypingText = previous;

    const frames: string[] = [];
    for (let i = previous.length; i >= 0; i--) {
      frames.push(previous.slice(0, i));
    }
    for (let j = 1; j <= target.length; j++) {
      frames.push(target.slice(0, j));
    }

    let index = 0;
    this.titleTypingTimer = window.setInterval(() => {
      if (index >= frames.length) {
        clearInterval(this.titleTypingTimer!);
        this.titleTypingTimer = null;
        this.titleTypingText = target;
        return;
      }
      this.titleTypingText = frames[index];
      index += 1;
    }, 32);
  },
  isConversationBlank() {
    if (!Array.isArray(this.messages) || !this.messages.length) return true;
    return !this.messages.some((msg) => msg && (msg.role === 'user' || msg.role === 'assistant'));
  },
  pickWelcomeText() {
    const pool = this.blankWelcomePool;
    if (!Array.isArray(pool) || !pool.length) {
      this.blankWelcomeText = '有什么可以帮忙的？';
      return;
    }
    const idx = Math.floor(Math.random() * pool.length);
    this.blankWelcomeText = pool[idx];
  },
  refreshBlankHeroState() {
    const isBlank = this.isConversationBlank();
    const currentConv = this.currentConversationId || 'temp';
    const needNewWelcome = !this.blankHeroActive || this.lastBlankConversationId !== currentConv;

    if (isBlank) {
      if (needNewWelcome && !this.blankHeroExiting) {
        this.pickWelcomeText();
      }
      this.blankHeroActive = true;
      this.lastBlankConversationId = currentConv;
    } else {
      this.blankHeroActive = false;
      this.blankHeroExiting = false;
      this.lastBlankConversationId = null;
    }
  },
  isOutputActive() {
    return !!(this.streamingMessage || this.taskInProgress || this.hasPendingToolActions());
  },
  logMessageState(action, extra = {}) {
    const count = Array.isArray(this.messages) ? this.messages.length : 'N/A';
    debugLog('[Messages]', {
      action,
      count,
      conversationId: this.currentConversationId,
      streaming: this.streamingMessage,
      ...extra
    });
  },
  iconStyle(iconKey, size) {
    const iconPath = this.icons ? this.icons[iconKey] : null;
    if (!iconPath) {
      return {};
    }
    const style = { '--icon-src': `url(${iconPath})` };
    if (size) {
      style['--icon-size'] = size;
    }
    return style;
  },
  renderMarkdown(content, isStreaming = false) {
    return renderMarkdownHelper(content, isStreaming);
  },
  decodeHtmlEntities(text) {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
  },
  async handleCopyCodeClick(event) {
    const target = event.target;
    if (!target || !target.classList || !target.classList.contains('copy-code-btn')) {
      return;
    }

    // 防止重复点击
    if (target.classList.contains('copied')) {
      return;
    }

    // 优先从按钮所在的代码块容器直接读取，避免依赖可能不稳定的 blockId
    const wrapper = target.closest('.code-block-wrapper');
    let codeEl = wrapper ? wrapper.querySelector('pre code') : null;

    // 兜底：旧版 renderMarkdown 输出的代码块仍带有 data-code
    if (!codeEl) {
      const blockId = target.getAttribute('data-code');
      if (blockId) {
        const selector = `[data-code-id="${blockId.replace(/"/g, '\\"')}"]`;
        codeEl = document.querySelector(selector);
      }
    }

    if (!codeEl) {
      return;
    }

    const encoded = codeEl.getAttribute('data-original-code');
    const content = encoded ? this.decodeHtmlEntities(encoded) : codeEl.textContent || '';

    if (!content.trim()) {
      return;
    }

    try {
      await navigator.clipboard.writeText(content);

      // 添加 copied 类，切换为对勾图标
      target.classList.add('copied');

      // 5秒后恢复
      setTimeout(() => {
        target.classList.remove('copied');
      }, 5000);
    } catch (error) {
      console.warn('复制失败:', error);
    }
  },
  fetchTodoList() {
    return this.fileFetchTodoList();
  },
  fetchSubAgents() {
    return this.subAgentFetch();
  }
};

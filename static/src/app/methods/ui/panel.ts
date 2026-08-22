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

export const panelMethods = {
  toggleSidebar() {
    if (this.isMobileViewport && this.activeMobileOverlay === 'conversation') {
      this.closeMobileOverlay();
      return;
    }
    this.uiToggleSidebar();
  },
  toggleFocusPanel() {
    this.rightCollapsed = !this.rightCollapsed;
    if (!this.rightCollapsed && this.rightWidth < this.minPanelWidth) {
      this.rightWidth = this.minPanelWidth;
    }
  },
  toggleApprovalPanel() {
    this.rightCollapsed = !this.rightCollapsed;
    if (!this.rightCollapsed && this.rightWidth < this.minPanelWidth) {
      this.rightWidth = this.minPanelWidth;
    }
    if (!this.rightCollapsed) {
      this.fetchPendingToolApprovals();
    }
  },
  toggleTerminalPanel() {
    this.terminalPanelOpen = !this.terminalPanelOpen;
    if (this.terminalPanelOpen) {
      this.subscribeTerminalEvents();
    }
  },
  closeTerminalPanel() {
    this.terminalPanelOpen = false;
  },
  handleFocusPanelToggleClick() {
    if (!this.isConnected) {
      return;
    }
    if (this.isPolicyBlocked('block_focus_panel', '聚焦面板已被管理员禁用')) {
      return;
    }
    this.toggleFocusPanel();
  },
  handleApprovalPanelToggleClick() {
    if (!this.currentConversationId) {
      return;
    }
    if (this.isMobileViewport) {
      // 手机端：获取审批列表（不自动关闭），然后切换遮罩
      this.fetchPendingToolApprovals();
      this.openMobileOverlay('approval');
      return;
    }
    this.toggleApprovalPanel();
  },
  handleTokenPanelToggleClick(fromSettingsMenu = false) {
    if (!this.currentConversationId) {
      return;
    }
    if (this.isPolicyBlocked('block_token_panel', '用量统计已被管理员禁用')) {
      return;
    }
    // 移动端禁用“点击展开顶部用量面板”，仅允许在已展开时点击收起
    if (this.isMobileViewport && this.tokenPanelCollapsed && !fromSettingsMenu) {
      return;
    }
    this.toggleTokenPanel();
  },
  getMessagesAreaElement() {
    const ref = this.$refs.messagesArea;
    if (!ref) {
      return null;
    }
    if (ref instanceof HTMLElement) {
      return ref;
    }
    if (ref.rootEl) {
      return ref.rootEl.value || ref.rootEl;
    }
    if (ref.$el && ref.$el.querySelector) {
      const el = ref.$el.querySelector('.messages-area');
      if (el) {
        return el;
      }
    }
    return null;
  },
  getChatAreaController() {
    const ref = this.$refs.messagesArea;
    if (!ref) return null;
    if (typeof ref === 'object') return ref;
    return null;
  },
  getThinkingContentElement(blockId) {
    const chatArea = this.$refs.messagesArea;
    if (chatArea && typeof chatArea.getThinkingRef === 'function') {
      const el = chatArea.getThinkingRef(blockId);
      if (el) {
        return el;
      }
    }
    const refName = `thinkingContent-${blockId}`;
    const elRef = this.$refs[refName];
    if (Array.isArray(elRef)) {
      return elRef[0] || null;
    }
    return elRef || null;
  }
};

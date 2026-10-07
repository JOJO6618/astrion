// @ts-nocheck
import { t } from '@/locales';

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
    this.approvalPanelCollapsed = !this.approvalPanelCollapsed;
    if (!this.approvalPanelCollapsed) this.fetchPendingToolApprovals();
  },
  restoreToolApprovalPanel() {
    this.approvalPanelCollapsed = false;
    if (this.approvalAutoCloseTimer) {
      clearTimeout(this.approvalAutoCloseTimer);
      this.approvalAutoCloseTimer = null;
    }
  },
  collapseToolApprovalPanel() {
    // Presentation only: never submits a decision or changes editor focus.
    this.approvalPanelCollapsed = true;
  },
  toggleTerminalPanel() {
    this.terminalPanelOpen = !this.terminalPanelOpen;
  },
  closeTerminalPanel() {
    this.terminalPanelOpen = false;
  },
  handleFocusPanelToggleClick() {
    if (!this.isConnected) {
      return;
    }
    if (this.isPolicyBlocked('block_focus_panel', t('appUi.policyBlockedFocusPanel'))) {
      return;
    }
    this.toggleFocusPanel();
  },
  handleApprovalPanelToggleClick() {
    this.toggleApprovalPanel();
  },
  handleTokenPanelToggleClick(fromSettingsMenu = false) {
    if (!this.currentConversationId) {
      return;
    }
    if (this.isPolicyBlocked('block_token_panel', t('appUi.policyBlockedTokenPanel'))) {
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

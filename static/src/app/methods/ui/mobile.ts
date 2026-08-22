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

export const mobileMethods = {
  setupMobileViewportWatcher() {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      this.updateMobileViewportState(false);
      return;
    }
    const query = window.matchMedia('(max-width: 768px)');
    this.mobileViewportQuery = query;
    this.updateMobileViewportState(query.matches);
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', this.handleMobileViewportQueryChange);
    } else if (typeof query.addListener === 'function') {
      query.addListener(this.handleMobileViewportQueryChange);
    }
  },
  teardownMobileViewportWatcher() {
    const query = this.mobileViewportQuery;
    if (!query) {
      return;
    }
    if (typeof query.removeEventListener === 'function') {
      query.removeEventListener('change', this.handleMobileViewportQueryChange);
    } else if (typeof query.removeListener === 'function') {
      query.removeListener(this.handleMobileViewportQueryChange);
    }
    this.mobileViewportQuery = null;
  },
  handleMobileViewportQueryChange(event) {
    this.updateMobileViewportState(event.matches);
  },
  updateMobileViewportState(isMobile) {
    this.uiSetMobileViewport(!!isMobile);
    if (!isMobile) {
      this.uiSetMobileOverlayMenuOpen(false);
      this.closeMobileOverlay();
    }
  },
  toggleMobileOverlayMenu() {
    if (!this.isMobileViewport) {
      return;
    }
    this.uiToggleMobileOverlayMenu();
  },
  openMobileOverlay(target) {
    if (!this.isMobileViewport) {
      return;
    }
    if (target === 'approval') {
      this.fetchPendingToolApprovals();
    }
    if (this.activeMobileOverlay === target) {
      this.closeMobileOverlay('same-target-click');
      return;
    }
    if (this.activeMobileOverlay === 'conversation') {
      this.uiSetSidebarCollapsed(true);
    }
    if (target === 'conversation') {
      this.uiSetSidebarCollapsed(false);
    }
    this.uiSetActiveMobileOverlay(target);
    this.uiSetMobileOverlayMenuOpen(false);
  },
  closeMobileOverlay(source = 'unknown') {
    if (!this.activeMobileOverlay) {
      this.uiCloseMobileOverlay();
      return;
    }
    if (this.activeMobileOverlay === 'conversation') {
      this.uiSetSidebarCollapsed(true);
    }
    this.uiCloseMobileOverlay();
  },
  handleMobileOverlayEscape(event) {
    if (event.key !== 'Escape' || !this.isMobileViewport) {
      return;
    }
    if (this.mobileOverlayMenuOpen) {
      this.uiSetMobileOverlayMenuOpen(false);
      return;
    }
    if (this.activeMobileOverlay) {
      this.closeMobileOverlay();
    }
  },
  handleMobileOverlaySelect(conversationId) {
    this.loadConversation(conversationId);
    this.closeMobileOverlay();
  },
  handleMobilePersonalClick() {
    this.closeMobileOverlay();
    this.uiSetMobileOverlayMenuOpen(false);
    this.openPersonalPage();
  },
  handleMobileRefreshClick() {
    this.uiSetMobileOverlayMenuOpen(false);
    this.refreshCurrentPage();
  },
  handleClickOutsideMobileMenu(event) {
    if (!this.isMobileViewport || !this.mobileOverlayMenuOpen) {
      return;
    }
    const trigger = this.$refs.mobilePanelTrigger;
    if (trigger && typeof trigger.contains === 'function' && trigger.contains(event.target)) {
      return;
    }
    this.uiSetMobileOverlayMenuOpen(false);
  }
};

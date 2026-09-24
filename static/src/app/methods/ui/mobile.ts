// @ts-nocheck
import { debugLog } from '../common';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { useQuickDockStore } from '../../../stores/quickDock';
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
      this.closeMobileOverlay();
    }
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
    if (this.activeMobileOverlay === 'quickdock') {
      // 离开快捷窗口悬浮层时关闭其瞬态面板（详情/预览/菜单），避免 fixed 层悬空
      useQuickDockStore().resetTransient();
    }
    if (target === 'conversation') {
      this.uiSetSidebarCollapsed(false);
    }
    this.uiSetActiveMobileOverlay(target);
  },
  closeMobileOverlay(source = 'unknown') {
    if (!this.activeMobileOverlay) {
      this.uiCloseMobileOverlay();
      return;
    }
    if (this.activeMobileOverlay === 'conversation') {
      this.uiSetSidebarCollapsed(true);
    }
    if (this.activeMobileOverlay === 'quickdock') {
      useQuickDockStore().resetTransient();
    }
    this.uiCloseMobileOverlay();
  },
  handleMobileOverlayEscape(event) {
    if (event.key !== 'Escape' || !this.isMobileViewport) {
      return;
    }
    if (this.activeMobileOverlay === 'quickdock') {
      // 快捷窗口悬浮层有自己的 Esc 分层关闭链（菜单 > 详情 > 预览，见 QuickDock.vue）。
      // 两层防御避免一次 Esc 同时关掉瞬态面板和悬浮层：
      // 1. defaultPrevented：QuickDock 处理器先执行并已关闭一层时跳过；
      // 2. store 状态：本处理器先执行时，有瞬态面板开着则让位给 QuickDock。
      if (event.defaultPrevented) {
        return;
      }
      const quickDock = useQuickDockStore();
      if (quickDock.menu || quickDock.detail || quickDock.previewPath) {
        return;
      }
    }
    if (this.activeMobileOverlay) {
      this.closeMobileOverlay('escape');
    }
  },
  handleMobileOverlaySelect(conversationId) {
    this.loadConversation(conversationId);
    this.closeMobileOverlay();
  }
};

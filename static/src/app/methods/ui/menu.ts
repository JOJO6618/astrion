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

export const menuMethods = {
  toggleHeaderMenu() {
    if (!this.isConnected) return;
    this.headerMenuOpen = !this.headerMenuOpen;
    if (this.headerMenuOpen) {
      this.closeQuickMenu();
      this.inputCloseMenus();
    }
  },
  togglePermissionMenu() {
    if (!this.isConnected) {
      return;
    }
    const next = !this.permissionMenuOpen;
    this.permissionMenuOpen = next;
    if (next) {
      this.workModeMenuOpen = false;
    }
  },
  closePermissionMenu() {
    this.permissionMenuOpen = false;
  },
  toggleSettings() {
    if (!this.isConnected) {
      return;
    }
    this.modeMenuOpen = false;
    this.modelMenuOpen = false;
    const nextState = this.inputToggleSettingsMenu();
    if (nextState) {
      this.inputSetToolMenuOpen(false);
      if (!this.quickMenuOpen) {
        this.inputOpenQuickMenu();
      }
    }
  },
  handleClickOutsideQuickMenu(event) {
    if (!this.quickMenuOpen && !this.permissionMenuOpen && !this.agentTypeMenuOpen && !this.workModeMenuOpen) {
      return;
    }
    const shell =
      this.getComposerElement('stadiumShellOuter') || this.getComposerElement('compactInputShell');
    if (shell && shell.contains(event.target)) {
      return;
    }
    this.closeQuickMenu();
    this.closePermissionMenu();
    this.closeWorkModeMenu();
  },
  handleClickOutsideHeaderMenu(event) {
    if (!this.headerMenuOpen) return;
    const ribbon = this.$refs.titleRibbon as HTMLElement | undefined;
    const menu = this.$refs.headerMenu as HTMLElement | undefined;
    if ((ribbon && ribbon.contains(event.target)) || (menu && menu.contains(event.target))) {
      return;
    }
    this.closeHeaderMenu();
  }
};

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

export const gitMethods = {
  startProjectGitSummaryIdleRefresh() {
    if (this.projectGitSummaryRefreshTimer) {
      return;
    }
    this.projectGitSummaryRefreshTimer = window.setInterval(() => {
      if (!this.isConnected) return;
      if (typeof this.isOutputActive === 'function' && this.isOutputActive()) return;
      this.refreshProjectGitSummary?.({ idleFallback: true });
    }, 5000);
  },
  startTerminalCountIdleRefresh() {
    if (this.terminalCountRefreshTimer) return;
    this.terminalCountRefreshTimer = window.setInterval(() => {
      if (!this.isConnected) return;
      this.fetchTerminalCount();
    }, 5000);
  },
  stopTerminalCountIdleRefresh() {
    if (!this.terminalCountRefreshTimer) return;
    window.clearInterval(this.terminalCountRefreshTimer);
    this.terminalCountRefreshTimer = null;
  },
  stopProjectGitSummaryIdleRefresh() {
    if (!this.projectGitSummaryRefreshTimer) {
      return;
    }
    window.clearInterval(this.projectGitSummaryRefreshTimer);
    this.projectGitSummaryRefreshTimer = null;
  },
  async refreshProjectGitSummary() {
    if (this.projectGitSummaryRefreshing) {
      return;
    }
    this.projectGitSummaryRefreshing = true;
    try {
      const resp = await fetch('/api/project/git-summary');
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload?.success) {
        this.projectGitSummary = null;
        this.gitChangesDiff = null;
        return;
      }
      this.projectGitSummary = payload.data || null;
      if (this.gitChangesPanelOpen) {
        this.loadGitChangesDiff?.();
      }
    } catch (_error) {
      this.projectGitSummary = null;
      this.gitChangesDiff = null;
    } finally {
      this.projectGitSummaryRefreshing = false;
    }
  },
  async loadGitChangesDiff() {
    if (!this.gitChangesPanelOpen) return;
    const hasExistingDiff = Boolean(this.gitChangesDiff?.has_git);
    this.gitChangesLoading = true;
    this.gitChangesError = '';
    try {
      const context = Math.max(0, Math.min(200, Number(this.gitChangesContext || 3)));
      const foldContextMap =
        this.gitChangesFoldContexts && typeof this.gitChangesFoldContexts === 'object'
          ? this.gitChangesFoldContexts
          : {};
      const params = new URLSearchParams();
      params.set('context', String(context));
      params.set('_', String(Date.now()));
      if (Object.keys(foldContextMap).length > 0) {
        params.set('fold_context_map', JSON.stringify(foldContextMap));
      }
      const resp = await fetch(
        `/api/project/git-diff?${params.toString()}`,
        { cache: 'no-store' }
      );
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload?.success) {
        this.gitChangesError = payload?.error || t('appUi.loadGitChangesFailed');
        if (!hasExistingDiff) {
          this.gitChangesDiff = null;
        }
        return;
      }
      this.gitChangesDiff = payload.data || null;
      if (!this.gitChangesDiff?.has_git) {
        this.gitChangesPanelOpen = false;
      }
    } catch (error) {
      this.gitChangesError = t('appUi.loadGitChangesFailed');
      if (!hasExistingDiff) {
        this.gitChangesDiff = null;
      }
    } finally {
      this.gitChangesLoading = false;
    }
  },
  openGitChangesPanel() {
    if (!this.projectGitSummary?.has_git) return;
    this.gitChangesPanelOpen = !this.gitChangesPanelOpen;
    if (this.gitChangesPanelOpen) {
      this.gitChangesContext = 3;
      this.gitChangesFileContexts = {};
      this.gitChangesFoldContexts = {};
      this.loadGitChangesDiff?.();
    }
  },
  closeGitChangesPanel() {
    this.gitChangesPanelOpen = false;
  },
  expandGitChangesContext(payload) {
    const path = typeof payload === 'object' ? payload?.path : payload;
    const foldKey = typeof payload === 'object' ? payload?.foldKey : '';
    const filePath = String(path || '').trim();
    const key = String(foldKey || '').trim();
    if (!filePath || !key) return;
    const currentMap =
      this.gitChangesFoldContexts && typeof this.gitChangesFoldContexts === 'object'
        ? this.gitChangesFoldContexts
        : {};
    const fileMap = currentMap[filePath] && typeof currentMap[filePath] === 'object' ? currentMap[filePath] : {};
    const current = Number(fileMap[key] || 0);
    this.gitChangesFoldContexts = {
      ...currentMap,
      [filePath]: {
        ...fileMap,
        [key]: Math.min(200, (Number.isFinite(current) ? current : 0) + 20)
      }
    };
    this.loadGitChangesDiff?.();
  },
  resetGitChangesContext(path) {
    const filePath = String(path || '').trim();
    if (!filePath) return;
    const currentMap =
      this.gitChangesFoldContexts && typeof this.gitChangesFoldContexts === 'object'
        ? this.gitChangesFoldContexts
        : {};
    if (!currentMap[filePath]) {
      return;
    }
    const nextMap = { ...currentMap };
    delete nextMap[filePath];
    this.gitChangesFoldContexts = nextMap;
    this.loadGitChangesDiff?.();
  }
};

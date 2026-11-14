// @ts-nocheck
import { t } from '@/locales';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { conversationViewEpoch } from '../conversation/session';
import { useGitWorktreeStore } from '@/stores/gitWorktree';

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
    const epoch = conversationViewEpoch(this);
    const cid = this.currentConversationId || '';
    const workspaceId = String(this.currentHostWorkspaceId || '');
    const scope = `${workspaceId}:${cid}`;
    const worktreeStore = useGitWorktreeStore();
    const worktreeGeneration = worktreeStore.generation;
    if (
      this.projectGitSummaryRefreshing &&
      this._projectGitRequestScope === scope &&
      this._projectGitRequestGeneration === worktreeGeneration
    )
      return;
    const owner = {};
    this._projectGitRequestOwner = owner;
    this._projectGitRequestScope = scope;
    this._projectGitRequestGeneration = worktreeGeneration;
    const owns = () =>
      this._projectGitRequestOwner === owner &&
      conversationViewEpoch(this) === epoch &&
      worktreeStore.generation === worktreeGeneration;
    this.projectGitSummaryRefreshing = true;
    try {
      const query = cid ? `?conversation_id=${encodeURIComponent(cid)}` : '';
      const resp = await fetch(`/api/project/git-summary${query}`);
      const payload = await resp.json().catch(() => ({}));
      if (!owns()) return;
      if (!resp.ok || !payload?.success) {
        this.projectGitSummary = null;
        this.gitChangesDiff = null;
        return;
      }
      this.projectGitSummary = payload.data || null;
      if (
        cid &&
        worktreeStore.scope === `${workspaceId}:${cid}` &&
        worktreeStore.enabled &&
        !worktreeStore.busy
      ) {
        worktreeStore.branch = payload.data?.branch || '';
        worktreeStore.directory = payload.data?.project_path || '';
        worktreeStore.active = payload.data?.git_worktree?.active === true;
      }
      if (this.gitChangesPanelOpen) {
        this.loadGitChangesDiff?.();
      }
    } catch (_error) {
      if (!owns()) return;
      this.projectGitSummary = null;
      this.gitChangesDiff = null;
    } finally {
      if (this._projectGitRequestOwner === owner) this.projectGitSummaryRefreshing = false;
    }
  },
  async loadGitChangesDiff() {
    if (!this.gitChangesPanelOpen) return;
    const epoch = conversationViewEpoch(this);
    const owner = {};
    this._gitDiffRequestOwner = owner;
    const worktreeGeneration = useGitWorktreeStore().generation;
    const owns = () =>
      this._gitDiffRequestOwner === owner &&
      conversationViewEpoch(this) === epoch &&
      useGitWorktreeStore().generation === worktreeGeneration;
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
      if (this.currentConversationId) params.set('conversation_id', this.currentConversationId);
      params.set('_', String(Date.now()));
      if (Object.keys(foldContextMap).length > 0) {
        params.set('fold_context_map', JSON.stringify(foldContextMap));
      }
      const resp = await fetch(`/api/project/git-diff?${params.toString()}`, { cache: 'no-store' });
      const payload = await resp.json().catch(() => ({}));
      if (!owns()) return;
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
      if (!owns()) return;
      this.gitChangesError = t('appUi.loadGitChangesFailed');
      if (!hasExistingDiff) {
        this.gitChangesDiff = null;
      }
    } finally {
      if (this._gitDiffRequestOwner === owner) this.gitChangesLoading = false;
    }
  },
  openGitChangesPanel() {
    if (!this.projectGitSummary?.has_git) return;
    if (!this.gitChangesPanelOpen) this.markRightPanelsManualOpen();
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
    const fileMap =
      currentMap[filePath] && typeof currentMap[filePath] === 'object' ? currentMap[filePath] : {};
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

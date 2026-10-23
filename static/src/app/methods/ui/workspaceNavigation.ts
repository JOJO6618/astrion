// @ts-nocheck
import { t } from '@/locales';
import { useConversationStore } from '../../../stores/conversation';
import { useConversationTabsStore } from '../../../stores/conversationTabs';
import { useTaskStore } from '../../../stores/task';
import { ownsConversationSession } from '../conversation/session';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { selectWorkspaceIdentity } from './workspaceSelection';
import { refreshNavigationAuxiliary } from './navigationRefresh';
import { requestNewConversationView } from './newConversation';

export function commitWorkspaceIdentity(host, workspaceId, data = {}) {
  const id = String(workspaceId || host.currentHostWorkspaceId || '');
  if (id) {
    host.currentHostWorkspaceId = id;
    useConversationStore().activateWorkspaceCache(id);
    for (const workspace of host.hostWorkspaces || [])
      workspace.is_current = workspace.workspace_id === id;
  }
  if (data.project_path !== undefined) host.projectPath = String(data.project_path || '');
  if (data.default_workspace_id) host.defaultHostWorkspaceId = String(data.default_workspace_id);
  host.searchActive = false;
  host.searchResults = [];
}

function openNavigationTab(host, conversationId, workspaceId) {
  const tabs = useConversationTabsStore();
  if (!tabs.enabled) return;
  const store = useConversationStore();
  const lists = [
    store.getWorkspaceCache(workspaceId).list,
    ...store.workspaceGroups
      .filter((group) => group.workspaceId === workspaceId)
      .map((group) => group.conversations)
  ];
  const summary = lists.flat().find((item) => item?.id === conversationId);
  const workspace = (host.hostWorkspaces || []).find((item) => item.workspace_id === workspaceId);
  tabs.openConversationTab({
    conversationId,
    workspaceId,
    workspaceLabel: workspace?.label || '',
    title: summary?.title || ''
  });
  tabs.persistNow();
}

async function saveSourceDraft(host) {
  // Local capture is synchronous; remote synchronization must not delay navigation.
  void host.persistComposerDraftNow({ reason: 'navigation' }).catch(() => {});
  return Promise.resolve();
}

async function recoverVisibleView(host, failedSession) {
  if (!ownsConversationSession(host, failedSession)) return;
  const id = String(host.currentConversationId || '');
  const workspaceId = String(host.currentHostWorkspaceId || '');
  const session = host.beginConversationView(id, workspaceId);
  const owns = () => ownsConversationSession(host, session);
  try {
    const selection = selectWorkspaceIdentity(host, workspaceId, owns);
    if (id) {
      const result = await host.enterConversation(id, {
        session,
        workspaceId,
        urlMode: 'none',
        source: 'navigation-recovery',
        preserveListPosition: true,
        navigationReady: Promise.all([selection, host.prepareComposerDraftState(workspaceId, id)])
      });
      if (!owns() || result?.superseded) return;
      if (!result?.success) throw new Error(result?.error || t('appUi.switchFailed'));
      refreshNavigationAuxiliary(host, session, id);
    } else {
      await selection;
      if (!owns()) return;
      host.leaveConversationView();
      host.historyLoading = false;
      host.historyLoadingFor = null;
    }
    host.hostWorkspaceSwitching = false;
  } catch (error) {
    if (!owns()) return;
    // Preserve visible content, but block sends until a fresh snapshot binds it.
    host.beginConversationView('', workspaceId);
    host.historyLoading = !!id;
    host.historyLoadingFor = id || null;
    host.hostWorkspaceSwitching = false;
    host.uiPushToast({
      title: t('appMessages.loadConversationErrorTitle'),
      message: String(error?.message || error),
      type: 'error'
    });
  }
}

export const workspaceNavigationMethods = {
  async handleSelectWorkspaceConversation(payload, options = {}) {
    const id = String(payload?.conversationId || '').trim();
    if (!id) return { success: false };
    const scoped = !!(this.versioningHostMode || this.dockerProjectMode);
    const workspaceId = String(payload?.workspaceId || this.currentHostWorkspaceId || '').trim();
    const crossingWorkspace = workspaceId !== String(this.currentHostWorkspaceId || '');
    if (
      !options.force &&
      id === this.currentConversationId &&
      !this.historyLoading &&
      (!scoped || workspaceId === String(this.currentHostWorkspaceId || ''))
    ) {
      this.suppressTitleTyping = false;
      this.titleReady = true;
      return { success: true, skipped: true };
    }
    this.flushReasoningEffortSave?.();
    const draftSave = options.prepared ? Promise.resolve() : saveSourceDraft(this);
    const session = this.beginConversationView(id, workspaceId);
    const owns = () => ownsConversationSession(this, session);
    this.suppressTitleTyping = true;
    this.titleReady = false;
    this.hostWorkspaceSwitching =
      scoped && workspaceId !== String(this.currentHostWorkspaceId || '');
    openNavigationTab(this, id, workspaceId);
    try {
      // Storage/target draft can be read with explicit identity before selection.
      // Selection writes are ordered, so late cookies cannot select an older target.
      const selection =
        !options.prepared && scoped && workspaceId
          ? selectWorkspaceIdentity(this, workspaceId, owns)
          : Promise.resolve({});
      const draft = options.prepared
        ? Promise.resolve()
        : this.prepareComposerDraftState(workspaceId, id);
      const result = await this.enterConversation(id, {
        session,
        workspaceId,
        urlMode: 'push',
        source: 'workspace',
        preserveListPosition: !!options.preserveListPosition,
        navigationReady: options.prepared || Promise.all([selection, draft, draftSave])
      });
      if (!owns() || result?.superseded) return result;
      if (result?.success) {
        refreshNavigationAuxiliary(this, session, result.conversation_id || id);
        if (crossingWorkspace || options.force) {
          void this.loadConversationsList({ background: true, autoEnter: false });
        }
      } else {
        this.suppressTitleTyping = false;
        this.titleReady = true;
        this.uiPushToast({
          title: t('appMessages.loadConversationFailedTitle'),
          message: result?.error || result?.message || t('appMessages.serverNotSuccessMessage'),
          type: 'error'
        });
        await recoverVisibleView(this, session);
      }
      return result;
    } catch (error) {
      if (!owns()) return { success: false, superseded: true };
      this.suppressTitleTyping = false;
      this.titleReady = true;
      this.uiPushToast({
        title: t('appMessages.loadConversationErrorTitle'),
        message: String(error?.message || error),
        type: 'error'
      });
      await recoverVisibleView(this, session);
      return { success: false, error: String(error?.message || error) };
    } finally {
      if (owns()) this.hostWorkspaceSwitching = false;
    }
  },
  async handleHostWorkspaceSwitch(workspaceId, options = {}) {
    const id = String(workspaceId || '').trim();
    if (!id) return;
    const sameWorkspace = id === String(this.currentHostWorkspaceId || '');
    if (!options.newConversation && sameWorkspace && !this.historyLoading) return;
    const previousId =
      options.newConversation && sameWorkspace ? this.currentConversationId || null : null;
    // Capture the departed view before navigation; other workspaces never inherit it.
    const save = saveSourceDraft(this);
    const session = this.beginConversationView('', id);
    const owns = () => ownsConversationSession(this, session);
    this.hostWorkspaceSwitching = true;
    try {
      const [selection, draft, response] = await Promise.all([
        selectWorkspaceIdentity(this, id, owns),
        this.prepareComposerDraftState(id),
        options.newConversation
          ? requestNewConversationView(id, previousId, session.controller.signal)
          : fetch(`/api/workspace-view?workspace_id=${encodeURIComponent(id)}`, {
              signal: session.controller.signal
            }),
        save
      ]);
      if (!owns()) return;
      let view;
      if (options.newConversation) {
        view = response;
      } else {
        const result = await response.json();
        if (!owns()) return;
        if (!response.ok || !result.success)
          throw new Error(result.error || t('appUi.switchFailed'));
        view = result.data || {};
      }
      if (!options.newConversation && view.active_conversation_id) {
        const entered = await this.handleSelectWorkspaceConversation(
          { workspaceId: id, conversationId: view.active_conversation_id },
          {
            force: true,
            prepared: Promise.all([
              Promise.resolve(selection),
              this.prepareComposerDraftState(id, view.active_conversation_id)
            ])
          }
        );
        return entered;
      }
      if (!options.newConversation) {
        view = await requestNewConversationView(id, null, session.controller.signal);
        if (!owns()) return;
      }
      if (this.composerDraftDirty) {
        void this.persistComposerDraftNow({ reason: 'navigation-edits', keepalive: true }).catch(
          () => {}
        );
      }
      commitWorkspaceIdentity(this, id, { ...selection, ...view.workspace });
      this.clearLocalTaskUiState?.('switch-workspace');
      useTaskStore().clearTask();
      this.messages = [];
      this.currentConversationId = null;
      this.currentConversationTitle = t('common.newConversation');
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.startTitleTyping(this.currentConversationTitle, { animate: false });
      this.applyNewConversationView(view);
      this.conversationHasImages = false;
      this.conversationHasVideos = false;
      useConversationStore().setCurrentConversationId(null);
      this.resetTokenStatistics?.();
      this.historyLoading = false;
      this.historyLoadingFor = null;
      this.applyComposerDraftState(draft, id, null);
      this.refreshBlankHeroState();
      history.replaceState({}, '', '/new');
      void this.loadConversationsList({ background: true, autoEnter: false });
      void this.refreshRunningWorkspaceTasks?.();
      void this.refreshProjectGitSummary?.();
      void this.fetchTerminalCount?.();
      this.hostWorkspaceSwitching = false;
    } catch (error) {
      if (!owns()) return;
      this.uiPushToast({
        title: this.versioningHostMode
          ? t('appUi.switchWorkspaceFailed')
          : t('appUi.switchProjectFailed'),
        message: String(error?.message || error),
        type: 'error'
      });
      await recoverVisibleView(this, session);
    } finally {
      if (owns()) {
        this.hostWorkspaceSwitching = false;
        this.historyLoading = false;
        this.historyLoadingFor = null;
      }
    }
  }
};

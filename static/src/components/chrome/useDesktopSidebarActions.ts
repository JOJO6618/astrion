import { ref } from 'vue';
import { chatApi, command, operationId } from '@/chat/api';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import { desktopSidebarBridge } from './desktopSidebarBridge';
import {
  sidebarRequest,
  type DesktopSidebarData,
  type DesktopWorkspace
} from './useDesktopSidebarData';

export function useDesktopSidebarActions(data: DesktopSidebarData) {
  const ui = useUiStore();
  const { tabs, workspaceId, chatMode, taskIndex } = data;
  const workspaceError = ref('');
  const workspaceBusy = ref(false);
  const creatingWorkspace = ref(false);
  const runningConversationActions = new Set<string>();

  function report(error: unknown, title?: string) {
    ui.pushToast({ type: 'error', title, message: String((error as Error)?.message || error) });
  }

  async function openConversation(
    target: { conversationId: string; workspaceId?: string },
    chat = chatMode.value
  ) {
    const id = target.conversationId;
    const workspace = chat ? '' : target.workspaceId || workspaceId.value;
    if (!id || (!chat && !workspace)) return;
    const current = tabs.activeTab;
    if (
      current?.kind === 'conv' &&
      current.conversationId === id &&
      current.workspaceId === workspace &&
      current.conversationKind === (chat ? 'chat' : 'agent')
    )
      return;
    try {
      const result = await tabs.openConversationTab({
        conversationId: id,
        workspaceId: workspace,
        conversationKind: chat ? 'chat' : 'agent'
      });
      if (result?.focus === 'detached' && result.key)
        desktopSidebarBridge()?.focusTab?.(result.key);
    } catch (error) {
      report(error);
    }
  }

  async function openRunningTask(task: {
    task_id: string;
    workspace_id: string;
    conversation_id: string;
  }) {
    await openConversation(
      { conversationId: task.conversation_id, workspaceId: task.workspace_id },
      !task.workspace_id
    );
    if (tabs.activeTab?.conversationId === task.conversation_id)
      taskIndex.refreshWorkspaceTaskIndexIdle();
  }

  function create(workspace = workspaceId.value, chat = chatMode.value) {
    return tabs
      .addNewTab({ workspaceId: workspace, conversationKind: chat ? 'chat' : 'agent' })
      .catch(report);
  }

  async function switchWorkspace(id: string) {
    if (!id || workspaceBusy.value) return;
    workspaceBusy.value = true;
    try {
      const result = await tabs.command('workspace', { workspace_id: id });
      if (result?.focus === 'detached' && result.key)
        desktopSidebarBridge()?.focusTab?.(result.key);
    } catch (error) {
      report(error);
    } finally {
      workspaceBusy.value = false;
    }
  }

  function openPage(path: string) {
    return tabs.openPage(path).catch(report);
  }

  function openGit() {
    if (workspaceId.value)
      void openPage(`/git?workspace_id=${encodeURIComponent(workspaceId.value)}`);
    else ui.pushToast({ type: 'warning', message: t('git.workspaceRequired') });
  }

  async function workspaceAction(path: string, payload: object, errorTitle: string) {
    return sidebarRequest<{ data?: { current_workspace_id?: string } }>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).catch((error) => {
      workspaceError.value = String(error.message || error);
      report(error, errorTitle);
      return null;
    });
  }

  async function changeWorkspace(action: string, payload: object, errorTitle: string) {
    if (workspaceBusy.value || creatingWorkspace.value) return null;
    const creating = action === 'create';
    const busy = creating ? creatingWorkspace : workspaceBusy;
    workspaceError.value = '';
    busy.value = true;
    try {
      const result = await workspaceAction(`/api/host/workspaces/${action}`, payload, errorTitle);
      if (result) await data.refreshCatalog(true);
      return result;
    } finally {
      busy.value = false;
    }
  }

  async function deleteWorkspace(workspace: DesktopWorkspace) {
    const deleted = workspace.workspace_id;
    const active = tabs.activeKey;
    const currentWorkspace = workspaceId.value;
    const result = await changeWorkspace(
      'delete',
      { workspace_id: deleted },
      t('appUi.deleteWorkspaceFailed')
    );
    if (result && currentWorkspace === deleted && tabs.activeKey === active) {
      const next = result.data?.current_workspace_id || data.defaultWorkspace.value;
      if (next && next !== deleted && tabs.activeTab?.kind !== 'page') await switchWorkspace(next);
    }
  }

  async function deleteConversation(target: string | { id: string; workspaceId?: string }) {
    const id = typeof target === 'string' ? target : target.id;
    const chat = chatMode.value;
    const workspace = chat
      ? ''
      : (typeof target === 'string' ? '' : target.workspaceId) || workspaceId.value;
    const key = JSON.stringify([chat, workspace, id]);
    if (!id || runningConversationActions.has(key)) return;
    runningConversationActions.add(key);
    try {
      const confirmed = await ui.requestConfirm({
        title: t(chat ? 'chatMode.delete' : 'appMessages.deleteConversationTitle'),
        message: t(
          chat ? 'chatMode.deleteConfirm' : 'appMessages.deleteConversationConfirmMessage'
        ),
        confirmText: t('common.delete'),
        confirmVariant: 'danger'
      });
      if (!confirmed) return;
      const route = `/conversations/${encodeURIComponent(id)}`;
      if (chat) {
        await chatApi(route, { method: 'DELETE' });
        void tabs.refreshSnapshot().catch(() => {});
      } else {
        const result = await sidebarRequest<{ desktop_navigation?: unknown }>(
          `/api${route}?workspace_id=${encodeURIComponent(workspace)}`,
          { method: 'DELETE' }
        );
        if (result.desktop_navigation && tabs.applyServerSnapshot(result.desktop_navigation))
          tabs.forward(result.desktop_navigation);
      }
      data.refreshConversationList({ chat, workspaceId: workspace });
    } catch (error) {
      report(error, t('appMessages.deleteConversationFailedTitle'));
    } finally {
      runningConversationActions.delete(key);
    }
  }

  async function duplicateConversation(id: string, sourceWorkspace?: string) {
    const chat = chatMode.value;
    const workspace = chat ? '' : sourceWorkspace || workspaceId.value;
    const key = JSON.stringify([chat, workspace, id]);
    if (!id || runningConversationActions.has(key)) return;
    runningConversationActions.add(key);
    try {
      const route = `/conversations/${encodeURIComponent(id)}/duplicate`;
      let conversationId: string;
      if (chat) {
        const result = await command<{ conversation_id: string }>(route, {
          operation_id: operationId()
        });
        conversationId = result.conversation_id;
      } else {
        const result = await sidebarRequest<{ duplicate_conversation_id: string }>(
          `/api${route}?workspace_id=${encodeURIComponent(workspace)}`,
          { method: 'POST' }
        );
        conversationId = result.duplicate_conversation_id;
      }
      data.refreshConversationList({ chat, workspaceId: workspace });
      await openConversation({ conversationId, workspaceId: workspace }, chat);
    } catch (error) {
      report(error, t('appMessages.duplicateConversationFailedTitle'));
    } finally {
      runningConversationActions.delete(key);
    }
  }

  return {
    create,
    switchWorkspace,
    openConversation,
    openRunningTask,
    openPage,
    openGit,
    deleteWorkspace,
    deleteConversation,
    duplicateConversation,
    workspaceError,
    workspaceBusy,
    creatingWorkspace,
    createWorkspace: (payload: { path: string; label: string }) =>
      changeWorkspace('create', payload, t('appUi.createWorkspaceFailed')),
    renameWorkspace: ({ workspaceId: id, label }: { workspaceId: string; label: string }) =>
      changeWorkspace('rename', { workspace_id: id, label }, t('appUi.renameWorkspaceFailed')),
    setDefaultWorkspace: (id: string) =>
      changeWorkspace('set-default', { workspace_id: id }, t('appUi.setDefaultWorkspaceFailed')),
    revealWorkspace: (id: string) =>
      workspaceAction(
        '/api/project/open-in-file-manager',
        { workspace_id: id },
        t('appUi.openWorkspaceFailed')
      )
  };
}

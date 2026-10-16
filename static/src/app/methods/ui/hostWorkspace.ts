// @ts-nocheck
import { t } from '@/locales';
import { beginAuxiliaryRequest } from '../auxiliaryOwnership';
import { useConversationStore } from '../../../stores/conversation';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { currentConversationSession } from '../conversation/session';
import { workspaceNavigationMethods } from './workspaceNavigation';

export const hostWorkspaceMethods = {
  ...workspaceNavigationMethods,
  async fetchHostWorkspaces() {
    const owns = beginAuxiliaryRequest(this, 'host-workspaces');
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      this.hostWorkspaces = [];
      this.currentHostWorkspaceId = '';
      this.defaultHostWorkspaceId = '';
      return;
    }
    try {
      const resp = await fetch(this.versioningHostMode ? '/api/host/workspaces' : '/api/projects');
      const payload = await resp.json().catch(() => ({}));
      if (!owns()) return;
      if (!resp.ok || !payload?.success) {
        throw new Error(payload?.error || t('appUi.fetchProjectsFailed'));
      }
      const data = payload.data || {};
      const items = Array.isArray(data.workspaces) ? data.workspaces : [];
      this.hostWorkspaces = items.map((item: any) => ({
        workspace_id: String(item.workspace_id || ''),
        label: String(item.label || item.workspace_id || t('appUi.unnamedProject')),
        path: String(item.path || ''),
        is_current: !!item.is_current,
        running_task_count: Number(item.running_task_count || 0)
      }));
      // Directory refreshes describe workspaces; the navigation commit owns visible identity.
      if (!this.currentHostWorkspaceId && !currentConversationSession(this)) {
        this.currentHostWorkspaceId = String(data.current_workspace_id || '');
        if (this.currentHostWorkspaceId)
          useConversationStore().activateWorkspaceCache(this.currentHostWorkspaceId);
      }
      for (const workspace of this.hostWorkspaces)
        workspace.is_current = workspace.workspace_id === this.currentHostWorkspaceId;
      this.defaultHostWorkspaceId = String(data.default_workspace_id || '');
      void this.refreshRunningWorkspaceTasks();
    } catch (error) {
      if (!owns()) return;
      console.warn('加载工作区/项目失败:', error);
      this.uiPushToast?.({
        title: t('appUi.fetchProjectsFailed'),
        message: String(error?.message || error),
        type: 'error'
      });
    }
  },
  async submitHostWorkspaceCreate() {
    const owns = beginAuxiliaryRequest(this, 'host-workspace-create');
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    if (this.hostWorkspaceSwitching || this.hostWorkspaceCreateSubmitting) {
      return;
    }
    const workspacePath = String(this.hostWorkspaceCreatePath || '').trim();
    const label = String(this.hostWorkspaceCreateLabel || '').trim();
    if (this.versioningHostMode && !workspacePath) {
      this.hostWorkspaceCreateError = t('appUi.pathCannotBeEmpty');
      return;
    }
    if (this.dockerProjectMode && !label) {
      this.hostWorkspaceCreateError = t('appUi.projectNameCannotBeEmpty');
      return;
    }

    this.hostWorkspaceCreateSubmitting = true;
    this.hostWorkspaceCreateError = '';
    try {
      const resp = await fetch(
        this.versioningHostMode ? '/api/host/workspaces/create' : '/api/projects/create',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            path: this.versioningHostMode ? workspacePath : undefined,
            label
          })
        }
      );
      const payload = await resp.json().catch(() => ({}));
      if (!owns()) return;
      if (!resp.ok || !payload?.success) {
        throw new Error(
          payload?.error ||
            (this.versioningHostMode
              ? t('appUi.createWorkspaceFailed')
              : t('appUi.createProjectFailed'))
        );
      }
      await this.fetchHostWorkspaces();
      const createdLabel = payload?.data?.workspace?.label || label || workspacePath;
      this.hostWorkspaceCreateDialogOpen = false;
      this.hostWorkspaceCreatePath = '';
      this.hostWorkspaceCreateLabel = '';
      this.uiPushToast({
        title: this.versioningHostMode ? t('appUi.workspaceCreated') : t('appUi.projectCreated'),
        message: createdLabel,
        type: 'success'
      });
      // 引导模式下创建首个工作区：后端已自动选中为当前工作区，
      // 补跑一次完整初始化（策略/个性化/模式等），无需用户手动刷新页面
      if (this.workspaceBootstrapActive) {
        this.workspaceBootstrapActive = false;
        await this.loadInitialData();
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error || t('appUi.createFailed'));
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: this.versioningHostMode
          ? t('appUi.createWorkspaceFailed')
          : t('appUi.createProjectFailed'),
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceCreateSubmitting = false;
    }
  },
  async submitHostWorkspaceCreateFromManage(payload = {}) {
    this.hostWorkspaceCreatePath = String(payload?.path || '').trim();
    this.hostWorkspaceCreateLabel = String(payload?.label || '').trim();
    await this.submitHostWorkspaceCreate();
  },
  async renameHostWorkspace(payload = {}) {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    const workspaceId = String(payload?.workspace_id || '').trim();
    const label = String(payload?.label || '').trim();
    if (!workspaceId || !label) {
      this.hostWorkspaceCreateError = this.dockerProjectMode
        ? t('appUi.projectNameCannotBeEmpty')
        : t('appUi.workspaceNameCannotBeEmpty');
      return;
    }
    this.hostWorkspaceManageSubmitting = true;
    this.hostWorkspaceCreateError = '';
    try {
      const endpoint = this.versioningHostMode
        ? '/api/host/workspaces/rename'
        : '/api/projects/rename';
      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ workspace_id: workspaceId, label })
      });
      const result = await resp.json().catch(() => ({}));
      if (!resp.ok || !result?.success) {
        throw new Error(result?.error || t('appUi.renameFailed'));
      }
      await this.fetchHostWorkspaces();
      this.uiPushToast({
        title: this.versioningHostMode ? t('appUi.workspaceRenamed') : t('appUi.projectRenamed'),
        message: label,
        type: 'success'
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error || t('appUi.renameFailed'));
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: this.versioningHostMode
          ? t('appUi.renameWorkspaceFailed')
          : t('appUi.renameProjectFailed'),
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceManageSubmitting = false;
    }
  },
  async handleDeleteWorkspaceFromSwitcher(item) {
    /* 切换浮层已内联确认，跳过全局确认弹窗 */
    await this.deleteHostWorkspace(item, { skipConfirm: true });
  },
  async deleteHostWorkspace(item, opts = {}) {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    const workspaceId = String(item?.workspace_id || '').trim();
    if (!workspaceId) return;
    const wasCurrent = workspaceId === this.currentHostWorkspaceId;
    const ok = opts.skipConfirm
      ? true
      : await this.confirmAction({
          title: this.versioningHostMode
            ? t('appUi.deleteWorkspaceConfirmTitle')
            : t('appUi.deleteProjectConfirmTitle'),
          message: this.versioningHostMode
            ? t('appUi.deleteWorkspaceConfirmMessage', { name: item?.label || workspaceId })
            : t('appUi.deleteProjectConfirmMessage', { name: item?.label || workspaceId }),
          confirmText: t('common.delete'),
          cancelText: t('common.cancel'),
          confirmVariant: 'danger'
        });
    if (!ok) return;

    this.hostWorkspaceManageSubmitting = true;
    this.hostWorkspaceCreateError = '';
    try {
      const endpoint = this.versioningHostMode
        ? '/api/host/workspaces/delete'
        : '/api/projects/delete';
      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ workspace_id: workspaceId })
      });
      const result = await resp.json().catch(() => ({}));
      if (!resp.ok || !result?.success) {
        throw new Error(result?.error || t('appUi.deleteFailed'));
      }
      const data = result.data || {};
      useConversationStore().invalidateWorkspaceCache(workspaceId);
      const leavingCurrent = wasCurrent && workspaceId === this.currentHostWorkspaceId;
      if (leavingCurrent) {
        this.leaveConversationView();
        this.clearLocalTaskUiState?.('delete-host-workspace');
      }
      if (!leavingCurrent && wasCurrent) return;
      this.currentHostWorkspaceId = String(
        data.current_workspace_id || this.currentHostWorkspaceId || ''
      );
      if (data.default_workspace_id) {
        this.defaultHostWorkspaceId = String(data.default_workspace_id);
      }
      if (leavingCurrent) {
        this.messages = [];
        this.currentConversationId = null;
        this.currentConversationTitle = t('common.newConversation');
        this.searchActive = false;
        this.searchResults = [];
        /* Deleted workspace only: keep other workspace caches intact. */
        useConversationStore().activateWorkspaceCache(this.currentHostWorkspaceId || 'default');
        this.conversationsOffset = 0;
        this.hasMoreConversations = false;
        this.conversationsLoading = true;
        history.replaceState({}, '', '/new');
      }
      await this.fetchHostWorkspaces();
      if (leavingCurrent && !currentConversationSession(this)) {
        this.newConversationSettingsReady = false;
        await this.enterNewConversationPage();
      }
      this.uiPushToast({
        title: this.versioningHostMode ? t('appUi.workspaceDeleted') : t('appUi.projectDeleted'),
        message: item?.label || workspaceId,
        type: 'success'
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error || t('appUi.deleteFailed'));
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: this.versioningHostMode
          ? t('appUi.deleteWorkspaceFailed')
          : t('appUi.deleteProjectFailed'),
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceManageSubmitting = false;
    }
  },
  async handleRenameWorkspaceFromSidebar(payload: { workspaceId: string; label: string }) {
    const workspaceId = String(payload?.workspace_id || payload?.workspaceId || '').trim();
    const label = String(payload?.label || '').trim();
    if (!workspaceId || !label) return;
    await this.renameHostWorkspace({ workspace_id: workspaceId, label });
  },
  async handlePinWorkspaceFromSidebar(_workspaceId: string) {
    // 置顶状态由侧边栏组件本地维护并持久化到 localStorage，此处无需额外操作。
    // _workspaceId 为组件事件载荷的签名占位，保留以便未来接入服务端置顶。
    void _workspaceId;
  },
  async revealHostWorkspace(workspaceId: string) {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    const targetId = String(workspaceId || '').trim();
    if (!targetId) return;
    try {
      const resp = await fetch('/api/project/open-in-file-manager', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workspace_id: targetId })
      });
      const result = await resp.json().catch(() => ({}));
      if (!resp.ok || !result?.success) {
        throw new Error(result?.error || t('appUi.openFailed'));
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error || t('appUi.openFailed'));
      this.uiPushToast({
        title: this.versioningHostMode
          ? t('appUi.openWorkspaceFailed')
          : t('appUi.openProjectFailed'),
        message,
        type: 'error'
      });
    }
  },
  async setDefaultHostWorkspace(payload = {}) {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    /* 组件事件契约为 workspaceId 字符串（WorkspaceSwitcher/ConversationSidebar 声明），兼容对象形式 */
    const workspaceId = String(
      typeof payload === 'string' ? payload : payload?.workspace_id || ''
    ).trim();
    if (!workspaceId) return;
    if (workspaceId === this.defaultHostWorkspaceId) return;

    this.hostWorkspaceManageSubmitting = true;
    this.hostWorkspaceCreateError = '';
    const isHostMode = this.versioningHostMode;
    try {
      const endpoint = this.versioningHostMode
        ? '/api/host/workspaces/set-default'
        : '/api/projects/set-default';
      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ workspace_id: workspaceId })
      });
      const result = await resp.json().catch(() => ({}));
      if (!resp.ok || !result?.success) {
        throw new Error(
          result?.error ||
            (isHostMode ? t('appUi.setDefaultWorkspaceFailed') : t('appUi.setDefaultProjectFailed'))
        );
      }
      const data = result.data || {};
      this.defaultHostWorkspaceId = String(data.default_workspace_id || workspaceId);
      await this.fetchHostWorkspaces();
      this.uiPushToast({
        title: isHostMode ? t('appUi.setAsDefaultWorkspace') : t('appUi.setAsDefaultProject'),
        message:
          (this.hostWorkspaces || []).find((w: any) => w.workspace_id === workspaceId)?.label ||
          workspaceId,
        type: 'success'
      });
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : String(
              error ||
                (isHostMode
                  ? t('appUi.setDefaultWorkspaceFailed')
                  : t('appUi.setDefaultProjectFailed'))
            );
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: isHostMode
          ? t('appUi.setDefaultWorkspaceFailed')
          : t('appUi.setDefaultProjectFailed'),
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceManageSubmitting = false;
    }
  }
};

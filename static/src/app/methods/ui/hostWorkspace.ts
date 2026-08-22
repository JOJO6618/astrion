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

export const hostWorkspaceMethods = {
  async fetchHostWorkspaces() {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      this.hostWorkspaces = [];
      this.currentHostWorkspaceId = '';
      this.defaultHostWorkspaceId = '';
      return;
    }
    try {
      const resp = await fetch(this.versioningHostMode ? '/api/host/workspaces' : '/api/projects');
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload?.success) {
        throw new Error(payload?.error || '获取项目列表失败');
      }
      const data = payload.data || {};
      const items = Array.isArray(data.workspaces) ? data.workspaces : [];
      this.hostWorkspaces = items.map((item: any) => ({
        workspace_id: String(item.workspace_id || ''),
        label: String(item.label || item.workspace_id || '未命名项目'),
        path: String(item.path || ''),
        is_current: !!item.is_current,
        running_task_count: Number(item.running_task_count || 0)
      }));
      this.currentHostWorkspaceId = String(data.current_workspace_id || '');
      this.defaultHostWorkspaceId = String(data.default_workspace_id || '');
      await this.refreshRunningWorkspaceTasks();
    } catch (error) {
      console.warn('加载工作区/项目失败:', error);
      this.hostWorkspaces = [];
      this.currentHostWorkspaceId = '';
      this.defaultHostWorkspaceId = '';
      this.runningWorkspaceTasks = [];
    }
  },
  async handleHostWorkspaceSwitch(workspaceId, options = {}) {
    // preserveConversationView：跨工作区点选具体对话时使用——保持当前对话视图冻结，
    // 不做 /new 跳转与状态清空（否则空对话态会让居中输入栏「先往上再往下」），
    // 目标对话由调用方随后立即加载
    const preserveConversationView = Boolean(options && options.preserveConversationView);
    const targetId = String(workspaceId || '').trim();
    if (!targetId || this.hostWorkspaceSwitching) {
      return;
    }
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    if (targetId === this.currentHostWorkspaceId) {
      return;
    }

    this.hostWorkspaceSwitching = true;
    const previousConversationList = Array.isArray(this.conversations) ? [...this.conversations] : [];
    const previousSearchActive = this.searchActive;
    this.searchActive = false;
    this.searchResults = [];
    /* 主列表是当前工作区作用域：切换前使双类型缓存整体失效（conversations 重指空缓存） */
    const { useConversationStore } = await import('../../../stores/conversation');
    useConversationStore().resetConversationsTypeCache();
    this.conversationsOffset = 0;
    this.hasMoreConversations = false;
    this.conversationsLoading = true;
    try {
      // 先在“当前工作区作用域”落盘草稿，再切换工作区。
      // 否则会把旧工作区草稿误写到目标工作区作用域里。
      await this.persistComposerDraftNow({
        reason: 'switch-host-workspace:before-select',
        force: true,
        keepalive: true
      }).catch(() => {});

      const query = encodeURIComponent(targetId);
      const selectEndpoint = this.versioningHostMode ? '/api/host/workspaces/select' : '/api/projects/select';
      const resp = await fetch(`${selectEndpoint}?workspace_id=${query}`);
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload?.success) {
        throw new Error(payload?.error || (this.versioningHostMode ? '切换宿主机工作区失败' : '切换项目失败'));
      }
      const data = payload.data || {};
      this.currentHostWorkspaceId = String(data.current_workspace_id || targetId);
      if (data.project_path) {
        this.projectPath = String(data.project_path);
      }
      if (data.default_workspace_id) {
        this.defaultHostWorkspaceId = String(data.default_workspace_id);
      }
      if (!preserveConversationView) {
        this.messages = [];
        this.currentConversationId = null;
        this.resetTokenStatistics?.();
        this.currentConversationTitle = '新对话';
        this.titleReady = true;
        this.suppressTitleTyping = false;
        try {
          const { useTaskStore } = await import('../../../stores/task');
          const taskStore = useTaskStore();
          taskStore.clearTask();
        } catch (_) {}
        this.clearLocalTaskUiState?.('switch-host-workspace');
      }
      await this.fetchHostWorkspaces();
      if (preserveConversationView) {
        // 不做视图跳转（/new 或自动进入运行中任务对话），仅刷新侧边栏列表
        this.conversationsOffset = 0;
        await this.loadConversationsList();
      } else {
        const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
        const activeTask = (Array.isArray(this.runningWorkspaceTasks) ? this.runningWorkspaceTasks : [])
          .filter((task: any) => String(task?.workspace_id || '') === this.currentHostWorkspaceId)
          .find((task: any) => activeStatuses.has(String(task?.status || '')) && task?.conversation_id);
        if (activeTask?.conversation_id) {
          await this.loadConversation(String(activeTask.conversation_id), { force: true });
          this.conversationsOffset = 0;
          await this.loadConversationsList();
        } else {
          this.startTitleTyping('新对话', { animate: false });
          history.replaceState({}, '', '/new');
          await this.loadConversationsList();
        }
      }
      const switchedWorkspace = (Array.isArray(this.hostWorkspaces) ? this.hostWorkspaces : [])
        .find((item: any) => String(item?.workspace_id || '') === this.currentHostWorkspaceId);
      const switchedLabel = String(switchedWorkspace?.label || '').trim();
      this.uiPushToast({
        title: this.versioningHostMode ? '工作区已切换' : '项目已切换',
        message: this.versioningHostMode
          ? (data.project_path || switchedLabel || targetId)
          : (switchedLabel || targetId),
        type: 'success'
      });
      this.refreshProjectGitSummary?.();
      this.fetchTerminalCount();
    } catch (error) {
      /* 切换失败仍在旧工作区：恢复旧列表并同步回当前类型缓存，保持引用一致 */
      const conversationStore = useConversationStore();
      const restoredCache = conversationStore.conversationsCache[conversationStore.sidebarConversationType];
      restoredCache.list = previousConversationList;
      restoredCache.loaded = true;
      this.conversations = restoredCache.list;
      this.searchActive = previousSearchActive;
      this.conversationsLoading = false;
      const message = error instanceof Error ? error.message : String(error || '切换失败');
      this.uiPushToast({
        title: this.versioningHostMode ? '切换工作区失败' : '切换项目失败',
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceSwitching = false;
    }
  },
  async submitHostWorkspaceCreate() {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    if (this.hostWorkspaceSwitching || this.hostWorkspaceCreateSubmitting) {
      return;
    }
    const workspacePath = String(this.hostWorkspaceCreatePath || '').trim();
    const label = String(this.hostWorkspaceCreateLabel || '').trim();
    if (this.versioningHostMode && !workspacePath) {
      this.hostWorkspaceCreateError = '路径不能为空';
      return;
    }
    if (this.dockerProjectMode && !label) {
      this.hostWorkspaceCreateError = '项目名称不能为空';
      return;
    }

    this.hostWorkspaceCreateSubmitting = true;
    this.hostWorkspaceCreateError = '';
    try {
      const resp = await fetch(this.versioningHostMode ? '/api/host/workspaces/create' : '/api/projects/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          path: this.versioningHostMode ? workspacePath : undefined,
          label
        })
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload?.success) {
        throw new Error(payload?.error || (this.versioningHostMode ? '新建宿主机工作区失败' : '新建项目失败'));
      }
      await this.fetchHostWorkspaces();
      const createdLabel = payload?.data?.workspace?.label || label || workspacePath;
      this.hostWorkspaceCreateDialogOpen = false;
      this.hostWorkspaceCreatePath = '';
      this.hostWorkspaceCreateLabel = '';
      this.uiPushToast({
        title: this.versioningHostMode ? '工作区已创建' : '项目已创建',
        message: createdLabel,
        type: 'success'
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || '新建失败');
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: this.versioningHostMode ? '新建工作区失败' : '新建项目失败',
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
      this.hostWorkspaceCreateError = this.dockerProjectMode ? '项目名称不能为空' : '工作区名称不能为空';
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
        throw new Error(result?.error || '重命名失败');
      }
      await this.fetchHostWorkspaces();
      this.uiPushToast({
        title: this.versioningHostMode ? '工作区已重命名' : '项目已重命名',
        message: label,
        type: 'success'
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || '重命名失败');
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: this.versioningHostMode ? '重命名工作区失败' : '重命名项目失败',
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
    const kindLabel = this.versioningHostMode ? '工作区' : '项目';
    const wasCurrent = workspaceId === this.currentHostWorkspaceId;
    const ok = opts.skipConfirm ? true : await this.confirmAction({
      title: `删除${kindLabel}`,
      message: this.versioningHostMode
        ? `确定要从列表中删除“${item?.label || workspaceId}”吗？不会删除磁盘上的工作区文件夹。`
        : `确定要删除项目“${item?.label || workspaceId}”吗？该项目文件夹和对话记录会被一并删除。`,
      confirmText: '删除',
      cancelText: '取消',
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
        throw new Error(result?.error || '删除失败');
      }
      const data = result.data || {};
      this.currentHostWorkspaceId = String(data.current_workspace_id || this.currentHostWorkspaceId || '');
      if (data.default_workspace_id) {
        this.defaultHostWorkspaceId = String(data.default_workspace_id);
      }
      await this.fetchHostWorkspaces();
      if (wasCurrent) {
        this.messages = [];
        this.currentConversationId = null;
        this.currentConversationTitle = '新对话';
        this.searchActive = false;
        this.searchResults = [];
        /* 当前工作区已删除：双类型缓存整体失效后重新加载 */
        const { useConversationStore } = await import('../../../stores/conversation');
        useConversationStore().resetConversationsTypeCache();
        this.conversationsOffset = 0;
        this.hasMoreConversations = false;
        this.conversationsLoading = true;
        history.replaceState({}, '', '/new');
        await this.loadConversationsList();
      }
      this.uiPushToast({
        title: `${kindLabel}已删除`,
        message: item?.label || workspaceId,
        type: 'success'
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || '删除失败');
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: `删除${kindLabel}失败`,
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceManageSubmitting = false;
    }
  },
  async handleSelectWorkspaceConversation(payload: { conversationId: string; workspaceId: string }) {
    const { conversationId, workspaceId } = payload || {};
    if (!conversationId || !workspaceId) return;
    if ((this.versioningHostMode || this.dockerProjectMode) && workspaceId !== this.currentHostWorkspaceId) {
      await this.handleHostWorkspaceSwitch(workspaceId, { preserveConversationView: true });
    }
    await this.loadConversation(conversationId, { force: true, workspaceId });
  },
  async handleRenameWorkspaceFromSidebar(payload: { workspaceId: string; label: string }) {
    const workspaceId = String(payload?.workspace_id || payload?.workspaceId || '').trim();
    const label = String(payload?.label || '').trim();
    if (!workspaceId || !label) return;
    await this.renameHostWorkspace({ workspace_id: workspaceId, label });
  },
  async handlePinWorkspaceFromSidebar(_workspaceId: string) {
    // 置顶状态由侧边栏组件本地维护并持久化到 localStorage，此处无需额外操作。
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
        throw new Error(result?.error || '打开失败');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || '打开失败');
      this.uiPushToast({
        title: this.versioningHostMode ? '打开工作区失败' : '打开项目失败',
        message,
        type: 'error'
      });
    }
  },
  async setDefaultHostWorkspace(payload = {}) {
    if (!(this.versioningHostMode || this.dockerProjectMode)) {
      return;
    }
    const workspaceId = String(payload?.workspace_id || '').trim();
    if (!workspaceId) return;
    if (workspaceId === this.defaultHostWorkspaceId) return;

    this.hostWorkspaceManageSubmitting = true;
    this.hostWorkspaceCreateError = '';
    const kindLabel = this.versioningHostMode ? '工作区' : '项目';
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
        throw new Error(result?.error || `设置默认${kindLabel}失败`);
      }
      const data = result.data || {};
      this.defaultHostWorkspaceId = String(data.default_workspace_id || workspaceId);
      await this.fetchHostWorkspaces();
      this.uiPushToast({
        title: `已设为默认${kindLabel}`,
        message: (this.hostWorkspaces || []).find((w: any) => w.workspace_id === workspaceId)?.label || workspaceId,
        type: 'success'
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || `设置默认${kindLabel}失败`);
      this.hostWorkspaceCreateError = message;
      this.uiPushToast({
        title: `设置默认${kindLabel}失败`,
        message,
        type: 'error'
      });
    } finally {
      this.hostWorkspaceManageSubmitting = false;
    }
  }
};

// @ts-nocheck
import { debugLog, traceLog } from '../common';
import { usePersonalizationStore } from '../../../stores/personalization';
import { persistNewConversationType } from '../../state';
import {

} from './shared';

export const actionMethods = {
  promoteConversationToTop(conversationId) {
    if (!Array.isArray(this.conversations) || !conversationId) {
      return;
    }
    const index = this.conversations.findIndex((conv) => conv && conv.id === conversationId);
    if (index > 0) {
      const [selected] = this.conversations.splice(index, 1);
      this.conversations.unshift(selected);
    }
  },
  async createNewConversation() {
    // 已移除「压缩中需确认并取消压缩」拦截：多对话独立运行后，某对话压缩中
    // 不影响新建/切换其他对话（压缩锁已按对话隔离）。
    debugLog('创建新对话...');
    traceLog('createNewConversation:start', {
      currentConversationId: this.currentConversationId,
      convCount: Array.isArray(this.conversations) ? this.conversations.length : 'n/a'
    });
    this.logMessageState('createNewConversation:start');

    // 新建对话前把 debounce 中的推理强度保存立即落盘（存到原对话）
    this.flushReasoningEffortSave?.();

    await this.persistComposerDraftNow({
      reason: 'create-new-conversation',
      force: true,
      keepalive: true
    }).catch(() => {});

    // 按个性化设置分流：route-跳转空白新对话页（发送首条消息时才真正创建）；
    // blank-保持现有行为，立即创建空对话
    let newChatBehavior = 'route';
    try {
      newChatBehavior = usePersonalizationStore()?.form?.new_chat_button_behavior || 'route';
    } catch (error) {
      console.warn('读取新建对话按钮行为设置失败，按默认跳转处理:', error);
    }
    if (newChatBehavior !== 'blank') {
      // 跳转 /new 前把侧边栏过滤器类型同步给输入栏选择器：
      // 看着哪类对话列表点新建，默认就创建哪类（选择器仍可手动改）。
      try {
        const { useConversationStore } = await import('../../../stores/conversation');
        this.newConversationType =
          useConversationStore().sidebarConversationType === 'multi_agent' ? 'multi_agent' : 'agent';
        persistNewConversationType(this.newConversationType);
      } catch (_e) {
        // ignore
      }
      const target = '/new';
      const normalize = (p) => String(p || '/').replace(/^\/+|\/+$/g, '');
      if (normalize(window.location.pathname) !== normalize(target)) {
        window.location.href = target;
      }
      return;
    }

    // 应用个性化设置中的默认模型和思考模式
    try {
      const personalizationStore = usePersonalizationStore();

      if (personalizationStore.loaded) {
        const defaultRunMode = personalizationStore.form.default_run_mode;
        const defaultModel = personalizationStore.form.default_model;
        const defaultReasoningEffort = personalizationStore.form.default_reasoning_effort;

        if (defaultRunMode) {
          this.runMode = defaultRunMode;
          debugLog('应用默认运行模式:', defaultRunMode);
        }

        this.reasoningEffort = defaultReasoningEffort;

        if (defaultModel) {
          this.currentModelKey = defaultModel;
          debugLog('应用默认模型:', defaultModel);
        }

        // 根据默认运行模式设置思考模式
        if (defaultRunMode === 'thinking') {
          this.thinkingMode = true;
        } else if (defaultRunMode === 'fast') {
          this.thinkingMode = false;
        }
      }
    } catch (error) {
      console.warn('应用个性化默认设置失败:', error);
    }

    // 创建新对话只切换视图，不再取消后台任务；停止当前轮询避免事件串写。
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      if (taskStore.hasActiveTask || this.taskInProgress) {
        taskStore.clearTask();
        if (typeof this.clearProcessedEvents === 'function') {
          this.clearProcessedEvents();
        }
      }
      this.clearLocalTaskUiState?.('create-new-conversation');
    } catch (error) {
      console.error('[创建新对话] 停止本地轮询失败:', error);
    }

    let backupToastId = null;
    try {
      const personalizationStore = usePersonalizationStore();
      const shouldShowBackupToast =
        this.versioningHostMode &&
        personalizationStore.form.versioning_enabled_by_default &&
        personalizationStore.form.versioning_backup_mode === 'full';
      if (shouldShowBackupToast) {
        backupToastId = this.uiPushToast({
          title: '正在初始化备份',
          message: '正在创建完整工作区快照，请稍候…',
          type: 'info',
          duration: null,
          closable: false
        });
        this.versioningInitializingBackupToastId = backupToastId;
      }

      // 侧边栏新建：类型跟随侧边栏过滤器（看着哪类列表就建哪类对话）
      const { useConversationStore } = await import('../../../stores/conversation');
      const isMultiAgent = useConversationStore().sidebarConversationType === 'multi_agent';
      const createUrl = isMultiAgent ? '/api/multiagent/conversations' : '/api/conversations';
      // reasoning_effort 随创建权威写入新对话 meta（此处 this.reasoningEffort 已被上方
      // 重置为个性化默认值，语义与“创建空对话写入默认值一次”一致）
      const createBody = isMultiAgent
        ? JSON.stringify({ preserve_mode: true, thinking_mode: this.thinkingMode, mode: this.runMode, reasoning_effort: this.reasoningEffort })
        : JSON.stringify({ thinking_mode: this.thinkingMode, mode: this.runMode, reasoning_effort: this.reasoningEffort });
      const response = await fetch(createUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: createBody
      });

      const result = await response.json();

      if (result.success) {
        const newConversationId = result.conversation_id;
        const waitForAnimation = (durationMs) =>
          new Promise((resolve) => {
            window.setTimeout(resolve, durationMs);
          });
        debugLog('新对话创建成功:', newConversationId);
        traceLog('createNewConversation:created', { newConversationId });

        // 在本地列表插入占位，避免等待刷新
        const placeholder = {
          id: newConversationId,
          title: '新对话',
          updated_at: new Date().toISOString(),
          total_messages: 0,
          total_tools: 0
        };
        this.conversationInsertAnimations = {
          ...this.conversationInsertAnimations,
          [newConversationId]: 'create'
        };
        this.conversationListAnimationMode = 'create';
        /* 原地 splice 保持数组引用：conversations 与双类型缓存中当前类型列表同一引用 */
        this.conversations.splice(
          0,
          this.conversations.length,
          placeholder,
          ...this.conversations.filter((conv) => conv && conv.id !== newConversationId)
        );

        // 分组视图下同步在当前工作区分组顶部插入占位
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          const conversationStore = useConversationStore();
          const currentWorkspaceId = this.currentHostWorkspaceId;
          if (currentWorkspaceId) {
            conversationStore.ensureWorkspaceGroup(currentWorkspaceId);
            const group = conversationStore.workspaceGroups.find(
              (g: any) => g.workspaceId === currentWorkspaceId
            );
            if (group) {
              group.conversations.splice(
                0,
                group.conversations.length,
                placeholder,
                ...group.conversations.filter((conv: any) => conv.id !== newConversationId)
              );
              group.expanded = true;
              group.visibleOffset = 0;
            }
          }
        } catch (_err) {
          // ignore
        }

        window.setTimeout(() => {
          const rest = { ...this.conversationInsertAnimations };
          delete rest[newConversationId];
          this.conversationInsertAnimations = rest;
          this.conversationListAnimationMode = 'idle';
        }, 700);

        // 等待插入动画先完成一帧，避免立即加载对话导致列表闪烁/卡顿。
        // 工作区旁新建不切换对话，所以不会触发此处的状态风暴；顶部新建需要切对话，
        // 但把 loadConversation 放到入场动画基本结束后再执行，动画就和工作区旁一致。
        await waitForAnimation(540);

        // 直接加载新对话，确保状态一致
        // 如果 socket 事件已把 currentConversationId 设置为新ID，则强制加载一次以同步状态
        await this.loadConversation(newConversationId, { force: true });
        traceLog('createNewConversation:after-load', {
          newConversationId,
          currentConversationId: this.currentConversationId
        });

        // 刷新对话列表获取最新统计
        this.conversationsOffset = 0;
        await this.loadConversationsList();
        traceLog('createNewConversation:after-refresh', {
          newConversationId,
          conversationsLen: Array.isArray(this.conversations) ? this.conversations.length : 'n/a'
        });

        // 分组视图下刷新当前工作区数据，用 refresh 模式避免清空已加载内容导致闪烁
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          const conversationStore = useConversationStore();
          const currentWorkspaceId = this.currentHostWorkspaceId;
          if (currentWorkspaceId) {
            await conversationStore.loadWorkspaceConversations(currentWorkspaceId, { refresh: true });
          }
        } catch (_err) {
          // ignore
        }

        await this.refreshRunningWorkspaceTasks?.();
      } else {
        console.error('创建对话失败:', result.message);
        this.uiPushToast({
          title: '创建对话失败',
          message: result.message || '服务器未返回成功状态',
          type: 'error'
        });
      }
    } catch (error) {
      console.error('创建对话异常:', error);
      this.uiPushToast({
        title: '创建对话异常',
        message: error.message || String(error),
        type: 'error'
      });
    } finally {
      if (backupToastId) {
        this.uiDismissToast(backupToastId);
        this.versioningInitializingBackupToastId = null;
      }
    }
  },
  async createWorkspaceConversation(workspaceId: string) {
    if (!workspaceId) return;
    debugLog('在指定工作区创建新对话:', workspaceId);
    try {
      const { useConversationStore } = await import('../../../stores/conversation');
      const conversationStore = useConversationStore();
      // 项目旁新建：类型跟随侧边栏过滤器（看着哪类列表就建哪类对话）
      const isMultiAgent = conversationStore.sidebarConversationType === 'multi_agent';
      const createUrl = isMultiAgent ? '/api/multiagent/conversations' : '/api/conversations';
      const createPayload = isMultiAgent
        ? { workspace_id: workspaceId, preserve_mode: true }
        : { workspace_id: workspaceId };
      const response = await fetch(createUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(createPayload)
      });
      const result = await response.json();
      if (result.success) {
        const newConversationId = result.conversation_id;
        debugLog('工作区新对话创建成功:', newConversationId);
        const placeholder = {
          id: newConversationId,
          title: '新对话',
          updated_at: new Date().toISOString(),
          total_messages: 0,
          total_tools: 0
        };

        // 统一播放“整体下移 + 新项从左侧滑入”的动画
        this.conversationInsertAnimations = {
          ...this.conversationInsertAnimations,
          [newConversationId]: 'create'
        };
        this.conversationListAnimationMode = 'create';
        window.setTimeout(() => {
          const rest = { ...this.conversationInsertAnimations };
          delete rest[newConversationId];
          this.conversationInsertAnimations = rest;
          this.conversationListAnimationMode = 'idle';
        }, 700);

        // 立即在分组侧边栏插入占位，保证用户能立刻看到
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          const conversationStore = useConversationStore();
          conversationStore.ensureWorkspaceGroup(workspaceId);
          const group = conversationStore.workspaceGroups.find((g: any) => g.workspaceId === workspaceId);
          if (group) {
            group.conversations.splice(
              0,
              group.conversations.length,
              placeholder,
              ...group.conversations.filter((conv: any) => conv.id !== newConversationId)
            );
            group.expanded = true;
            group.visibleOffset = 0;
          }
          // 延迟刷新该工作区列表以获取真实数据，用 refresh 模式保留占位避免闪烁
          window.setTimeout(() => {
            conversationStore.loadWorkspaceConversations(workspaceId, { refresh: true }).catch(() => {});
          }, 300);
        } catch (_err) {
          // ignore
        }
      } else {
        console.error('创建对话失败:', result.message);
        this.uiPushToast({
          title: '创建对话失败',
          message: result.message || '服务器未返回成功状态',
          type: 'error'
        });
      }
    } catch (error) {
      console.error('创建工作区对话异常:', error);
      this.uiPushToast({
        title: '创建工作区对话异常',
        message: error.message || String(error),
        type: 'error'
      });
    }
  },
  async deleteConversation(payload) {
    const conversationId = typeof payload === 'string' ? payload : payload?.id;
    const workspaceId = typeof payload === 'object' ? payload?.workspaceId : undefined;
    if (!conversationId) return;
    const confirmed = await this.confirmAction({
      title: '删除对话',
      message: '确定要删除这个对话吗？删除后无法恢复。',
      confirmText: '删除',
      cancelText: '取消'
    });
    if (!confirmed) {
      return;
    }

    const waitForAnimation = (durationMs) =>
      new Promise((resolve) => {
        window.setTimeout(resolve, durationMs);
      });

    // 确认弹窗自身有 250ms 退出动画；等弹窗完全消失后，再开始列表删除动画。
    await waitForAnimation(270);

    debugLog('删除对话:', conversationId);
    this.logMessageState('deleteConversation:start', { conversationId });

    try {
      const deleteUrl = workspaceId
        ? `/api/conversations/${conversationId}?workspace_id=${encodeURIComponent(workspaceId)}`
        : `/api/conversations/${conversationId}`;
      const response = await fetch(deleteUrl, {
        method: 'DELETE'
      });

      const result = await response.json();

      if (result.success) {
        debugLog('对话删除成功');

        // 如果删除的是当前对话，清空界面
        if (conversationId === this.currentConversationId) {
          this.logMessageState('deleteConversation:before-clear', { conversationId });
          this.messages = [];
          this.logMessageState('deleteConversation:after-clear', { conversationId });
          this.currentConversationId = null;
          this.currentConversationTitle = '';
          this.resetAllStates(`deleteConversation:${conversationId}`);
          this.resetTokenStatistics();
          history.replaceState({}, '', '/new');
        }

        // 先给目标项加 deleting 类执行左滑动画，再从本地列表移除。
        // 这比完全依赖 transition-group leave 更稳定：即使列表即将为空或分支切换，也能先播放离场动画。
        this.conversationListAnimationMode = 'delete';
        const deletingIds = Array.isArray(this.pendingDeletingConversationIds)
          ? this.pendingDeletingConversationIds
          : [];
        if (!deletingIds.includes(conversationId)) {
          this.pendingDeletingConversationIds = [...deletingIds, conversationId];
        }
        await this.$nextTick();
        // 分组侧边栏使用 transition-group 离场动画，立即从列表移除
        try {
          const { useConversationStore } = await import('../../../stores/conversation');
          const conversationStore = useConversationStore();
          conversationStore.workspaceGroups.forEach((group: any) => {
            group.conversations.splice(
              0,
              group.conversations.length,
              ...group.conversations.filter((conv: any) => conv.id !== conversationId)
            );
            const maxVisibleOffset = Math.max(0, group.conversations.length - group.visibleLimit);
            if (group.visibleOffset > maxVisibleOffset) {
              group.visibleOffset = maxVisibleOffset;
            }
          });
        } catch (_err) {
          // ignore
        }
        await waitForAnimation(430);

        this.conversations.splice(
          0,
          this.conversations.length,
          ...this.conversations.filter((conversation) => conversation.id !== conversationId)
        );
        this.searchResults = this.searchResults.filter(
          (conversation) => conversation.id !== conversationId
        );
        // 跨工作区搜索结果分组：同步剔除被删对话，空组整体移除
        this.searchGroups = (Array.isArray(this.searchGroups) ? this.searchGroups : [])
          .map((group: any) => ({
            ...group,
            results: (group.results || []).filter((conv: any) => conv.id !== conversationId)
          }))
          .filter((group: any) => group.results.length > 0);
        this.pendingDeletingConversationIds = (Array.isArray(this.pendingDeletingConversationIds)
          ? this.pendingDeletingConversationIds
          : []
        ).filter((id) => id !== conversationId);

        // 等待“左滑离场 + 0.1s 后集体上移”动画结束，避免刷新列表打断过渡。
        await waitForAnimation(180);

        // 刷新对话列表
        this.conversationsOffset = 0;
        await this.loadConversationsList();
        this.conversationListAnimationMode = 'idle';
      } else {
        console.error('删除对话失败:', result.message);
        this.uiPushToast({
          title: '删除对话失败',
          message: result.message || '服务器未返回成功状态',
          type: 'error'
        });
      }
    } catch (error) {
      this.pendingDeletingConversationIds = (Array.isArray(this.pendingDeletingConversationIds)
        ? this.pendingDeletingConversationIds
        : []
      ).filter((id) => id !== conversationId);
      this.conversationListAnimationMode = 'idle';
      console.error('删除对话异常:', error);
      this.uiPushToast({
        title: '删除对话异常',
        message: error.message || String(error),
        type: 'error'
      });
    }
  },
  async duplicateConversation(conversationId) {
    debugLog('复制对话:', conversationId);
    try {
      const response = await fetch(`/api/conversations/${conversationId}/duplicate`, {
        method: 'POST'
      });

      const result = await response.json();

      if (response.ok && result.success) {
        const newId = result.duplicate_conversation_id;
        const waitForAnimation = (durationMs) =>
          new Promise((resolve) => {
            window.setTimeout(resolve, durationMs);
          });
        if (newId) {
          const sourceIndex = this.conversations.findIndex(
            (conv) => conv && conv.id === conversationId
          );
          const sourceConversation = sourceIndex >= 0 ? this.conversations[sourceIndex] : null;
          const duplicateTitle =
            result.load_result?.title || sourceConversation?.title || '复制的对话';
          const duplicatePlaceholder = {
            id: newId,
            title: duplicateTitle,
            updated_at: new Date().toISOString(),
            total_messages: sourceConversation?.total_messages || 0,
            total_tools: sourceConversation?.total_tools || 0
          };

          this.conversationInsertAnimations = {
            ...this.conversationInsertAnimations,
            [conversationId]: 'duplicateSource',
            [newId]: 'duplicate'
          };
          this.conversationListAnimationMode = 'duplicate';

          const withoutDuplicate = this.conversations.filter((conv) => conv && conv.id !== newId);
          const insertIndex = withoutDuplicate.findIndex(
            (conv) => conv && conv.id === conversationId
          );
          if (insertIndex >= 0) {
            this.conversations.splice(
              0,
              this.conversations.length,
              ...withoutDuplicate.slice(0, insertIndex + 1),
              duplicatePlaceholder,
              ...withoutDuplicate.slice(insertIndex + 1)
            );
          } else {
            this.conversations.splice(0, this.conversations.length, duplicatePlaceholder, ...withoutDuplicate);
          }

          window.setTimeout(() => {
            const rest = { ...this.conversationInsertAnimations };
            delete rest[conversationId];
            delete rest[newId];
            this.conversationInsertAnimations = rest;
            this.conversationListAnimationMode = 'idle';
          }, 860);

          // 同步插入分组侧边栏对应工作区
          try {
            const { useConversationStore } = await import('../../../stores/conversation');
            const conversationStore = useConversationStore();
            const group = conversationStore.workspaceGroups.find((g: any) =>
              g.conversations.some((conv: any) => conv.id === conversationId)
            );
            if (group) {
              const sourceGroupIndex = group.conversations.findIndex((conv: any) => conv.id === conversationId);
              const withoutDuplicate = group.conversations.filter((conv: any) => conv.id !== newId);
              if (sourceGroupIndex >= 0) {
                group.conversations.splice(
                  0,
                  group.conversations.length,
                  ...withoutDuplicate.slice(0, sourceGroupIndex + 1),
                  duplicatePlaceholder,
                  ...withoutDuplicate.slice(sourceGroupIndex + 1)
                );
              } else {
                group.conversations.splice(0, group.conversations.length, duplicatePlaceholder, ...withoutDuplicate);
              }
              group.expanded = true;
            }
          } catch (_err) {
            // ignore
          }

          // 先让“目标下方钻出 + 下方整体下移”动画播完，再加载复制出的对话。
          await waitForAnimation(860);
          await this.loadConversation(newId, { force: true, preserveListPosition: true });
        }
      } else {
        const message = result.message || result.error || '复制失败';
        this.uiPushToast({
          title: '复制对话失败',
          message,
          type: 'error'
        });
      }
    } catch (error) {
      console.error('复制对话异常:', error);
      this.uiPushToast({
        title: '复制对话异常',
        message: error.message || String(error),
        type: 'error'
      });
    }
  }
};

// @ts-nocheck
import { debugLog, traceLog } from '../common';
import { t } from '@/locales';
import { usePersonalizationStore } from '../../../stores/personalization';
import {

} from './shared';

export const loadMethods = {
  /**
   * 侧边栏对话类型（普通/多智能体）切换：
   * 重置平铺列表分页并重新加载，同时刷新所有已展开的工作区分组。
   * 由 ConversationSidebar 的 conversation-type-change 事件触发；
   * store 中的 sidebarConversationType 已由组件先行写入。
   */
  /**
   * 侧边栏对话类型（普通/多智能体）切换后的补载：
   * 列表引用已由 store setSidebarConversationType 同步交换（零请求切换），
   * 此处仅兜底「目标类型从未加载过」的场景（如登录后首次直接切换），
   * 按需补载当前类型的主列表与工作区分组首页。
   */
  async handleSidebarConversationTypeChange(_type?: 'normal' | 'multi_agent') {
    const { useConversationStore } = await import('../../../stores/conversation');
    const conversationStore = useConversationStore();
    const listType = conversationStore.sidebarConversationType;
    if (!conversationStore.conversationsCache[listType]?.loaded) {
      this.conversationsOffset = 0;
      this.hasMoreConversations = false;
      await this.loadConversationsList();
    }
    try {
      const groups = Array.isArray(conversationStore.workspaceGroups)
        ? conversationStore.workspaceGroups
        : [];
      for (const group of groups) {
        if (group && group.workspaceId && !group.pagingByType?.[listType]?.loaded) {
          await conversationStore.loadWorkspaceConversations(group.workspaceId);
        }
      }
    } catch (error) {
      console.error('补载工作区分组对话失败:', error);
    }
  },

  async loadConversationsList() {
    const queryOffset = this.conversationsOffset;
    const queryLimit = this.conversationsLimit;
    const { useConversationStore } = await import('../../../stores/conversation');
    const conversationStore = useConversationStore();
    /* 锁定发起时类型：响应期间用户可能已切换过滤器，数据始终写入对应类型缓存 */
    const listType = conversationStore.sidebarConversationType;
    const refreshToken =
      queryOffset === 0 ? ++this.conversationListRefreshToken : this.conversationListRefreshToken;
    const requestSeq = ++this.conversationListRequestSeq;
    this.conversationsLoading = true;
    try {
      // 列表过滤由请求发起时的侧边栏类型过滤器（普通/多智能体）决定
      const maParam = listType === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
      const response = await fetch(`/api/conversations?limit=${queryLimit}&offset=${queryOffset}${maParam}`);
      const data = await response.json();

      if (data.success) {
        if (refreshToken < this.conversationListRefreshToken) {
          debugLog('忽略已过期的对话列表响应', {
            requestSeq,
            responseOffset: queryOffset,
            refreshToken,
            currentRefreshToken: this.conversationListRefreshToken
          });
          return;
        }

        /* 原地写入该类型缓存（当前显示类型的缓存与 conversations 同一引用，原地修改即同步显示） */
        const cache = conversationStore.conversationsCache[listType];
        const items = data.data.conversations;
        if (queryOffset === 0) {
          cache.list.splice(0, cache.list.length, ...items);
        } else {
          cache.list.push(...items);
        }
        cache.offset = queryOffset;
        cache.hasMore = data.data.has_more;
        cache.loaded = true;

        /* 仅仍是当前显示类型时同步扁平字段与首对话自动加载 */
        if (listType === conversationStore.sidebarConversationType) {
          this.conversations = cache.list;
          this.hasMoreConversations = cache.hasMore;
          if (this.currentConversationId) {
            this.promoteConversationToTop(this.currentConversationId);
          }
          if (
            queryOffset === 0 &&
            !this.currentConversationId &&
            cache.list.length > 0 &&
            !this.isExplicitNewConversationRoute() &&
            !this.isConversationIndependentRoute()
          ) {
            // 只有在初始化完成后，才自动加载第一个对话
            // 避免与 bootstrapRoute 冲突
            if (this.initialRouteResolved) {
              const latestConversation = cache.list[0];
              if (latestConversation && latestConversation.id) {
                await this.loadConversation(latestConversation.id);
              }
            }
          }
        }
        debugLog(`已加载 ${cache.list.length} 个对话`);

        /* 首页加载成功后后台补载另一类型，保证切换过滤器时零等待 */
        const otherType = listType === 'multi_agent' ? 'normal' : 'multi_agent';
        if (queryOffset === 0 && !conversationStore.conversationsCache[otherType].loaded) {
          this.loadConversationTypeCache(otherType).catch(() => {});
        }
      } else {
        console.error('加载对话列表失败:', data.error);
      }
    } catch (error) {
      console.error('加载对话列表异常:', error);
    } finally {
      if (refreshToken === this.conversationListRefreshToken) {
        this.conversationsLoading = false;
      }
    }
  },

  /** 后台补载指定类型的首页列表缓存：已加载则跳过；refreshToken 快照防 reset/刷新后旧响应污染 */
  async loadConversationTypeCache(type: 'normal' | 'multi_agent') {
    const { useConversationStore } = await import('../../../stores/conversation');
    const conversationStore = useConversationStore();
    const cache = conversationStore.conversationsCache[type];
    if (!cache || cache.loaded) return;
    const tokenAtStart = this.conversationListRefreshToken;
    const maParam = type === 'multi_agent' ? '&multi_agent_mode=1' : '&multi_agent_mode=0';
    try {
      const response = await fetch(
        `/api/conversations?limit=${this.conversationsLimit}&offset=0${maParam}`
      );
      const data = await response.json();
      if (!data?.success) return;
      /* reset/刷新后旧响应丢弃；并发补载去重 */
      if (tokenAtStart !== this.conversationListRefreshToken || cache.loaded) return;
      const items = data.data?.conversations || [];
      cache.list.push(...items);
      cache.offset = 0;
      cache.hasMore = !!data.data?.has_more;
      cache.loaded = true;
    } catch (error) {
      console.error('补载对话列表缓存异常:', error);
    }
  },
  async loadMoreConversations() {
    if (this.loadingMoreConversations || !this.hasMoreConversations) return;

    this.loadingMoreConversations = true;
    this.conversationsOffset += this.conversationsLimit;
    await this.loadConversationsList();
    this.loadingMoreConversations = false;
  },
  async loadConversation(conversationId, options = {}) {
    const force = Boolean(options.force);
    const preserveListPosition = Boolean(options.preserveListPosition);
    const workspaceId = options.workspaceId ? String(options.workspaceId) : '';
    debugLog('加载对话:', conversationId);
    traceLog('loadConversation:start', {
      conversationId,
      currentConversationId: this.currentConversationId,
      force
    });
    this.logMessageState('loadConversation:start', { conversationId, force });
    this.suppressTitleTyping = true;
    this.titleReady = false;
    this.currentConversationTitle = '';
    this.titleTypingText = '';

    if (!force && conversationId === this.currentConversationId) {
      debugLog('已是当前对话，跳过加载');
      traceLog('loadConversation:skip-same', { conversationId });
      this.suppressTitleTyping = false;
      this.titleReady = true;
      return;
    }

    // 切换对话前把 debounce 中的推理强度保存立即落盘（随请求带原对话 id，
    // 不会串到新对话）；fire-and-forget，不阻塞加载
    this.flushReasoningEffortSave?.();

    // 已移除「压缩中切换对话需确认/取消压缩」拦截：多对话独立运行后，压缩在
    // 后端随对话任务进行，切换视图不影响压缩本身；压缩锁也按对话隔离
    // （compressionActiveForCurrentConversation），不再存在对话级全局锁。

    // 注意：加载已有对话时必须保留该对话自身的模型/模式，不能套用用户默认值。

    // 多工作区并行后，切换对话只切换视图，不再取消后台任务；停止当前轮询避免事件写入新对话界面。
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      if (taskStore.hasActiveTask || this.taskInProgress) {
        taskStore.clearTask();
        if (typeof this.clearProcessedEvents === 'function') {
          this.clearProcessedEvents();
        }
      }
      this.clearLocalTaskUiState?.(`switch-conversation:${conversationId}`);
    } catch (error) {
      console.error('[切换对话] 停止本地轮询失败:', error);
    }

    await this.persistComposerDraftNow({
      reason: `switch-conversation:${conversationId}`,
      force: true,
      keepalive: true
    }).catch(() => {});

    try {
      // 统一加载协议：一次 bootstrap 替代「PUT load + GET messages + GET tasks」串行链。
      // 模式/模型应用、历史渲染、运行中任务恢复均在 enterConversation 内完成。
      const result = await this.enterConversation(conversationId, {
        source: 'sidebar',
        workspaceId,
        urlMode: 'push',
        preserveListPosition,
        resetUI: true
      });

      if (result.success) {
        debugLog('对话 bootstrap 成功:', result);
        traceLog('loadConversation:api-success', { conversationId, title: result.title });
        this.currentConversationTitle = result.title;
        this.titleReady = true;
        this.suppressTitleTyping = false;
        this.startTitleTyping(this.currentConversationTitle, { animate: false });

        this.subAgentFetch();
        this.fetchTodoList();

        await this.refreshRunningWorkspaceTasks?.();
        const visibleTask =
          typeof this.getVisibleWorkspaceTaskForConversation === 'function'
            ? this.getVisibleWorkspaceTaskForConversation(conversationId)
            : null;
        const visibleTaskStatus = String(visibleTask?.status || '');
        const terminalStatuses = new Set(['succeeded', 'failed', 'canceled']);
        if (visibleTask && terminalStatuses.has(visibleTaskStatus)) {
          // 用户已点进完成后的“待查看”对话，清除列表里的完成标记。
          this.acknowledgeCompletedWorkspaceTask?.(visibleTask.task_id);
        }
        this.fetchWorkMode();
        this.fetchPermissionMode();
        this.fetchExecutionMode();
        this.fetchNetworkPermission();
        await this.fetchVersioningStatus(conversationId, { silent: true });
        this.fetchPendingToolApprovals();
        this.fetchPendingUserQuestions();
        this.fetchPendingPlanApprovals();
        this.refreshProjectGitSummary();
        this.fetchConversationTokenStatistics();
        this.updateCurrentContextTokens();
        traceLog('loadConversation:after-history', {
          conversationId,
          messagesLen: Array.isArray(this.messages) ? this.messages.length : 'n/a'
        });
      } else {
        console.error('对话加载失败:', result.message);
        this.suppressTitleTyping = false;
        this.titleReady = true;
        this.uiPushToast({
          title: t('appMessages.loadConversationFailedTitle'),
          message: result.message || t('appMessages.serverNotSuccessMessage'),
          type: 'error'
        });
      }
    } catch (error) {
      console.error('加载对话异常:', error);
      traceLog('loadConversation:error', {
        conversationId,
        error: error?.message || String(error)
      });
      this.suppressTitleTyping = false;
      this.titleReady = true;
      this.uiPushToast({
        title: t('appMessages.loadConversationErrorTitle'),
        message: error.message || String(error),
        type: 'error'
      });
    }
  }
};

/* 工作区分组加载统一走 store 版（stores/conversation.ts 的 loadWorkspaceConversations/
   loadMoreWorkspaceConversations/loadWorkspaceConversationTypeCache），双类型缓存已在那里适配；
   此处不再保留重复实现 */

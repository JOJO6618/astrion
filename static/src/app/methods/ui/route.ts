// @ts-nocheck
import { t } from '@/locales';
import { useConversationTabsStore } from '../../../stores/conversationTabs';

export const routeMethods = {
  async bootstrapRoute() {
    // 桌面端标签条：先于路由解析恢复标签列表（后续 enterConversation 挂钩与
    // 启动恢复都依赖它；await 保证 hydrate 与标签登记不竞争）
    try {
      const { useConversationTabsStore } = await import('../../../stores/conversationTabs');
      const tabsStore = useConversationTabsStore();
      if (tabsStore.enabled) {
        await tabsStore.hydrate();
      }
    } catch (_tabsErr) {
      // 恢复失败不阻断路由
    }
    // 在路由解析期间抑制标题动画，避免预置"新对话"闪烁
    this.suppressTitleTyping = true;
    this.titleReady = false;
    this.currentConversationTitle = '';
    this.titleTypingText = '';
    let path = window.location.pathname.replace(/^\/+|\/+$/g, '');
    // 兼容重定向：旧多智能体路由统一收敛到裸路径。
    // 对话类型是 metadata 属性（创建时确定），不再是路由概念。
    if (path === 'multiagent/new' || path === 'multiagent') {
      history.replaceState({}, '', '/new');
      path = 'new';
    } else if (path.startsWith('multiagent/')) {
      const convPart = path.slice('multiagent/'.length);
      const bareId = convPart.startsWith('conv_') ? convPart.slice('conv_'.length) : convPart;
      history.replaceState({}, '', `/${bareId}`);
      path = bareId;
    }
    // 工作流编辑器：独立于对话体系的全屏路由，直接交给前端组件
    if (this.isConversationIndependentRoute()) {
      if (path === 'workflow') {
        history.replaceState({}, '', '/workflows');
        path = 'workflows';
      }
      if (path === 'settings' || path.startsWith('settings/')) {
        this.settingsRoute = path;
      } else {
        this.workflowDemoRoute = path;
      }
      // 桌面端标签条：独立全屏路由不属于任何对话标签，清空激活态——
      // chrome 条所有标签取消选中（暂存当前标签供返回时恢复）
      this.stashAndClearActiveTab?.();
      this.currentConversationId = null;
      this.currentConversationTitle = '';
      this.messages = [];
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.initialRouteResolved = true;
      return;
    }
    if (!path || this.isExplicitNewConversationRoute()) {
      this.currentConversationId = null;
      this.currentConversationTitle = t('common.newConversation');
      this.logMessageState('bootstrapRoute:clear-messages-for-new');
      this.messages = [];
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.startTitleTyping(t('common.newConversation'), { animate: false });
      this.initialRouteResolved = true;
      this.refreshBlankHeroState();
      // 触发后端重建索引以补全旧对话的 multi_agent_mode 字段（每次页面加载最多一次）
      if (!this.multiAgentIndexRebuildTriggered) {
        this.multiAgentIndexRebuildTriggered = true;
        try {
          await fetch('/api/multiagent/rebuild-index', { method: 'POST' });
        } catch (_e) {
          // 重建失败不阻断主流程
        }
      }
      await this.restoreComposerDraftState('bootstrap-route:new');
      return;
    }

    const convId = path.startsWith('conv_') ? path : `conv_${path}`;
    try {
      // 统一加载协议：一次 bootstrap 拿元数据+历史+运行状态（纯只读，不切换后端上下文）
      const result = await this.enterConversation(convId, {
        source: 'refresh',
        urlMode: 'replace'
      });
      if (result.success) {
        this.currentConversationTitle = result.title || '';
        this.titleReady = true;
        this.suppressTitleTyping = false;
        this.startTitleTyping(this.currentConversationTitle, { animate: false });
        // 刷新路径不经 loadConversation，需在此补拉 token 统计，
        // 否则输入栏右下角上下文用量圆环保持 0（enterConversation 内
        // skipConversationHistoryReload 会让 watcher 也跳过拉取）
        this.fetchConversationTokenStatistics();
        this.updateCurrentContextTokens();
      } else {
        history.replaceState({}, '', '/new');
        this.currentConversationId = null;
        this.currentConversationTitle = t('common.newConversation');
        this.titleReady = true;
        this.suppressTitleTyping = false;
        this.startTitleTyping(t('common.newConversation'), { animate: false });
      }
    } catch (error) {
      console.warn('初始化路由失败:', error);
      history.replaceState({}, '', '/new');
      this.currentConversationId = null;
      this.currentConversationTitle = t('common.newConversation');
      this.titleReady = true;
      this.suppressTitleTyping = false;
      this.startTitleTyping(t('common.newConversation'), { animate: false });
    } finally {
      this.initialRouteResolved = true;
    }
    await this.restoreComposerDraftState('bootstrap-route:conversation');
    if (this.currentConversationId) {
      this.fetchPendingUserQuestions?.();
    }
  },
  handlePopState(event) {
    const state = event.state || {};
    const convId = state.conversationId;
    if (!convId) {
      this.currentConversationId = null;
      this.currentConversationTitle = t('common.newConversation');
      this.logMessageState('handlePopState:clear-messages-no-conversation');
      this.messages = [];
      this.logMessageState('handlePopState:after-clear-no-conversation');
      this.resetAllStates('handlePopState:no-conversation');
      this.resetTokenStatistics();
      this.restoreComposerDraftState('popstate:new').catch(() => {});
      return;
    }
    this.loadConversation(convId);
  },
  refreshCurrentPage() {
    if (typeof window === 'undefined') {
      return;
    }
    this.persistComposerDraftNow({
      reason: 'refresh-page',
      force: true,
      keepalive: true
    })
      .catch(() => {})
      .finally(() => {
        window.location.reload();
      });
  },
  isExplicitNewConversationRoute() {
    const normalizedPath = window.location.pathname.replace(/^\/+|\/+$/g, '');
    // 多智能体旧路由在 bootstrapRoute 已被重定向到 /new，这里只判裸新建路由
    return normalizedPath === 'new';
  },
  /**
   * 当前 URL 是否为独立于对话体系的全屏路由（当前：/workflows、/workflow/*、/settings、/settings/*）。
   * 与 isExplicitNewConversationRoute 一样直接读 location.pathname，不依赖
   * bootstrap 阶段写入的状态字段，任意时机调用结果都正确。
   * 对话体系的自动恢复/广播接管/任务接管/URL 写回在这些路由下必须全部禁用，
   * 这是唯一判定入口，新增同类路由（全屏独立页）时在此登记。
   */
  isConversationIndependentRoute() {
    const normalizedPath = window.location.pathname.replace(/^\/+|\/+$/g, '');
    return (
      normalizedPath === 'workflows' ||
      normalizedPath === 'workflow' ||
      normalizedPath.startsWith('workflow/') ||
      normalizedPath === 'settings' ||
      normalizedPath.startsWith('settings/')
    );
  },
  openWorkflowsPage() {
    // 工作流编辑器是 bootstrap 级全屏路由，与退出方向（/new）对称使用整页跳转，保证状态干净
    // 跳转前暂存当前激活标签（返回时恢复）并立刻清空激活态（chrome 选中效果即时消失）
    this.stashAndClearActiveTab?.();
    window.location.assign('/workflows');
  },
  openSettingsPage() {
    // 设置页同样是 bootstrap 级全屏路由，整页跳转保证状态干净
    this.stashAndClearActiveTab?.();
    window.location.assign('/settings');
  },
  closeSettingsPage() {
    // 设置页返回出口：直接回到进入时的对话（暂存标签），避免先闪一个空 /new
    // 再加载对话记录；暂存标签已不存在才落 /new（restore 会补一个新对话标签）
    try {
      const tabsStore = useConversationTabsStore();
      const resume = tabsStore.consumeResumeTarget();
      if (resume) {
        // 选中态立即持久化，chrome 在页面加载期间就已恢复选中
        tabsStore.setActive(resume.key);
        tabsStore.persistNow();
        window.location.assign(resume.url);
        return;
      }
    } catch (_tabsErr) {
      // ignore
    }
    window.location.assign('/new');
  },
  stripConversationPrefix(conversationId) {
    if (!conversationId) return '';
    return conversationId.startsWith('conv_') ? conversationId.slice(5) : conversationId;
  }
};

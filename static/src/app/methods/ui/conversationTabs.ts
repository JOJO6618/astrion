// @ts-nocheck
// 桌面端顶部对话标签条：激活/新建/关闭后的视图导航动作。
// store 数据操作在 ConversationTabStrip.vue 组件内完成，这里只负责导航，
// 全部复用现有对话切换路径（跨工作区打开 / SPA 进入 /new），不发明新链路。
import { t } from '@/locales';
import { useConversationTabsStore, CHROME_RESUME_TAB_KEY } from '../../../stores/conversationTabs';

export const conversationTabsMethods = {
  /** 进入设置/工作流等独立全屏路由前：记住当前激活标签（返回时恢复），
   *  并立刻清空激活态+立即持久化——chrome 轮询在页面加载期间就开始收敛，
   *  选中态消失的感知延迟从「新页面 bootstrap 后」提前到「点击瞬间」。 */
  stashAndClearActiveTab() {
    try {
      useConversationTabsStore().stashAndClearActive();
    } catch (_e) {
      // ignore
    }
  },
  /** 点击标签：conv → 跨工作区打开对话；new → /new 页（本质是多个 /new 实例）。 */
  async handleTabActivate(tab) {
    if (!tab) return;
    const tabsStore = useConversationTabsStore();
    // 独立全屏路由（设置/工作流）没有对话体系状态，整跳回对话/新建页
    if (this.isConversationIndependentRoute?.()) {
      const target =
        tab.kind === 'conv' && tab.conversationId
          ? `/${String(tab.conversationId).replace(/^conv_/, '')}`
          : '/new';
      // 整跳前把激活态落地——跳转后新页面 hydrate 才能恢复正确的选中标签
      tabsStore.setActive(tab.key);
      tabsStore.persistNow();
      window.location.assign(target);
      return;
    }
    if (tab.kind === 'conv' && tab.conversationId) {
      await this.handleSelectWorkspaceConversation({
        conversationId: tab.conversationId,
        workspaceId: tab.workspaceId
      });
      return;
    }
    // new 标签：主页面是标签数据唯一写者——必须把激活态写进主 store 并持久化。
    // 此前这里只导航不 setActive，主 store 的 activeKey 停在旧对话标签，
    // chrome 乐观窗口（2.5s）过期后轮询快照把选中态收敛回旧标签（点击回弹）。
    tabsStore.setActive(tab.key);
    this.navigateToTabNewPage();
  },

  /** chrome 标签条（独立 webview）派发来的意图：activate/new/close。
   *  主页面是标签数据唯一写者，这里应用变更并完成实际导航。 */
  async handleChromeIntent(msg) {
    const tabsStore = useConversationTabsStore();
    if (!tabsStore.enabled || !msg || typeof msg !== 'object') return;
    const action = String(msg.action || '');
    const payload = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
    if (action === 'activate') {
      const key = String(payload.key || '');
      let tab = tabsStore.tabs.find((item) => item.key === key);
      if (!tab && key && payload.kind) {
        // 本地 store 未填充（独立路由下 hydrate 失败等）：用载荷信息兜底
        tab = {
          key,
          kind: payload.kind === 'conv' ? 'conv' : 'new',
          conversationId: String(payload.conversationId || ''),
          workspaceId: String(payload.workspaceId || ''),
          workspaceLabel: String(payload.workspaceLabel || ''),
          title: String(payload.title || '')
        };
      }
      if (tab) await this.handleTabActivate(tab);
      return;
    }
    if (action === 'new') {
      this.handleTabNew();
      return;
    }
    if (action === 'close') {
      const key = String(payload.key || '');
      const tab = tabsStore.tabs.find((item) => item.key === key);
      if (!tab) return;
      const wasActive = tab.key === tabsStore.activeKey;
      const nextTab = tabsStore.closeTab(key);
      if (wasActive) await this.handleTabClosedActive(nextTab);
    }
  },

  /** + 按钮 / ⌘T：新增一个 /new 标签并切过去。 */
  handleTabNew() {
    const tabsStore = useConversationTabsStore();
    if (!tabsStore.enabled) return;
    const wsId = String(this.currentHostWorkspaceId || '');
    const ws = (Array.isArray(this.hostWorkspaces) ? this.hostWorkspaces : []).find(
      (item) => String(item?.workspace_id || '') === wsId
    );
    tabsStore.addNewTab({ workspaceId: wsId, workspaceLabel: String(ws?.label || '') });
    this.navigateToTabNewPage();
  },

  /** 激活标签被关闭后：导航到继任标签；无剩余时补一个 /new 标签（永远留有出口）。 */
  async handleTabClosedActive(nextTab) {
    if (nextTab) {
      await this.handleTabActivate(nextTab);
      return;
    }
    this.handleTabNew();
  },

  /**
   * 启动恢复（桌面壳总是以裸路径打开应用）：loadInitialData 完成、工作区列表
   * 就绪后，按持久化的激活标签还原视图——conv 标签走进对话（含跨工作区切换），
   * new 标签保持 /new 空态，空列表补一个 /new 标签。
   * 从设置/工作流返回时（进入前已清空激活态）：优先恢复进入时暂存的标签，
   * 标签已不存在则新开一个「新对话」标签（导航栏不能空白无选中）。
   */
  async restoreConversationTabView() {
    const tabsStore = useConversationTabsStore();
    if (!tabsStore.enabled || !tabsStore.hydrated) return;
    // 独立全屏路由（设置/工作流）下禁止恢复：进入前已刻意清空激活态，
    // 这里若恢复暂存标签会重新 setActive 并持久化——chrome 选中效果消失后
    // 又跳回来，还会在后台把对话加载进设置页。恢复只在回到对话体系时进行
    if (this.isConversationIndependentRoute?.()) return;
    if (this.currentConversationId) return; // bootstrapRoute 已进入对话（如显式 URL）
    // 双 webview 后「新建对话」是 SPA 导航不触发整页加载，mounted 只发生在
    // 冷启动/手动刷新——此处还原持久化激活标签不会劫持用户的新建意图
    // （激活的是 new 标签时本来就不会导航）；冷启动落在 /new 也要还原
    let active = tabsStore.activeTab;
    if (!active) {
      let resumeKey = '';
      try {
        resumeKey = window.localStorage.getItem(CHROME_RESUME_TAB_KEY) || '';
        window.localStorage.removeItem(CHROME_RESUME_TAB_KEY);
      } catch (_e) {
        // ignore
      }
      if (resumeKey) {
        const resumed = tabsStore.tabs.find((item) => item.key === resumeKey);
        if (resumed) {
          tabsStore.setActive(resumed.key);
          active = resumed;
        }
      }
    }
    if (active && active.kind === 'conv' && active.conversationId) {
      await this.handleSelectWorkspaceConversation({
        conversationId: active.conversationId,
        workspaceId: active.workspaceId
      });
      return;
    }
    if (active && active.kind === 'new') {
      return; // 恢复的是 new 标签：已在 /new，选中态已由 setActive 落地
    }
    if (!tabsStore.activeTab) {
      // 暂存标签已不存在（对话被删/标签被关）或无任何可恢复的：
      // 新开一个「新对话」标签作为出口（空列表场景同此路径）
      this.handleTabNew();
    }
  },

  /** 跳到 /new 页（双 webview 后主页面 SPA 进入，不再整页跳转）。 */
  navigateToTabNewPage() {
    this.enterNewConversationPage();
  },

  /** 主页面窗口级快捷键：⌘T 新建标签 / ⌘W 关闭当前标签。
   *  双 webview 后 chrome 条在独立 webview，主页面聚焦时按键到不了 chrome，
   *  在主页面同样注册一份（直接调本地方法，不经 dispatch 中继）。 */
  handleGlobalTabShortcut(event) {
    const tabsStore = useConversationTabsStore();
    if (!tabsStore.enabled) return;
    if (!(event.metaKey || event.ctrlKey)) return;
    const key = String(event.key || '').toLowerCase();
    if (key === 't') {
      event.preventDefault();
      this.handleTabNew();
      return;
    }
    if (key === 'w') {
      const active = tabsStore.activeTab;
      // 与 chrome 组件同规则：仅剩一个空对话标签不可关
      if (!active || (tabsStore.tabs.length === 1 && active.kind === 'new')) return;
      event.preventDefault();
      const nextTab = tabsStore.closeTab(active.key);
      // ⌘W 关的一定是激活标签，关闭后导航到继任者（无剩余时补新标签）
      this.handleTabClosedActive(nextTab);
    }
  },

  /** SPA 方式进入 /new 空态：chrome 标签条常驻顶部，主页面切换不整页刷新，
   *  避免旧方案 location.href 跳转的全屏闪白与标签条重建。 */
  enterNewConversationPage() {
    // 独立全屏路由（设置/工作流）没有对话体系状态，只能整跳回 /new
    if (this.isConversationIndependentRoute?.()) {
      useConversationTabsStore().persistNow();
      window.location.assign('/new');
      return;
    }
    const path = String(window.location.pathname || '/').replace(/^\/+|\/+$/g, '');
    if ((path === '' || path === 'new') && !this.currentConversationId) return;
    history.pushState({}, '', '/new');
    this.currentConversationId = null;
    this.currentConversationTitle = t('common.newConversation');
    this.messages = [];
    this.titleReady = true;
    this.suppressTitleTyping = false;
    this.startTitleTyping(t('common.newConversation'), { animate: false });
    this.resetAllStates('chrome-tabs:enter-new');
    this.resetTokenStatistics();
    this.refreshBlankHeroState();
    this.restoreComposerDraftState('chrome-tabs:new').catch(() => {});
  }
};

import { defineStore } from 'pinia';
import { setViewVisible } from '../app/methods/ui/viewVisibility';
import { NavigationError, NavigationTransport, navigationId } from './desktopNavigationTransport';

export interface ConversationTab {
  /** Immutable for the entire tab lifetime, including new -> conversation binding. */
  key: string;
  kind: 'conv' | 'new' | 'page';
  conversationKind: 'chat' | 'agent';
  conversationId: string;
  workspaceId: string;
  workspaceLabel: string;
  title: string;
  error: string;
  running: boolean;
  awaitingHumanApproval: boolean;
  awaitingUserAnswer: boolean;
  placement: 'main' | 'detached';
  /** 仅 page 标签。 */
  pageType: '' | 'settings' | 'workflows' | 'git' | 'schedules';
  route: string;
  openerKey: string;
}

/** 壳层桥（preload-view/preload-chrome 的 astrionShell 并集）；网页端不存在。 */
export interface ShellBridge {
  identity?: () => Promise<{
    key: string;
    placement: string;
    visible: boolean;
    legacy: boolean;
    state: any;
  } | null>;
  onTabsState?: (callback: (state: any) => void) => () => void;
  forwardTabsState?: (state: any) => void;
  show?: (key: string, requestId: string) => void;
  settleShow?: (requestId: string) => void;
  focusMain?: () => void;
  focusTab?: (key: string) => void;
  onPlacement?: (callback: (payload: { key: string; placement: string }) => void) => () => void;
  dragTab?: (payload: {
    key: string;
    phase: 'start' | 'move' | 'drop' | 'cancel';
    screenX: number;
    screenY: number;
  }) => void;
  onRequestDetach?: (callback: (payload: { key: string }) => void) => () => void;
  onRequestDock?: (callback: (payload: { key: string }) => void) => () => void;
  onWindowFocus?: (callback: (payload: { key: string }) => void) => () => void;
  onRequestPage?: (callback: (payload: { route: string }) => void) => () => void;
  onNotificationOpen?: (callback: (payload: ConversationTabTarget) => void) => () => void;
  focusNotificationTab?: (key: string) => void;
}

interface ConversationTabTarget {
  conversationId: string;
  workspaceId: string;
  conversationKind?: 'chat' | 'agent';
  title?: string;
}

export function shellBridge(): ShellBridge | null {
  return ((window as any).astrionShell as ShellBridge) || null;
}

/** 冷启动宽限期：这段时间内的会话未就绪/网络错误不显示成同步故障。 */
const SYNC_FAILURE_QUIET_MS = 20_000;

const transports = new WeakMap<object, NavigationTransport>();
function transport(store: object) {
  if (!transports.has(store)) transports.set(store, new NavigationTransport());
  return transports.get(store)!;
}

export function normalizeTab(raw: any): ConversationTab {
  return {
    key: String(raw.key),
    kind: raw.kind,
    conversationKind: raw.conversation_kind === 'chat' ? 'chat' : 'agent',
    conversationId: String(raw.conversation_id || ''),
    workspaceId: String(raw.workspace_id || ''),
    workspaceLabel: String(raw.workspace_label || ''),
    title: String(raw.title || ''),
    error: String(raw.error || ''),
    running: Boolean(raw.running),
    awaitingHumanApproval: Boolean(raw.awaiting_human_approval),
    awaitingUserAnswer: Boolean(raw.awaiting_user_answer),
    placement: raw.placement === 'detached' ? 'detached' : 'main',
    pageType: raw.kind === 'page' ? raw.page_type || '' : '',
    route: String(raw.route || ''),
    openerKey: String(raw.opener_key || '')
  };
}

/** 页面标签关闭/返回的目标：opener 仍在主窗口则回 opener，否则右侧相邻、无则左侧。 */
export function pageReturnTarget(tabs: ConversationTab[], pageKey: string): ConversationTab | null {
  const main = tabs.filter((tab) => tab.placement === 'main');
  const page = main.find((tab) => tab.key === pageKey);
  const opener = page && main.find((tab) => tab.key === page.openerKey && tab.key !== pageKey);
  if (opener) return opener;
  const index = main.findIndex((tab) => tab.key === pageKey);
  return main[index + 1] || (index > 0 ? main[index - 1] : null) || null;
}

export const useConversationTabsStore = defineStore('conversationTabs', {
  state: () => ({
    // 仅桌面壳且存在 astrionShell 桥时启用；网页端/Android/旧壳保持单页行为。
    enabled: Boolean((window as any).__ASTRION_DESKTOP__ && (window as any).astrionShell),
    role: ((window as any).__ASTRION_CHROME__ ? 'chrome' : 'view') as 'chrome' | 'view',
    /** 本文档固定绑定的标签 key（view 角色；legacy 兜底页为空并停用）。 */
    viewKey: '',
    placement: 'main' as 'main' | 'detached',
    tabs: [] as ConversationTab[],
    confirmedActiveKey: '',
    pendingShow: null as { key: string; id: string } | null,
    workspaceId: '',
    renderers: {} as Record<string, string>,
    revision: -1,
    responseSequence: 0,
    hydrated: false,
    initialized: false,
    localRendererId: navigationId(),
    attached: false,
    pendingFail: '',
    syncError: '',
    commandError: '',
    /** 本次文档开始同步的时间（冷启动宽限期的基准）。 */
    bootAt: 0
  }),
  getters: {
    activeKey: (state): string => state.pendingShow?.key || state.confirmedActiveKey,
    activeTab(): ConversationTab | null {
      return this.tabs.find((tab) => tab.key === this.activeKey) || null;
    },
    viewTab: (state): ConversationTab | null =>
      (state.viewKey && state.tabs.find((tab) => tab.key === state.viewKey)) || null,
    /** 本视图当前在独立对话窗口（P2）：隐藏侧边栏，顶部自绘标题栏。 */
    detached: (state): boolean =>
      state.enabled && state.role === 'view' && state.placement === 'detached',
    /** 本文档就是设置页标签（页内跳转可就地进行；否则应发 page 命令）。 */
    isSettingsView(): boolean {
      return this.viewTab?.kind === 'page' && this.viewTab.pageType === 'settings';
    },
    mainTabs: (state): ConversationTab[] => state.tabs.filter((tab) => tab.placement === 'main'),
    /** 已 attach 且本文档仍持有本标签的 renderer 写权。 */
    ownsView: (state): boolean =>
      state.attached &&
      Boolean(state.viewKey) &&
      state.renderers[state.viewKey] === state.localRendererId &&
      state.tabs.some((tab) => tab.key === state.viewKey)
  },
  actions: {
    /**
     * 冷启动宽限期：会话尚未建立（桌面壳刚起来、免密登录还没完成）时会拿到 401 或网络错误，
     * 这不是同步故障，不能把启动过程报成「同步失败，重试」；超过宽限期仍失败就照常提示。
     */
    shouldSilenceSyncFailure(error: unknown): boolean {
      if (this.hydrated) return false;
      const startedAt = this.bootAt || (this.bootAt = Date.now());
      if (Date.now() - startedAt > SYNC_FAILURE_QUIET_MS) return false;
      return error instanceof NavigationError ? error.status === 401 : true;
    },
    /** 记录壳层身份并订阅广播；legacy 兜底页（key 为空）整体降级为网页行为。 */
    async init() {
      if (!this.enabled || this.initialized) return;
      this.initialized = true;
      this.bootAt = this.bootAt || Date.now();
      const shell = shellBridge();
      shell?.onTabsState?.((state) => this.applyServerSnapshot(state, this.responseSequence));
      if (this.role === 'chrome') return;
      // 身份返回前收到的 placement 通知（viewKey 尚未确定，先按 key 记下；只用于 init 内的竞态）。
      const earlyPlacements = new Map<string, 'main' | 'detached'>();
      // 拖出/放回时壳层即时通知，页面就地切换布局（不重载）。
      // 这是本文档归属的本地权威：拖出期间后端快照仍是 main，轮询广播会在 500ms 内改回 placement；
      // 照抄快照会让标题栏在拖出途中消失，因此 placement 只由通知与初始身份决定。
      shell?.onPlacement?.((payload) => {
        if (!payload?.key) return;
        const key = String(payload.key);
        const placement = payload.placement === 'detached' ? 'detached' : 'main';
        if (key === this.viewKey) this.placement = placement;
        else if (!this.viewKey) earlyPlacements.set(key, placement);
      });
      let identity = null;
      try {
        identity = (await shell?.identity?.()) || null;
      } catch {
        identity = null;
      }
      if (!identity || identity.legacy || !identity.key) {
        this.enabled = false;
        return;
      }
      this.viewKey = String(identity.key);
      // 身份请求可能在拖出事件之前发出：期间收到过本标签的 placement 通知时以通知为准，
      // 否则残留的旧身份会把刚出现的独立窗口布局改回主窗口。
      const early = earlyPlacements.get(this.viewKey);
      this.placement = early ?? (identity.placement === 'detached' ? 'detached' : 'main');
      setViewVisible(identity.visible !== false);
      if (identity.state) this.applyServerSnapshot(identity.state, this.responseSequence);
    },
    applyServerSnapshot(raw: any, sequence = 0): boolean {
      if (!this.enabled || !raw || !Array.isArray(raw.tabs) || !Number.isInteger(raw.revision))
        return false;
      if (
        raw.revision < this.revision ||
        (raw.revision === this.revision && sequence < this.responseSequence)
      )
        return false;
      this.responseSequence = sequence;
      // Consumers observe after this patch; synchronous nested-field watchers are unsafe.
      const tabs: ConversationTab[] = raw.tabs.map(normalizeTab);
      this.$patch({
        tabs,
        pendingShow: tabs.some(tab => tab.key === this.pendingShow?.key && tab.placement === 'main')
          ? this.pendingShow : null,
        confirmedActiveKey: String(raw.active_key || ''),
        workspaceId: String(raw.workspace_id || ''),
        renderers: { ...(raw.renderers || {}) },
        revision: raw.revision,
        hydrated: true,
        syncError: ''
      });
      // placement 只由壳层通知/初始身份决定：快照里的后端归属会滞后（拖出已开窗但尚未 detach 时仍报 main），
      // 由它改写会让独立窗口的标题栏在拖拽途中闪回主窗口布局。
      return true;
    },
    /** 把本文档拿到的更新快照转交壳层（壳层只接受更高 revision 并广播给其他视图）。 */
    forward(raw: any) {
      try {
        shellBridge()?.forwardTabsState?.(raw);
      } catch {
        // 壳层转发失败不影响本页状态；chrome 轮询会兜底。
      }
    },
    async hydrate() {
      if (!this.enabled) return;
      await this.init();
      if (!this.enabled || this.hydrated) return;
      await this.refreshSnapshot();
    },
    async refreshSnapshot() {
      if (!this.enabled) return;
      try {
        const { result, sequence } = await transport(this).snapshot();
        if (this.applyServerSnapshot(result, sequence) && this.role === 'chrome')
          this.forward(result);
      } catch (error) {
        // 启动宽限期内的 401/网络抖动不出错：登录一完成，下一次轮询就会恢复。
        if (this.shouldSilenceSyncFailure(error)) return;
        this.syncError = String((error as Error)?.message || error);
        throw error;
      }
    },
    /**
     * chrome 视图接收壳层请求并转成后端命令（同一 envelope，传输层网络不确定时原样重试一次）：
     * 拖出完成→detach、独立窗口关闭→dock、窗口聚焦→focus、快捷入口深链→page。返回取消函数。
     */
    installShellRequests() {
      const shell = shellBridge();
      if (!this.enabled || this.role !== 'chrome' || !shell) return () => {};
      const run = (action: string, payload: Record<string, unknown>) =>
        void this.command(action, payload).catch(() => {});
      const offs = [
        shell.onRequestDetach?.((p) => p?.key && run('detach', { key: p.key })),
        shell.onRequestDock?.((p) => p?.key && run('dock', { key: p.key })),
        shell.onWindowFocus?.((p) => p?.key && run('focus', { key: p.key })),
        shell.onRequestPage?.((p) => p?.route && run('page', { page: p.route })),
        shell.onNotificationOpen?.((target) => {
          if (!target?.conversationId) return;
          void this.openConversationTab(target)
            .then((outcome) => {
              if (outcome?.key) shell.focusNotificationTab?.(outcome.key);
            })
            .catch(() => {});
        })
      ];
      return () => offs.forEach((off) => off?.());
    },
    /** 仅 chrome 视图轮询；内容视图靠壳层广播。 */
    startPolling() {
      if (!this.enabled || this.role !== 'chrome') return;
      void this.init();
      transport(this).start(
        (state, sequence) => {
          if (this.applyServerSnapshot(state, sequence)) this.forward(state);
        },
        (error) => {
          if (this.shouldSilenceSyncFailure(error)) {
            this.syncError = '';
            return;
          }
          this.syncError = String((error as Error)?.message || error);
        }
      );
    },
    stopPolling() {
      transport(this).stop();
    },
    async command(action: string, payload: Record<string, unknown> = {}) {
      if (!this.enabled) return null;
      this.commandError = '';
      try {
        const { result, sequence } = await transport(this).command({
          command_id: navigationId(),
          action,
          payload
        });
        if (this.applyServerSnapshot(result.state, sequence)) this.forward(result.state);
        const outcome = result.result || {};
        // 目标对话在独立窗口：让壳层前置该窗口（不改 active）。
        if (outcome.focus === 'detached' && outcome.key) shellBridge()?.focusTab?.(outcome.key);
        // 独立窗口里点设置/Git/工作流：页面标签在主窗口打开，随后前置主窗口。
        if (action === 'page' && this.placement === 'detached') shellBridge()?.focusMain?.();
        return outcome;
      } catch (error) {
        if (error instanceof NavigationError && error.status === 409) {
          await this.refreshSnapshot().catch(() => {});
        }
        // 零工作区（新装 host）是正常空态，不是同步故障：保持当前页，不跳转。
        if (error instanceof NavigationError && error.code === 'no_workspace') {
          this.commandError = '';
          return null;
        }
        this.commandError = this.shouldSilenceSyncFailure(error)
          ? ''
          : String((error as Error)?.message || error);
        throw error;
      }
    },
    async attach() {
      if (!this.viewKey) return;
      await this.command('attach', { key: this.viewKey, renderer_id: this.localRendererId });
      this.attached = true;
      if (this.pendingFail) {
        const error = this.pendingFail;
        this.pendingFail = '';
        await this.failView(error);
      }
    },
    addNewTab(
      payload: {
        workspaceId?: string;
        workspaceLabel?: string;
        conversationKind?: 'chat' | 'agent';
      } = {}
    ) {
      let base = this.viewTab || this.activeTab;
      // 页面标签没有对话模式：沿用其 opener 的模式。
      if (base?.kind === 'page')
        base = this.tabs.find((tab) => tab.key === base!.openerKey) || null;
      const kind = payload.conversationKind || base?.conversationKind || 'agent';
      // 工作区以导航快照为准；真正零工作区时后端返回 code=no_workspace，command 静默忽略。
      return this.command('create', {
        key: `tab_${navigationId()}`,
        conversation_kind: kind,
        workspace_id: kind === 'chat' ? '' : payload.workspaceId || this.workspaceId,
        workspace_label: payload.workspaceLabel || ''
      });
    },
    openConversationTab(payload: ConversationTabTarget) {
      return this.command('open', {
        key: `tab_${navigationId()}`,
        conversation_id: payload.conversationId,
        conversation_kind: payload.conversationKind || 'agent',
        workspace_id: payload.workspaceId,
        title: payload.title || ''
      });
    },
    closeTab(key: string) {
      return this.command('close', { key });
    },
    setActive(key: string) {
      return this.command('activate', { key });
    },
    /** 标签栏点击：先让壳层乐观显示，再发 activate（以后端快照为准）。 */
    async showTab(key: string) {
      const shell = shellBridge();
      if (!shell?.show || !this.mainTabs.some(tab => tab.key === key)) return this.setActive(key);
      const id = navigationId();
      try {
        shell.show(key, id);
        this.pendingShow = { key, id };
      } catch {
        return this.setActive(key);
      }
      try {
        return await this.setActive(key);
      } finally {
        if (this.pendingShow?.id === id) this.pendingShow = null;
        shell.settleShow?.(id);
      }
    },
    reorderTab(key: string, beforeKey: string) {
      return this.command('reorder', { key, before_key: beforeKey });
    },
    bindConversation(key: string, workspaceId: string, conversationId: string, title: string) {
      return this.command('bind', {
        key,
        workspace_id: workspaceId,
        conversation_id: conversationId,
        renderer_id: this.localRendererId,
        title
      });
    },
    failView(error: string) {
      if (!this.ownsView) {
        // 路由启动早于 attach：登记后由 attach 补发。
        this.pendingFail = error;
        return Promise.resolve(null);
      }
      return this.command('fail', {
        key: this.viewKey,
        renderer_id: this.localRendererId,
        error
      });
    },
    openPage(page: string) {
      return this.command('page', { page });
    },
    /** 独立页面返回：激活 opener（仍在主窗口），否则右侧相邻/左侧；不关闭页面标签。 */
    async leavePage() {
      const target = pageReturnTarget(this.tabs, this.viewKey || this.activeKey);
      if (target) return this.showTab(target.key);
      return this.addNewTab();
    },
    updateTitle(conversationId: string, title: string, workspaceId: string) {
      if (!this.enabled || !title) return;
      // The authoritative index also enriches titles; this command keeps the persisted fallback fresh.
      void this.command('title', {
        conversation_id: conversationId,
        workspace_id: workspaceId,
        title
      }).catch(() => {});
    }
  }
});

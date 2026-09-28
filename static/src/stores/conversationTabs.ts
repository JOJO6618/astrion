// static/src/stores/conversationTabs.ts - 桌面端顶部对话标签条状态
//
// 职责：
// - 仅桌面壳内启用（壳 initialization_script 注入 window.__ASTRION_DESKTOP__）；
//   浏览器环境不渲染标签条、状态恒空
// - 维护标签列表（跨工作区混排）：conv 标签 = 已打开的对话；new 标签 = /new 页
//   （发送首条消息落地为真实对话后转换为 conv 标签）
// - 持久化到后端 {DATA_DIR}/conversation_tabs.json（防抖 PUT），
//   重开应用经 hydrate 恢复；运行状态点不持久化，由任务状态实时推导
import { defineStore } from 'pinia';

const PERSIST_DEBOUNCE_MS = 400;

/** 进入设置/工作流等独立路由前暂存激活标签的 localStorage 键（返回时恢复）。 */
export const CHROME_RESUME_TAB_KEY = 'agents_chrome_resume_tab';

export type ConversationTabKind = 'conv' | 'new';

export interface ConversationTab {
  /** 稳定标识：'conv:<conversationId>' 或 'new:<n>' */
  key: string;
  kind: ConversationTabKind;
  conversationId: string;
  workspaceId: string;
  workspaceLabel: string;
  /** conv 标签的对话标题；new 标签标题由组件按语言实时渲染，不存这里 */
  title: string;
  /** 是否有活动任务（仅 GET 快照下发，用于 chrome 状态点；不持久化） */
  running?: boolean;
}

/** chrome 镜像乐观动作的有效窗口：主页面应用并持久化后经快照收敛，
 *  窗口内服务端快照被视为旧数据，用本地乐观结果覆盖，防止操作闪烁。 */
const MIRROR_OPTIMISTIC_TTL_MS = 2500;

async function parseJson(resp: Response): Promise<any | null> {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

function sanitizeTab(raw: any): ConversationTab | null {
  if (!raw || typeof raw !== 'object') return null;
  const kind = raw.kind === 'new' ? 'new' : raw.kind === 'conv' ? 'conv' : null;
  const key = String(raw.key || '').trim();
  if (!kind || !key) return null;
  return {
    key,
    kind,
    conversationId: String(raw.conversation_id || ''),
    workspaceId: String(raw.workspace_id || ''),
    workspaceLabel: String(raw.workspace_label || ''),
    title: String(raw.title || ''),
    running: Boolean(raw.running)
  };
}

export const useConversationTabsStore = defineStore('conversationTabs', {
  state: () => ({
    /** 是否桌面壳环境（一次性检测，常量） */
    enabled: Boolean((window as any).__ASTRION_DESKTOP__),
    tabs: [] as ConversationTab[],
    activeKey: '',
    /** 已从后端拉取过（避免 bootstrap 期间重复 hydrate） */
    hydrated: false,
    /** hydrate 成功后才允许持久化：防止拉取失败（如会话未就绪）时用空状态覆写后端数据 */
    persistReady: false,
    /** chrome 独立 webview 镜像模式：只做乐观镜像，不持久化（主页面是唯一写者） */
    mirrorMode: false,
    _newCounter: 0 as number,
    _persistTimer: 0 as number,
    /** 镜像乐观动作记录（applyServerSnapshot 收敛窗口内覆盖服务端旧数据） */
    _optimistic: {
      activeKey: '',
      activeAt: 0,
      removed: [] as { key: string; at: number }[],
      added: [] as { tab: ConversationTab; at: number }[]
    }
  }),

  getters: {
    activeTab(state): ConversationTab | null {
      return state.tabs.find((t) => t.key === state.activeKey) || null;
    },
    /** 指定对话是否已有标签 */
    hasConversationTab(state): (conversationId: string) => boolean {
      return (conversationId: string) =>
        state.tabs.some((t) => t.kind === 'conv' && t.conversationId === conversationId);
    }
  },

  actions: {
    /** 启动时从后端恢复标签列表（仅桌面壳调用一次）。 */
    async hydrate() {
      if (!this.enabled || this.hydrated) return;
      this.hydrated = true;
      try {
        const resp = await fetch('/api/conversation-tabs', { credentials: 'same-origin' });
        const payload = await parseJson(resp);
        if (!payload?.success) return;
        this.persistReady = true;
        const tabs = (Array.isArray(payload.tabs) ? payload.tabs : [])
          .map(sanitizeTab)
          .filter(Boolean) as ConversationTab[];
        this.tabs = tabs;
        const activeKey = String(payload.active_key || '');
        // 显式空激活（离开于设置/工作流页）必须尊重；
        // 只有持久化的 key 失效（指向已不存在的标签）才回退第一个
        this.activeKey = !activeKey
          ? ''
          : tabs.some((t) => t.key === activeKey)
            ? activeKey
            : tabs[0]?.key || '';
        // new 标签计数器接续，避免 key 撞车
        for (const t of tabs) {
          const m = /^new:(\d+)$/.exec(t.key);
          if (m) this._newCounter = Math.max(this._newCounter, Number(m[1]));
        }
      } catch {
        // 拉取失败按空列表处理，不阻塞启动
      }
    },

    /** 防抖持久化（全局 fetch 包装自动带 CSRF）。 */
    schedulePersist() {
      if (!this.enabled || !this.persistReady || this.mirrorMode) return;
      if (this._persistTimer) window.clearTimeout(this._persistTimer);
      this._persistTimer = window.setTimeout(() => {
        this._persistTimer = 0;
        this.persistNow();
      }, PERSIST_DEBOUNCE_MS);
    },

    /** 立即持久化（keepalive 使请求在页面卸载后仍能发完）。
     *  整页跳转（window.location.href）前必须调它——防抖中的变更会随卸载丢失。 */
    persistNow() {
      if (!this.enabled || !this.persistReady || this.mirrorMode) return;
      if (this._persistTimer) {
        window.clearTimeout(this._persistTimer);
        this._persistTimer = 0;
      }
      const body = {
        tabs: this.tabs.map((t) => ({
          key: t.key,
          kind: t.kind,
          conversation_id: t.conversationId,
          workspace_id: t.workspaceId,
          workspace_label: t.workspaceLabel,
          title: t.title
        })),
        active_key: this.activeKey
      };
      fetch('/api/conversation-tabs', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        keepalive: true
      }).catch(() => {
        // 持久化失败不影响交互，下次变更再试
      });
    },

    /** 对话成功进入后登记/激活对应标签（enterConversation 挂钩点）。 */
    openConversationTab(payload: {
      conversationId: string;
      workspaceId: string;
      workspaceLabel: string;
      title: string;
    }) {
      if (!this.enabled || !payload.conversationId) return;
      const existing = this.tabs.find(
        (t) => t.kind === 'conv' && t.conversationId === payload.conversationId
      );
      if (existing) {
        existing.title = payload.title || existing.title;
        this.activeKey = existing.key;
      } else {
        const active = this.activeTab;
        if (active && active.kind === 'new') {
          // 当前在 /new 页打开对话：就地转换当前新标签，不另开新标签
          active.kind = 'conv';
          active.conversationId = payload.conversationId;
          active.workspaceId = payload.workspaceId || active.workspaceId;
          active.workspaceLabel = payload.workspaceLabel || active.workspaceLabel;
          active.title = payload.title;
          active.key = `conv:${payload.conversationId}`;
          this.activeKey = active.key;
        } else {
          const tab: ConversationTab = {
            key: `conv:${payload.conversationId}`,
            kind: 'conv',
            conversationId: payload.conversationId,
            workspaceId: payload.workspaceId,
            workspaceLabel: payload.workspaceLabel,
            title: payload.title
          };
          this.tabs.push(tab);
          this.activeKey = tab.key;
        }
      }
      this.schedulePersist();
    },

    /** 新增一个 /new 标签（可多个并存，发首条消息后转换为 conv）。 */
    addNewTab(payload: { workspaceId: string; workspaceLabel: string }) {
      return this.addNewTabAt(this.tabs.length, payload);
    },

    /** 在指定位置插入 /new 标签（chrome 关闭最后一个 conv 标签时同位置露出新标签）。 */
    addNewTabAt(index: number, payload: { workspaceId: string; workspaceLabel: string }) {
      if (!this.enabled) return null;
      this._newCounter += 1;
      const tab: ConversationTab = {
        key: `new:${this._newCounter}`,
        kind: 'new',
        conversationId: '',
        workspaceId: payload.workspaceId,
        workspaceLabel: payload.workspaceLabel,
        title: ''
      };
      const at = Math.max(0, Math.min(index, this.tabs.length));
      this.tabs.splice(at, 0, tab);
      this.activeKey = tab.key;
      if (this.mirrorMode) {
        this._optimistic.added.push({ tab, at: Date.now() });
        this._optimistic.activeKey = tab.key;
        this._optimistic.activeAt = Date.now();
      }
      this.schedulePersist();
      return tab;
    },

    /** 首条消息创建对话成功：把当前激活的 new 标签转换为 conv 标签。 */
    convertActiveNewTab(payload: { conversationId: string; title: string }) {
      if (!this.enabled) return;
      const tab = this.tabs.find((t) => t.key === this.activeKey);
      if (!tab || tab.kind !== 'new') {
        // 激活的不是 new 标签（例如在既有对话里发消息）——按普通打开登记
        return;
      }
      tab.kind = 'conv';
      tab.conversationId = payload.conversationId;
      tab.title = payload.title;
      tab.key = `conv:${payload.conversationId}`;
      this.activeKey = tab.key;
      this.schedulePersist();
    },

    /** 关闭标签；返回关闭后应激活的标签（无剩余时为 null，调用方落到 /new 空态）。 */
    closeTab(key: string): ConversationTab | null {
      const index = this.tabs.findIndex((t) => t.key === key);
      if (index === -1) return this.activeTab;
      const wasActive = this.tabs[index].key === this.activeKey;
      this.tabs.splice(index, 1);
      if (wasActive) {
        const next = this.tabs[Math.min(index, this.tabs.length - 1)] || null;
        this.activeKey = next ? next.key : '';
      }
      if (this.mirrorMode) {
        this._optimistic.removed.push({ key, at: Date.now() });
        if (wasActive) {
          this._optimistic.activeKey = this.activeKey;
          this._optimistic.activeAt = Date.now();
        }
      }
      this.schedulePersist();
      return this.activeTab;
    },

    /** 仅切换激活态（实际导航由 App.vue 事件处理完成）。 */
    setActive(key: string) {
      if (!this.tabs.some((t) => t.key === key)) return;
      if (this.activeKey === key) return;
      this.activeKey = key;
      if (this.mirrorMode) {
        this._optimistic.activeKey = key;
        this._optimistic.activeAt = Date.now();
      }
      this.schedulePersist();
    },

    /** 清空激活态（进入设置/工作流等独立全屏路由时调用：
     *  这些页面不属于任何对话标签，chrome 条应全部取消选中）。 */
    clearActive() {
      if (!this.activeKey) return;
      this.activeKey = '';
      this.schedulePersist();
    },

    /** 进入独立全屏路由前：暂存当前激活标签（返回时恢复）+ 清空激活态 +
     *  立即持久化。只在有激活标签时暂存——已进入独立路由后二次跳转
     *  （设置↔工作流）activeKey 为空，不能覆盖进入前暂存的值。 */
    stashAndClearActive() {
      if (!this.enabled) return;
      try {
        if (this.activeKey) {
          window.localStorage.setItem(CHROME_RESUME_TAB_KEY, this.activeKey);
        }
      } catch (_e) {
        // localStorage 不可用时仅清空不暂存
      }
      this.clearActive();
      this.persistNow();
    },

    /** 从独立全屏路由返回时：取出并清除暂存标签，推导返回目标 URL。
     *  返回 { url, key }；无暂存或标签已不存在返回 null（调用方落 /new，
     *  由 restoreConversationTabView 补一个「新对话」标签）。 */
    consumeResumeTarget(): { url: string; key: string } | null {
      if (!this.enabled) return null;
      let resumeKey = '';
      try {
        resumeKey = window.localStorage.getItem(CHROME_RESUME_TAB_KEY) || '';
        window.localStorage.removeItem(CHROME_RESUME_TAB_KEY);
      } catch (_e) {
        // ignore
      }
      if (!resumeKey) return null;
      const tab = this.tabs.find((t) => t.key === resumeKey);
      if (!tab) return null;
      if (tab.kind === 'conv' && tab.conversationId) {
        return { url: `/${tab.conversationId.replace(/^conv_/, '')}`, key: tab.key };
      }
      return { url: '/new', key: tab.key };
    },

    /** 对话标题更新（重命名等）时同步标签标题。 */
    updateTitle(conversationId: string, title: string) {
      const tab = this.tabs.find((t) => t.kind === 'conv' && t.conversationId === conversationId);
      if (tab && title && tab.title !== title) {
        tab.title = title;
        this.schedulePersist();
      }
    },

    /** chrome 镜像轮询收敛：以服务端快照为基底，合并未过期的本地乐观动作。
     *  主页面是唯一写者——chrome 乐观操作后主页面应用并持久化，快照最多滞后
     *  一个防抖+轮询周期；窗口期内的快照是旧数据，用乐观记录覆盖防闪烁。 */
    applyServerSnapshot(rawTabs: any[], rawActiveKey: string) {
      if (!this.enabled || !this.mirrorMode) return;
      this.persistReady = true;
      const now = Date.now();
      const opt = this._optimistic;
      opt.removed = opt.removed.filter((r) => now - r.at < MIRROR_OPTIMISTIC_TTL_MS);
      opt.added = opt.added.filter((a) => now - a.at < MIRROR_OPTIMISTIC_TTL_MS);
      if (now - opt.activeAt >= MIRROR_OPTIMISTIC_TTL_MS) opt.activeKey = '';

      const serverTabs = (Array.isArray(rawTabs) ? rawTabs : [])
        .map(sanitizeTab)
        .filter(Boolean) as ConversationTab[];
      const removedKeys = new Set(opt.removed.map((r) => r.key));
      const serverKeys = new Set(serverTabs.map((t) => t.key));
      const merged = serverTabs.filter((t) => !removedKeys.has(t.key));
      for (const a of opt.added) {
        if (!serverKeys.has(a.tab.key) && !removedKeys.has(a.tab.key)) merged.push(a.tab);
      }
      this.tabs = merged;
      const chosen = opt.activeKey || String(rawActiveKey || '');
      // 空激活是合法状态（设置/工作流等独立页面不属于任何标签）：
      // 服务端明确下空就必须无选中，不能回退到第一个标签
      this.activeKey = chosen && merged.some((t) => t.key === chosen) ? chosen : '';
      // new 标签计数器接续，避免 key 撞车
      for (const t of merged) {
        const m = /^new:(\d+)$/.exec(t.key);
        if (m) this._newCounter = Math.max(this._newCounter, Number(m[1]));
      }
    }
  }
});

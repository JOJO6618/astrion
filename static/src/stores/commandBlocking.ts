// static/src/stores/commandBlocking.ts - 个人级指令拦截状态
//
// 职责：
// - 对接个人指令拦截接口：GET/POST /api/command-blocking（当前登录用户/host Bearer，无管理员限制）
//   契约：{ success, enabled, rules[], recommended_rules[] }；POST 支持部分更新
//   （{ enabled? } / { rules? } 独立提交，未提供字段服务端保留）。
// - 管理规则编辑窗口（CommandBlockingDialog）的开关、草稿、加载/保存状态与错误。
//
// 语义约定（与后端契约对齐）：
// - 规则为「不区分大小写的文本包含」匹配；空规则列表不回退到推荐规则。
// - 推荐规则（recommended_rules）只是建议：只在用户点击「使用推荐的规则」时追加去重到草稿，
//   绝不自动写入规则、也不自动保存。
// - 开关 POST 只携带 enabled 单字段，响应只回写 enabled，绝不覆盖本地 rules/草稿
//   （避免与窗口内规则保存产生读写竞态）。
//
// 使用方：
// - settings/tabs/GeneralTab.vue（开关行 + 管理规则入口）
// - overlay/CommandBlockingDialog.vue（规则编辑窗口，由 App.vue 统一挂载，
//   设置路由 settingsRoute 下同样可见）
// - App.vue（QuickMenu → InputComposer 一路冒泡的 open-command-blocking 事件入口）
import { defineStore } from 'pinia';
import { t } from '@/locales';

interface CommandBlockingPayload {
  success?: boolean;
  enabled?: boolean;
  rules?: unknown;
  recommended_rules?: unknown;
}

interface CommandBlockingState {
  /** 服务端当前开关状态（默认开启） */
  enabled: boolean;
  /** 服务端当前规则列表 */
  rules: string[];
  /** 服务端推荐规则（仅作为建议展示，不自动生效） */
  recommendedRules: string[];
  /** 窗口内编辑中的规则草稿（每行一条，原始文本） */
  draft: string;
  dialogOpen: boolean;
  loading: boolean;
  saving: boolean;
  /** 开关 POST 进行中（防止连点产生并发开关请求） */
  toggling: boolean;
  /** 是否曾成功从服务器拉到状态（决定 GeneralTab 开关是否可交互） */
  loaded: boolean;
  /** 最近一次窗口加载失败：失败态下禁止保存（避免把空草稿覆盖服务器规则） */
  loadFailed: boolean;
  /** 最近一次错误（加载/保存/开关），保留并展示，不清除直到下一次成功或重试 */
  error: string;
  /** 读请求序号：并发/重复打开窗口时丢弃过期响应，仅最后一次生效 */
  fetchSeq: number;
}

const toStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((x) => String(x ?? '').trim()).filter(Boolean) : [];

/** 草稿文本 → 规则数组：逐行 trim、去空行、去重（匹配不区分大小写，去重同样不区分） */
const parseDraftLines = (text: string): string[] => {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of String(text || '').split('\n')) {
    const rule = line.trim();
    if (!rule) continue;
    const key = rule.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(rule);
  }
  return out;
};

async function requestCommandBlocking(options: RequestInit = {}): Promise<CommandBlockingPayload> {
  const response = await fetch('/api/command-blocking', {
    credentials: 'same-origin',
    ...options
  });
  const payload: CommandBlockingPayload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.success === false) {
    throw new Error(String((payload as any)?.error || `HTTP ${response.status}`));
  }
  if (
    payload.success !== true ||
    typeof payload.enabled !== 'boolean' ||
    !Array.isArray(payload.rules) ||
    !payload.rules.every((rule) => typeof rule === 'string') ||
    !Array.isArray(payload.recommended_rules) ||
    !payload.recommended_rules.every((rule) => typeof rule === 'string')
  ) {
    throw new Error(t('commandBlocking.invalidResponse'));
  }
  return payload;
}

export const useCommandBlockingStore = defineStore('commandBlocking', {
  state: (): CommandBlockingState => ({
    enabled: true,
    rules: [],
    recommendedRules: [],
    draft: '',
    dialogOpen: false,
    loading: false,
    saving: false,
    toggling: false,
    loaded: false,
    loadFailed: false,
    error: '',
    fetchSeq: 0
  }),

  getters: {
    /** 窗口内推荐规则中尚未出现在草稿里的部分（按钮置灰/计数用） */
    pendingRecommended(state): string[] {
      const existing = new Set(parseDraftLines(state.draft).map((r) => r.toLowerCase()));
      return state.recommendedRules.filter((r) => !existing.has(r.toLowerCase()));
    }
  },

  actions: {
    /** 拉取服务端状态。forDialog=true 时用结果重置窗口草稿（窗口每次打开都重新拉当前值）。 */
    async fetchState(forDialog = false): Promise<void> {
      if (this.toggling || this.saving) return;
      const seq = ++this.fetchSeq;
      this.loading = true;
      if (forDialog) {
        this.loadFailed = false;
        this.error = '';
      }
      try {
        const payload = await requestCommandBlocking();
        if (seq !== this.fetchSeq) return; // 已有更新的请求发出，丢弃过期响应
        if (typeof payload.enabled === 'boolean') this.enabled = payload.enabled;
        this.rules = toStringList(payload.rules);
        this.recommendedRules = toStringList(payload.recommended_rules);
        this.loaded = true;
        this.loadFailed = false;
        this.error = '';
        // 仅打开窗口时初始化草稿，后台刷新不能覆盖正在编辑的内容。
        if (forDialog && this.dialogOpen) this.draft = this.rules.join('\n');
      } catch (error: any) {
        if (seq !== this.fetchSeq) return;
        this.loadFailed = true;
        this.error = String(error?.message || error || 'request_failed');
      } finally {
        if (seq === this.fetchSeq) this.loading = false;
      }
    },

    /** 打开规则管理窗口：每次打开都从服务器重新拉取当前值（关闭即丢弃草稿） */
    openDialog(): void {
      if (this.saving || this.toggling || this.dialogOpen) return;
      this.dialogOpen = true;
      this.draft = '';
      void this.fetchState(true);
    },

    /** 关闭窗口（取消）：丢弃草稿，保留服务端状态与错误记录 */
    closeDialog(): void {
      if (this.saving) return;
      this.dialogOpen = false;
      this.draft = '';
      if (this.loading) {
        ++this.fetchSeq;
        this.loading = false;
      }
      this.loadFailed = false;
    },

    /** 开关：立即 POST 单字段 { enabled }；响应只回写 enabled，不触碰规则与草稿 */
    async setEnabled(value: boolean): Promise<void> {
      if (this.toggling || this.saving || this.loading || !this.loaded || this.loadFailed) return;
      const previous = this.enabled;
      this.enabled = value; // 乐观更新，失败回滚
      this.toggling = true;
      try {
        const payload = await requestCommandBlocking({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: value })
        });
        // 仅回写开关字段：rules 可能被窗口保存并发修改，此处绝不覆盖
        if (typeof payload.enabled === 'boolean') this.enabled = payload.enabled;
        this.loaded = true;
        this.error = '';
      } catch (error: any) {
        this.enabled = previous;
        this.error = String(error?.message || error || 'request_failed');
      } finally {
        this.toggling = false;
      }
    },

    /** 「使用推荐的规则」：仅把未出现的推荐规则追加去重到草稿，不自动保存 */
    applyRecommended(): void {
      const lines = parseDraftLines(this.draft);
      const seen = new Set(lines.map((r) => r.toLowerCase()));
      for (const rule of this.recommendedRules) {
        const key = rule.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        lines.push(rule);
      }
      this.draft = lines.join('\n');
    },

    /** 保存规则：POST 单字段 { rules }；成功后以服务器返回的规则重置草稿 */
    async saveRules(): Promise<void> {
      if (this.saving || this.toggling || this.loading || this.loadFailed || !this.loaded) return;
      const rules = parseDraftLines(this.draft);
      this.saving = true;
      try {
        const payload = await requestCommandBlocking({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rules })
        });
        this.rules = toStringList(payload.rules);
        this.recommendedRules = toStringList(payload.recommended_rules);
        // 开关可能被并发切换：仅在无开关请求进行时回写 enabled
        if (!this.toggling && typeof payload.enabled === 'boolean') this.enabled = payload.enabled;
        this.draft = this.rules.join('\n');
        this.error = '';
        this.dialogOpen = false;
      } catch (error: any) {
        // 保存失败：窗口保持打开、草稿保留、错误展示
        this.error = String(error?.message || error || 'request_failed');
      } finally {
        this.saving = false;
      }
    }
  }
});

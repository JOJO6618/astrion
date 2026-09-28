import { defineStore } from 'pinia';
import { usePersonalizationStore } from './personalization';

/**
 * 预览面板状态（2026-09-27 新增）
 * 数据源：任务事件流 preview_targets_updated（live=true 实时）+ 对话 bootstrap 回填（静态）。
 * 预览目标存于对话 metadata.preview_targets，按对话隔离。
 */

export interface PreviewTarget {
  type: 'server' | 'file';
  /** server：完整 URL；file：工作区相对路径 */
  url?: string;
  origin?: string;
  path?: string;
  label: string;
  /** link_click = 桌面端点击聊天中的 localhost 链接登记的临时目标（不写后端） */
  source?: 'file_edit' | 'model_output' | 'command_output' | 'link_click';
  ts?: string;
}

/** 与后端 modules/preview_targets._target_key 同格式 */
export const previewTargetKey = (t: PreviewTarget): string =>
  t.type === 'server' ? `server:${t.url || ''}` : `file:${t.path || ''}`;

interface PreviewState {
  targets: PreviewTarget[];
  /** 链接点击登记的临时服务器目标（不持久化、不出现在 dock 列表，随对话切换清除） */
  ephemeralTargets: PreviewTarget[];
  /** true = 来自任务期实时事件（可用于动画/自动展开）；false = 加载/bootstrap */
  targetsLive: boolean;
  /** 当前在预览面板中打开的目标 key（null = 面板关闭） */
  activeKey: string | null;
  /** 用户手动关闭过面板后，本次任务内不再自动展开（避免「关了又弹」） */
  autoOpenSuppressed: boolean;
  /** 上一次自动展开的目标 key：同一目标的内容更新不重复抢焦点 */
  lastAutoOpenedKey: string;
  /** 独立预览服务器基址（http://127.0.0.1:<port>）；空 = 远端访问回退旧同源端点 */
  previewBase: string;
  /** 当前对话的预览 token（路径鉴权用，随对话 metadata 持久化） */
  previewToken: string;
}

export const usePreviewStore = defineStore('preview', {
  state: (): PreviewState => ({
    targets: [],
    ephemeralTargets: [],
    targetsLive: false,
    activeKey: null,
    autoOpenSuppressed: false,
    lastAutoOpenedKey: '',
    previewBase: '',
    previewToken: ''
  }),
  getters: {
    activeTarget(state): PreviewTarget | null {
      if (!state.activeKey) return null;
      return (
        state.targets.find((t) => previewTargetKey(t) === state.activeKey) ||
        state.ephemeralTargets.find((t) => previewTargetKey(t) === state.activeKey) ||
        null
      );
    }
  },
  actions: {
    setTargets(list: PreviewTarget[] | null | undefined, live = false) {
      const prevKeys = new Set(this.targets.map(previewTargetKey));
      const seen = new Set<string>();
      const normalized: PreviewTarget[] = [];
      for (const item of Array.isArray(list) ? list : []) {
        if (!item || typeof item !== 'object') continue;
        const key = previewTargetKey(item);
        if (!key || key.endsWith(':') || seen.has(key)) continue;
        seen.add(key);
        normalized.push(item);
      }
      this.targetsLive = live;
      this.targets = normalized;
      // 列表移除正在预览的目标时，同步关闭面板（临时目标不受影响）
      if (
        this.activeKey &&
        !seen.has(this.activeKey) &&
        !this.ephemeralTargets.some((t) => previewTargetKey(t) === this.activeKey)
      ) {
        this.activeKey = null;
      }
      // 实时新增目标：按设置决定是否自动展开（最新优先）
      if (live) {
        const added = normalized.filter((t) => !prevKeys.has(previewTargetKey(t)));
        if (added.length) {
          this.maybeAutoOpen(added[added.length - 1]);
        }
      }
    },
    maybeAutoOpen(target: PreviewTarget) {
      const personalization = usePersonalizationStore();
      if (personalization.form.preview_auto_open !== true) return;
      if (this.autoOpenSuppressed) return;
      const key = previewTargetKey(target);
      if (!key || key === this.lastAutoOpenedKey) return;
      this.lastAutoOpenedKey = key;
      this.activeKey = key;
    },
    openTarget(key: string) {
      if (!key) return;
      // 再点同一目标 = 收起
      this.activeKey = this.activeKey === key ? null : key;
    },
    /** 桌面端点击聊天中的 localhost 链接：打开窗口内预览面板（而不是让壳拦去系统浏览器）。
     *  已登记的目标直接激活；未登记的记为临时目标（不写后端 metadata）。 */
    openServerUrl(url: unknown) {
      const clean = String(url || '').trim();
      if (!clean) return;
      const key = `server:${clean}`;
      const exists =
        this.targets.some((t) => previewTargetKey(t) === key) ||
        this.ephemeralTargets.some((t) => previewTargetKey(t) === key);
      if (!exists) {
        // 标签与后端 _server_label 同风格：:端口[/路径]
        const label = clean.replace(/^https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])/i, '') || clean;
        this.ephemeralTargets.push({
          type: 'server',
          url: clean,
          label,
          source: 'link_click'
        });
      }
      this.activeKey = key;
    },
    /** 记录预览运行时（独立预览服务器基址 + 对话 token），由 targets/bootstrap/messages 响应下发 */
    setRuntime(base: unknown, token: unknown) {
      if (typeof base === 'string' && base) this.previewBase = base;
      if (typeof token === 'string' && token) this.previewToken = token;
    },
    /** 推导目标的 iframe 地址：有独立预览服务器时走隔离源，否则回退旧同源端点 */
    previewUrlFor(target: PreviewTarget, hostMode: boolean): string {
      const isolated = this.previewBase && this.previewToken;
      if (target.type === 'file') {
        const encoded = (target.path || '')
          .split('/')
          .map((seg) => encodeURIComponent(seg))
          .join('/');
        return isolated
          ? `${this.previewBase}/${this.previewToken}/file/${encoded}`
          : `/api/preview/file/${encoded}`;
      }
      const url = target.url || '';
      // host 模式：浏览器与服务器同机，直连 localhost（保真，HMR 可用；与主应用天然跨源）
      if (hostMode) return url;
      const m = url.match(/^https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::(\d+))?(\/[^\s]*)?$/i);
      if (!m) return url;
      const port = m[1] || '80';
      const path = m[2] || '/';
      return isolated
        ? `${this.previewBase}/${this.previewToken}/proxy/${port}${path}`
        : `/api/preview/proxy/${port}${path}`;
    },
    closePanel() {
      this.activeKey = null;
      // 用户主动关闭：本次任务内不再自动展开
      this.autoOpenSuppressed = true;
    },
    /** 新任务开始时调用：重置自动展开抑制 */
    resetAutoOpen() {
      this.autoOpenSuppressed = false;
    },
    /** 切换对话/空对话时清空瞬态（token 按对话隔离，必须随对话清除） */
    resetTransient() {
      this.activeKey = null;
      this.autoOpenSuppressed = false;
      this.lastAutoOpenedKey = '';
      this.previewBase = '';
      this.previewToken = '';
      this.ephemeralTargets = [];
    },
    async removeTarget(key: string) {
      try {
        const resp = await fetch('/api/preview/targets/remove', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key })
        });
        const payload = await resp.json().catch(() => ({}));
        if (resp.ok && payload?.success) {
          this.setTargets(
            Array.isArray(payload.preview_targets) ? payload.preview_targets : [],
            false
          );
        }
      } catch {
        // 删除失败不影响界面现状
      }
    }
  }
});

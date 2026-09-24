import { defineStore } from 'pinia';
import { useResourceStore } from './resource';

/**
 * 设置页（全屏）状态：当前分区 + 管理员可见性判定。
 *
 * 说明：
 * - 设置页壳组件为 static/src/components/settings/SettingsShell.vue（由 App.vue 挂载，接线不在本文件）。
 * - isAdmin / isHostMode 判定逻辑与原 PersonalizationDrawer.vue 保持一致：
 *   配额角色（usageQuota.role）或会话角色（/api/session-status）任一为 admin 即视为管理员。
 * - 会话状态只拉取一次（sessionStatusLoaded 去重），与个人空间抽屉共享同一份判定结果。
 */

/** 设置页分区 ID（与 SettingsShell 左导航一致） */
export type SettingsSection =
  // 模型
  | 'providers'
  | 'models'
  | 'model-pref'
  | 'codex'
  // 系统
  | 'general'
  | 'workspace'
  | 'tools'
  | 'context'
  | 'files'
  | 'voice'
  // 智能体
  | 'sub-agents'
  | 'review-agents'
  // 界面
  | 'appearance'
  // 管理
  | 'admin';

/** 仅管理员可见的分区（providers：连接/断开提供商是管理员操作） */
export const SETTINGS_ADMIN_SECTIONS: ReadonlySet<SettingsSection> = new Set([
  'providers',
  'codex',
  'admin'
]);

interface SettingsState {
  activeSection: SettingsSection;
  /** /api/session-status 快照：登录角色（'' = 未知/未登录） */
  sessionRole: string;
  /** /api/session-status 快照：host 模式 */
  sessionHostMode: boolean;
  /** 会话状态是否已成功拉取（去重，避免每个宿主组件重复请求） */
  sessionStatusLoaded: boolean;
}

export const useSettingsStore = defineStore('settings', {
  state: (): SettingsState => ({
    activeSection: 'general',
    sessionRole: '',
    sessionHostMode: false,
    sessionStatusLoaded: false
  }),
  getters: {
    /** 管理员判定（沿用原 PersonalizationDrawer.vue 逻辑） */
    isAdmin(state): boolean {
      const resourceStore = useResourceStore();
      const quotaRole = (resourceStore.usageQuota.role || '').toLowerCase();
      const loginRole = (state.sessionRole || '').toLowerCase();
      return quotaRole === 'admin' || loginRole === 'admin';
    },
    /** host 模式判定（沿用原 PersonalizationDrawer.vue 逻辑） */
    isHostMode(state): boolean {
      const resourceStore = useResourceStore();
      const mode = (resourceStore.containerStatus?.mode || '').toLowerCase();
      if (mode) {
        return mode === 'host';
      }
      return state.sessionHostMode;
    },
    /** MCP 配置入口可见性（管理员 + host 模式） */
    showMcpConfigEntry(): boolean {
      return this.isAdmin && this.isHostMode;
    }
  },
  actions: {
    setActiveSection(section: SettingsSection) {
      this.activeSection = section;
    },
    /** 拉取会话状态（管理员/host 判定来源）；默认只拉一次，force 可强制刷新 */
    async fetchSessionStatus(force = false) {
      if (this.sessionStatusLoaded && !force) {
        return;
      }
      try {
        const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
        if (!resp.ok) return;
        const payload = await resp.json();
        const snapshot = payload?.session || {};
        this.sessionRole = String(snapshot.role || '');
        this.sessionHostMode = !!snapshot.host_mode;
        this.sessionStatusLoaded = true;
      } catch {
        this.sessionRole = '';
      }
    }
  }
});

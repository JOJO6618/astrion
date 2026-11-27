import { defineStore } from 'pinia';

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
  // 账户
  | 'usage'
  | 'storage'
  // 模型
  | 'providers'
  | 'models'
  | 'model-pref'
  | 'codex'
  // 系统
  | 'general'
  | 'chat'
  | 'quick-chat'
  | 'workspace'
  | 'search'
  | 'tools'
  | 'mcp'
  | 'context'
  | 'files'
  | 'browser'
  // 智能体
  | 'sub-agents'
  | 'review-agents'
  // 界面
  | 'appearance';

/** 仅管理员可见的分区（providers：连接/断开提供商是管理员操作） */
export const SETTINGS_ADMIN_SECTIONS: ReadonlySet<SettingsSection> = new Set([
  'providers',
  'codex'
]);

interface SettingsState {
  activeSection: SettingsSection;
  /** /api/session-status 快照：登录角色（'' = 未知/未登录） */
  sessionRole: string;
  /** 会话状态是否已成功拉取（去重，避免每个宿主组件重复请求） */
  sessionStatusLoaded: boolean;
}

export const useSettingsStore = defineStore('settings', {
  state: (): SettingsState => ({
    activeSection: 'general',
    sessionRole: '',
    sessionStatusLoaded: false
  }),
  getters: {
    /** 管理员判定：Host 登录会话的角色（Host 密码登录为 admin）。 */
    isAdmin(state): boolean {
      return (state.sessionRole || '').toLowerCase() === 'admin';
    },
    /** Host-only 主仓：设置页始终按本机 Host 模式展示。 */
    isHostMode(): boolean {
      return true;
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
        this.sessionStatusLoaded = true;
      } catch {
        this.sessionRole = '';
      }
    }
  }
});

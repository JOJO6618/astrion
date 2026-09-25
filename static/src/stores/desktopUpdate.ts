// static/src/stores/desktopUpdate.ts - 桌面端软件更新状态
//
// 职责：
// - 仅桌面壳内启用（壳 initialization_script 注入 window.__ASTRION_DESKTOP__）；
//   Web/移动浏览器环境不检测、不显示入口
// - 启动静默检查一次（侧边栏挂载时触发）：发现新版本只亮红点，不弹窗打扰
// - 手动检查（弹窗打开时强制回源）+ 无感更新安装（进度 1s 轮询直到应用重启）
//
// 后端链路：/api/desktop/update/* → 壳控制桥 / 官网更新清单（见 server/status/desktop_update.py）
import { defineStore } from 'pinia';

const PROGRESS_POLL_MS = 1000;

export type UpdateCheckData = {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  notes: string;
  pub_date: string;
  bridge_available: boolean;
  checked_at: number;
};

export type UpdateProgressState =
  | 'idle'
  | 'checking'
  | 'downloading'
  | 'installing'
  | 'restarting'
  | 'no_update'
  | 'error';

export type UpdateProgress = {
  state: UpdateProgressState;
  downloaded: number;
  total: number | null;
  error: string | null;
};

async function parseJson(resp: Response): Promise<any | null> {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

export const useDesktopUpdateStore = defineStore('desktopUpdate', {
  state: () => ({
    /** 是否桌面壳环境（一次性检测，常量） */
    isDesktop: Boolean((window as any).__ASTRION_DESKTOP__),
    /** 弹窗开关 */
    dialogOpen: false,
    /** 检查中（启动静默检查也置位，但无 UI 展示） */
    checking: false,
    /** 已至少成功/失败检查过一次（红点逻辑据此避免误亮） */
    checked: false,
    /** 最近一次检查结果（null = 尚未检查成功） */
    result: null as UpdateCheckData | null,
    /** 最近一次检查失败信息（仅弹窗内展示） */
    checkError: '',
    /** 安装进度（安装中由轮询驱动） */
    progress: null as UpdateProgress | null,
    /** 安装轮询定时器 */
    _progressTimer: 0 as number,
  }),

  getters: {
    /** 有可用新版本（驱动侧边栏红点） */
    updateAvailable(state): boolean {
      return Boolean(state.result?.update_available);
    },
    /** 安装流程进行中（下载/安装/重启，弹窗禁止关闭） */
    installBusy(state): boolean {
      const s = state.progress?.state;
      return s === 'checking' || s === 'downloading' || s === 'installing' || s === 'restarting';
    },
  },

  actions: {
    /** 检查更新。silent=true 为启动静默检查：失败只记录不展示。 */
    async checkUpdate(opts: { silent?: boolean; force?: boolean } = {}) {
      if (!this.isDesktop || this.checking) return;
      this.checking = true;
      if (!opts.silent) this.checkError = '';
      try {
        const url = `/api/desktop/update/check${opts.force ? '?force=1' : ''}`;
        const resp = await fetch(url, { credentials: 'same-origin' });
        const payload = await parseJson(resp);
        if (payload?.success && payload.data) {
          this.result = payload.data as UpdateCheckData;
          this.checked = true;
          this.checkError = '';
        } else if (!opts.silent) {
          this.checkError = payload?.error || 'check_failed';
        }
      } catch {
        if (!opts.silent) this.checkError = 'network_error';
      } finally {
        this.checking = false;
      }
    },

    openDialog() {
      this.dialogOpen = true;
      // 打开弹窗即视为一次手动检查（强制回源，绕过 30s 缓存）
      this.checkUpdate({ force: true });
    },

    closeDialog() {
      if (this.installBusy) return; // 安装中禁止关闭（重启前最后状态可见）
      this.dialogOpen = false;
    },

    /** 触发无感更新：壳侧下载 → 验签 → 安装 → 自动重启。 */
    async startInstall() {
      if (!this.isDesktop || this.installBusy) return;
      this.progress = { state: 'checking', downloaded: 0, total: null, error: null };
      try {
        const resp = await fetch('/api/desktop/update/install', {
          method: 'POST',
          credentials: 'same-origin',
        });
        const payload = await parseJson(resp);
        if (!payload?.success) {
          this.progress = {
            state: 'error',
            downloaded: 0,
            total: null,
            error: payload?.error || payload?.data?.error || 'install_rejected',
          };
          return;
        }
      } catch {
        this.progress = { state: 'error', downloaded: 0, total: null, error: 'network_error' };
        return;
      }
      this._startProgressPoll();
    },

    _startProgressPoll() {
      this._stopProgressPoll();
      this._progressTimer = window.setInterval(async () => {
        try {
          const resp = await fetch('/api/desktop/update/progress', { credentials: 'same-origin' });
          const payload = await parseJson(resp);
          if (payload?.success && payload.data) {
            this.progress = payload.data as UpdateProgress;
            // restarting 之后应用随时退出；error / no_update 收尾停轮询
            const s = this.progress.state;
            if (s === 'error' || s === 'no_update' || s === 'idle') {
              this._stopProgressPoll();
            }
          }
        } catch {
          // 重启过程中后端先死，请求失败属预期——保持 restarting 展示，不判错误
        }
      }, PROGRESS_POLL_MS);
    },

    _stopProgressPoll() {
      if (this._progressTimer) {
        window.clearInterval(this._progressTimer);
        this._progressTimer = 0;
      }
    },
  },
});

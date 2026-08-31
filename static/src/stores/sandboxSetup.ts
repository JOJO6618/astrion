// static/src/stores/sandboxSetup.ts - 沙箱环境检测与一键安装向导状态
//
// 职责：
// - 进页面自动检测（Windows 宿主机模式且沙箱未就绪时弹出安装向导，不分 sandbox/direct 执行环境）
// - 「暂不安装」= 12 小时内不再弹（sessionStorage 时间戳）；「不再提示」= 持久关闭（localStorage）
// - 安装任务进度轮询（1s），完成后刷新检测状态
//
// 使用方：app/lifecycle.ts（自动检测）、overlay/SandboxSetupDialog.vue（弹窗）、
//        personalization/tabs/GeneralTab.vue（沙箱环境区块）
import { defineStore } from 'pinia';

const NEVER_KEY = 'agents_sandbox_setup_never';
const SNOOZE_KEY = 'agents_sandbox_setup_snoozed';
const POLL_INTERVAL_MS = 1000;
/** 「暂不安装」免打扰窗口：12 小时。
 *  承诺语义是「本次会话不再弹、下次进页面仍提示」，但 sessionStorage 生命周期
 *  是标签页会话——标签页长期不关会永久拦截弹窗（实测踩坑：用户卸载 WSL 重测时
 *  被数小时前点的「暂不安装」挡住）。时间戳窗口是对该语义的诚实实现；
 *  旧值 '1' 会被解析为 1970 年时间戳而必然过期，旧拦截自动失效。 */
const SNOOZE_WINDOW_MS = 12 * 60 * 60 * 1000;

function readSnoozed(): boolean {
  try {
    const raw = sessionStorage.getItem(SNOOZE_KEY);
    if (!raw) return false;
    const at = Number(raw);
    if (!Number.isFinite(at) || Date.now() - at > SNOOZE_WINDOW_MS) {
      sessionStorage.removeItem(SNOOZE_KEY);
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

export type SandboxState =
  | 'ready'
  | 'wsl_missing'
  | 'distro_missing'
  | 'bwrap_missing'
  | 'not_applicable'
  | 'error';

export interface SandboxStatus {
  applicable: boolean;
  platform: string;
  state: SandboxState;
  distro_name: string;
  detail: string;
  setup_running: boolean;
}

export type SetupPhase =
  | 'idle'
  | 'enabling_wsl'
  | 'installing_wsl'
  | 'installing'
  | 'verifying'
  | 'done'
  | 'needs_reboot'
  | 'error';

export interface SetupProgress {
  active: boolean;
  phase: SetupPhase;
  step_index: number;
  step_total: number;
  step_title: string;
  log_tail: string[];
  download_bytes: number | null;
  download_total: number | null;
  error: string | null;
  error_kind: string | null;
  updated_at: number;
}

interface SandboxSetupState {
  status: SandboxStatus | null;
  checking: boolean;
  dialogVisible: boolean;
  progress: SetupProgress | null;
  starting: boolean;
  snoozed: boolean;
  pollTimer: ReturnType<typeof setInterval> | null;
}

export const useSandboxSetupStore = defineStore('sandboxSetup', {
  state: (): SandboxSetupState => ({
    status: null,
    checking: false,
    dialogVisible: false,
    progress: null,
    starting: false,
    snoozed: readSnoozed(),
    pollTimer: null
  }),

  getters: {
    neverAsk(): boolean {
      try {
        return localStorage.getItem(NEVER_KEY) === '1';
      } catch {
        return false;
      }
    },
    /** 沙箱未就绪（仅统计 applicable 且非 ready 的三种缺失状态） */
    missing(): boolean {
      const s = this.status;
      if (!s || !s.applicable) return false;
      return s.state === 'wsl_missing' || s.state === 'distro_missing' || s.state === 'bwrap_missing';
    },
    /** 进页面是否应自动弹出向导 */
    shouldAutoPrompt(): boolean {
      if (this.dialogVisible || this.snoozed || this.neverAsk) return false;
      if (this.progress?.active) return true; // 刷新页面时安装仍在进行，恢复弹窗
      return this.missing;
    },
    /** 安装是否处于终态（done/needs_reboot/error） */
    setupFinished(): boolean {
      const p = this.progress;
      return !!p && !p.active && p.phase !== 'idle';
    }
  },

  actions: {
    async fetchStatus(force = false): Promise<void> {
      if (this.checking) return;
      this.checking = true;
      try {
        const resp = await fetch(`/api/sandbox/status${force ? '?force=1' : ''}`, {
          credentials: 'same-origin'
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data?.success) {
          this.status = data.data as SandboxStatus;
        }
      } catch {
        // 检测失败保持沉默（不打扰正常使用）
      } finally {
        this.checking = false;
      }
    },

    /** 进页面自动检测：未就绪且未被打断偏好时弹出向导 */
    async autoCheck(): Promise<void> {
      await this.fetchStatus();
      // 安装任务可能因页面刷新而仍在后台运行：恢复进度并重新打开弹窗
      if (this.status?.setup_running) {
        this.dialogVisible = true;
        this.startPolling();
        return;
      }
      if (this.shouldAutoPrompt) {
        this.dialogVisible = true;
      }
    },

    async recheck(): Promise<void> {
      await this.fetchStatus(true);
    },

    openWizard(): void {
      this.dialogVisible = true;
      if (this.progress?.active) this.startPolling();
      if (!this.status) void this.fetchStatus();
    },

    /** 「暂不安装」：12 小时内不再自动弹出 */
    snooze(): void {
      this.snoozed = true;
      try {
        sessionStorage.setItem(SNOOZE_KEY, String(Date.now()));
      } catch {
        /* ignore */
      }
      this.dialogVisible = false;
    },

    setNeverAsk(): void {
      try {
        localStorage.setItem(NEVER_KEY, '1');
      } catch {
        /* ignore */
      }
    },

    resetNeverAsk(): void {
      try {
        localStorage.removeItem(NEVER_KEY);
      } catch {
        /* ignore */
      }
    },

    /** 「不再提示」勾选后随暂不安装一起生效；安装中不允许关闭 */
    closeDialog(never: boolean): void {
      if (this.progress?.active) return;
      if (never) this.setNeverAsk();
      this.snooze();
    },

    async startSetup(): Promise<void> {
      if (this.starting || this.progress?.active) return;
      this.starting = true;
      try {
        const resp = await fetch('/api/sandbox/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ enable_wsl_if_needed: true })
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data?.success) {
          this.progress = data.data as SetupProgress;
          this.startPolling();
        } else {
          // 启动失败（如并发）：以进度对象呈现错误
          this.progress = {
            active: false,
            phase: 'error',
            step_index: 0,
            step_total: 6,
            step_title: '',
            log_tail: [],
            download_bytes: null,
            download_total: null,
            error: data?.error || 'start failed',
            error_kind: 'start_failed',
            updated_at: Date.now() / 1000
          };
        }
      } catch {
        this.progress = {
          active: false,
          phase: 'error',
          step_index: 0,
          step_total: 6,
          step_title: '',
          log_tail: [],
          download_bytes: null,
          download_total: null,
          error: 'network error',
          error_kind: 'start_failed',
          updated_at: Date.now() / 1000
        };
      } finally {
        this.starting = false;
      }
    },

    startPolling(): void {
      if (this.pollTimer) return;
      void this.pollOnce();
      this.pollTimer = setInterval(() => void this.pollOnce(), POLL_INTERVAL_MS);
    },

    stopPolling(): void {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    },

    async pollOnce(): Promise<void> {
      try {
        const resp = await fetch('/api/sandbox/setup/status', { credentials: 'same-origin' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data?.success) return;
        this.progress = data.data as SetupProgress;
        if (!this.progress.active) {
          this.stopPolling();
          if (this.progress.phase === 'done') {
            // 安装成功：刷新检测状态（应为 ready）
            void this.fetchStatus(true);
          }
        }
      } catch {
        /* 轮询失败静默，下个周期重试 */
      }
    },

    /** 安装失败/需重启后重试：清空进度重新发起 */
    async retrySetup(): Promise<void> {
      this.progress = null;
      await this.startSetup();
    }
  }
});

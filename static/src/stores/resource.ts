import { defineStore } from 'pinia';

interface ConversationTokens {
  cumulative_input_tokens: number;
  cumulative_output_tokens: number;
  cumulative_total_tokens: number;
}

interface ProjectStorage {
  used_bytes: number;
  limit_bytes: number | null;
  limit_label: string;
  usage_percent: number | null;
}

interface ContainerSample {
  timestamp: number;
  rx_bytes: number | null;
  tx_bytes: number | null;
}

interface ContainerNetRate {
  down_bps: number | null;
  up_bps: number | null;
}

interface QuotaTier {
  count: number;
  limit: number;
  reset_at: number | null;
  window_start: number | null;
}

interface UsageQuota {
  fast: QuotaTier;
  thinking: QuotaTier;
  search: QuotaTier;
  role: string;
}

const defaultQuotaTier = (): QuotaTier => ({
  count: 0,
  limit: 0,
  reset_at: null,
  window_start: null
});

const buildDefaultQuota = (): UsageQuota => ({
  fast: defaultQuotaTier(),
  thinking: defaultQuotaTier(),
  search: defaultQuotaTier(),
  role: 'user'
});

const PROJECT_STORAGE_POLL_INTERVAL_MS = 60_000;
const PROJECT_STORAGE_POLL_INTERVAL_MAX_MS = 300_000;
const CONTAINER_STATS_POLL_INTERVAL_MS = 5_000;
const CONTAINER_STATS_POLL_INTERVAL_MAX_MS = 30_000;

export const useResourceStore = defineStore('resource', {
  state: () => ({
    tokenPanelCollapsed: true,
    currentContextTokens: 0,
    currentConversationTokens: {
      cumulative_input_tokens: 0,
      cumulative_output_tokens: 0,
      cumulative_total_tokens: 0
    } as ConversationTokens,
    projectStorage: {
      used_bytes: 0,
      limit_bytes: null,
      limit_label: '',
      usage_percent: null
    } as ProjectStorage,
    containerStatus: null as Record<string, any> | null,
    containerNetRate: {
      down_bps: null,
      up_bps: null
    } as ContainerNetRate,
    containerVisibilityBound: false,
    lastContainerSample: null as ContainerSample | null,
    containerStatsInFlight: false,
    containerStatsFailCount: 0,
    containerStatsIntervalMs: CONTAINER_STATS_POLL_INTERVAL_MS,
    containerStatsTimer: null as ReturnType<typeof setInterval> | null,
    projectStorageTimer: null as ReturnType<typeof setInterval> | null,
    projectStorageInFlight: false,
    projectStorageFailCount: 0,
    projectStorageIntervalMs: PROJECT_STORAGE_POLL_INTERVAL_MS,
    usageQuota: buildDefaultQuota(),
    usageQuotaTimer: null as ReturnType<typeof setInterval> | null
  }),
  actions: {
    resetTokenStatistics() {
      this.currentContextTokens = 0;
      this.currentConversationTokens = {
        cumulative_input_tokens: 0,
        cumulative_output_tokens: 0,
        cumulative_total_tokens: 0
      };
    },
    setCurrentContextTokens(value: number) {
      this.currentContextTokens = value || 0;
    },
    toggleTokenPanel() {
      this.tokenPanelCollapsed = !this.tokenPanelCollapsed;
      if (this.tokenPanelCollapsed) {
        this.stopContainerStatsPolling();
      } else {
        this.startContainerStatsPolling();
      }
    },
    async updateCurrentContextTokens(conversationId: string | null) {
      if (!conversationId) {
        this.currentContextTokens = 0;
        return;
      }
      try {
        const response = await fetch(`/api/conversations/${conversationId}/tokens`);
        const data = await response.json();
        if (data.success && data.data) {
          this.currentContextTokens = data.data.total_tokens || 0;
        } else {
          this.currentContextTokens = 0;
        }
      } catch (error) {
        console.warn('获取当前上下文Token异常:', error);
        this.currentContextTokens = 0;
      }
    },
    async fetchConversationTokenStatistics(conversationId: string | null) {
      if (!conversationId) {
        this.resetTokenStatistics();
        return;
      }
      try {
        const response = await fetch(`/api/conversations/${conversationId}/token-statistics`);
        const data = await response.json();
        if (data.success && data.data) {
          this.currentConversationTokens.cumulative_input_tokens =
            data.data.total_input_tokens || 0;
          this.currentConversationTokens.cumulative_output_tokens =
            data.data.total_output_tokens || 0;
          this.currentConversationTokens.cumulative_total_tokens = data.data.total_tokens || 0;
          if (typeof data.data.current_context_tokens === 'number') {
            this.currentContextTokens = data.data.current_context_tokens;
          }
        }
      } catch (error) {
        console.warn('获取Token统计异常:', error);
      }
    },
    applyStatusSnapshot(status: any) {
      if (!status || typeof status !== 'object') {
        return;
      }
      if (status.project) {
        const project = status.project;
        this.projectStorage.used_bytes = project.total_size || 0;
        this.projectStorage.limit_bytes = project.limit_bytes ?? null;
        this.projectStorage.limit_label =
          project.limit_label ||
          (project.limit_bytes
            ? `${(project.limit_bytes / (1024 * 1024)).toFixed(0)} MB`
            : '未限制');
        if (project.limit_bytes) {
          const pct =
            typeof project.usage_percent === 'number'
              ? project.usage_percent
              : (project.total_size / project.limit_bytes) * 100;
          this.projectStorage.usage_percent = pct;
        } else {
          this.projectStorage.usage_percent = null;
        }
      }
      if (Object.prototype.hasOwnProperty.call(status, 'container')) {
        this.updateContainerStatus(status.container);
      }
    },
    updateContainerStatus(status: any) {
      if (!status || status.mode !== 'docker') {
        this.containerStatus = status || null;
        this.containerNetRate = { down_bps: null, up_bps: null };
        this.lastContainerSample = null;
        return;
      }
      const stats = status.stats;
      if (stats && typeof stats.timestamp === 'number') {
        const currentSample: ContainerSample = {
          timestamp: stats.timestamp,
          rx_bytes:
            stats.net_io && typeof stats.net_io.rx_bytes === 'number'
              ? stats.net_io.rx_bytes
              : null,
          tx_bytes:
            stats.net_io && typeof stats.net_io.tx_bytes === 'number' ? stats.net_io.tx_bytes : null
        };
        const last = this.lastContainerSample;
        if (
          last &&
          currentSample.rx_bytes !== null &&
          currentSample.tx_bytes !== null &&
          last.rx_bytes !== null &&
          last.tx_bytes !== null
        ) {
          const deltaT = Math.max(0.001, currentSample.timestamp - last.timestamp);
          const downRate = Math.max(0, (currentSample.rx_bytes - last.rx_bytes) / deltaT);
          const upRate = Math.max(0, (currentSample.tx_bytes - last.tx_bytes) / deltaT);
          this.containerNetRate = {
            down_bps: downRate,
            up_bps: upRate
          };
        } else {
          this.containerNetRate = { down_bps: null, up_bps: null };
        }
        this.lastContainerSample = currentSample;
      } else {
        this.containerNetRate = { down_bps: null, up_bps: null };
        this.lastContainerSample = null;
      }
      this.containerStatus = status;
    },
    async pollContainerStats() {
      if (typeof document !== 'undefined' && document.hidden) {
        return;
      }
      if (this.containerStatsInFlight) {
        return;
      }
      this.containerStatsInFlight = true;
      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const timeoutId = controller ? window.setTimeout(() => controller.abort(), 4000) : null;
      try {
        const response = await fetch('/api/container-status', {
          signal: controller?.signal
        });
        if (!response.ok) {
          throw new Error(`http-${response.status}`);
        }
        const data = await response.json();
        if (data.success) {
          this.updateContainerStatus(data.data || null);
        }
        this.containerStatsFailCount = 0;
        if (this.containerStatsIntervalMs > CONTAINER_STATS_POLL_INTERVAL_MS) {
          this._restartContainerStatsTimer(CONTAINER_STATS_POLL_INTERVAL_MS);
        }
      } catch (error) {
        console.warn('获取容器状态异常:', error);
        this.containerStatsFailCount += 1;
        if (this.containerStatsFailCount >= 3) {
          const nextInterval = Math.min(
            this.containerStatsIntervalMs * 2,
            CONTAINER_STATS_POLL_INTERVAL_MAX_MS
          );
          this._restartContainerStatsTimer(nextInterval);
        }
      } finally {
        this.containerStatsInFlight = false;
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
      }
    },
    startContainerStatsPolling() {
      if (this.containerStatsTimer) {
        return;
      }
      if (this.tokenPanelCollapsed) {
        return;
      }
      this.containerStatsFailCount = 0;
      this.containerStatsIntervalMs = CONTAINER_STATS_POLL_INTERVAL_MS;
      this.pollContainerStats();
      this.containerStatsTimer = setInterval(() => {
        this.pollContainerStats();
      }, this.containerStatsIntervalMs);
    },
    stopContainerStatsPolling() {
      if (this.containerStatsTimer) {
        clearInterval(this.containerStatsTimer);
        this.containerStatsTimer = null;
      }
      this.containerStatsInFlight = false;
    },
    _restartContainerStatsTimer(interval: number) {
      if (this.containerStatsTimer) {
        clearInterval(this.containerStatsTimer);
        this.containerStatsTimer = null;
      }
      this.containerStatsIntervalMs = interval;
      this.containerStatsTimer = setInterval(() => {
        this.pollContainerStats();
      }, this.containerStatsIntervalMs);
    },
    bindContainerVisibilityWatcher() {
      if (this.containerVisibilityBound || typeof document === 'undefined') {
        return;
      }
      const handler = () => {
        if (document.hidden) {
          this.stopContainerStatsPolling();
        } else {
          this.containerStatsFailCount = 0;
          this.containerStatsIntervalMs = CONTAINER_STATS_POLL_INTERVAL_MS;
          this.startContainerStatsPolling();
        }
      };
      document.addEventListener('visibilitychange', handler);
      this.containerVisibilityBound = true;
    },
    async pollProjectStorage() {
      if (this.projectStorageInFlight) {
        return;
      }
      this.projectStorageInFlight = true;
      try {
        const resp = await fetch('/api/project-storage');
        if (!resp.ok) {
          throw new Error(resp.statusText || '请求失败');
        }
        const data = await resp.json();
        if (data && data.success && data.data) {
          this.projectStorage.used_bytes = data.data.used_bytes || 0;
          this.projectStorage.limit_bytes = data.data.limit_bytes ?? null;
          this.projectStorage.limit_label = data.data.limit_label || '';
          this.projectStorage.usage_percent = data.data.usage_percent ?? null;
          this.projectStorageFailCount = 0;
          if (this.projectStorageIntervalMs > PROJECT_STORAGE_POLL_INTERVAL_MS) {
            this._restartProjectStorageTimer(PROJECT_STORAGE_POLL_INTERVAL_MS);
          }
        }
      } catch (err) {
        this.projectStorageFailCount += 1;
        console.warn('获取存储信息失败:', err);
        if (this.projectStorageFailCount >= 3) {
          const slower = Math.min(
            this.projectStorageIntervalMs * 2,
            PROJECT_STORAGE_POLL_INTERVAL_MAX_MS
          );
          if (slower !== this.projectStorageIntervalMs) {
            console.warn(`连续获取失败，存储轮询间隔调整为 ${Math.round(slower / 1000)} 秒`);
            this._restartProjectStorageTimer(slower);
          }
        }
      } finally {
        this.projectStorageInFlight = false;
      }
    },
    startProjectStoragePolling() {
      if (this.projectStorageTimer) {
        return;
      }
      this.pollProjectStorage();
      this.projectStorageTimer = setInterval(() => {
        this.pollProjectStorage();
      }, this.projectStorageIntervalMs);
    },
    stopProjectStoragePolling() {
      if (this.projectStorageTimer) {
        clearInterval(this.projectStorageTimer);
        this.projectStorageTimer = null;
      }
      this.projectStorageInFlight = false;
      this.projectStorageFailCount = 0;
      this.projectStorageIntervalMs = PROJECT_STORAGE_POLL_INTERVAL_MS;
    },
    _restartProjectStorageTimer(intervalMs: number) {
      if (this.projectStorageTimer) {
        clearInterval(this.projectStorageTimer);
      }
      this.projectStorageIntervalMs = Math.max(intervalMs, 10_000);
      this.projectStorageTimer = setInterval(() => {
        this.pollProjectStorage();
      }, this.projectStorageIntervalMs);
    },
    normalizeUsageQuota(raw: any) {
      const base = buildDefaultQuota();
      const quotas = (raw && raw.quotas) || {};
      const role = quotas.role || (raw && raw.role) || 'user';
      return {
        fast: { ...base.fast, ...(quotas.fast || {}) },
        thinking: { ...base.thinking, ...(quotas.thinking || {}) },
        search: { ...base.search, ...(quotas.search || {}) },
        role
      };
    },
    setUsageQuota(raw: any) {
      this.usageQuota = this.normalizeUsageQuota(raw);
    },
    async fetchUsageQuota() {
      try {
        const response = await fetch('/api/usage');
        if (!response.ok) {
          throw new Error(response.statusText || '请求失败');
        }
        const data = await response.json();
        if (data && data.success && data.data) {
          this.setUsageQuota(data.data);
        }
      } catch (error) {
        console.warn('获取用量配额失败:', error);
      }
    },
    startUsageQuotaPolling() {
      if (this.usageQuotaTimer) {
        return;
      }
      this.fetchUsageQuota();
      this.usageQuotaTimer = setInterval(() => {
        this.fetchUsageQuota();
      }, 60000);
    },
    stopUsageQuotaPolling() {
      if (this.usageQuotaTimer) {
        clearInterval(this.usageQuotaTimer);
        this.usageQuotaTimer = null;
      }
    }
  }
});

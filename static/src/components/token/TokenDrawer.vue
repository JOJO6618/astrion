<template>
  <div class="token-drawer" v-if="visible" :class="{ collapsed }" data-tutorial="token-drawer">
    <div class="token-display-panel">
      <button
        class="token-close-btn"
        type="button"
        data-tutorial="token-close"
        @click="emit('toggle')"
        aria-label="收起用量统计"
      >
        <span class="sr-only">关闭</span>
      </button>
      <div class="token-panel-content">
        <div class="usage-dashboard">
          <div class="usage-cell usage-cell--left usage-cell--token panel-card">
            <div class="usage-title">Token 统计</div>
            <div class="stat-grid stat-grid--triple">
              <div class="stat-block">
                <div class="stat-label">当前上下文</div>
                <div class="stat-value stat-value--accent">
                  {{ formatTokenCount(currentContextTokens || 0) }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">累计输入</div>
                <div class="stat-value stat-value--success">
                  {{ formatTokenCount(currentConversationTokens.cumulative_input_tokens || 0) }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">累计输出</div>
                <div class="stat-value stat-value--warning">
                  {{ formatTokenCount(currentConversationTokens.cumulative_output_tokens || 0) }}
                </div>
              </div>
            </div>
          </div>
          <div class="usage-cell usage-cell--right usage-cell--performance panel-card">
            <div class="usage-title">
              <span>性能统计</span>
              <span class="status-pill" v-if="containerStatus" :class="containerStatusClass">
                {{ containerStatusText }}
              </span>
            </div>
            <template v-if="containerStatus && containerStatus.mode === 'docker'">
              <template v-if="hasContainerStats">
                <div class="stat-grid stat-grid--double">
                  <div class="stat-block">
                    <div class="stat-label">CPU</div>
                    <div class="stat-value">
                      {{ formatPercentage(containerStatus.stats.cpu_percent) }}
                    </div>
                  </div>
                  <div class="stat-block">
                    <div class="stat-label">内存</div>
                    <div class="stat-value stat-value--mono">
                      {{ formatBytes(containerStatus.stats.memory.used_bytes) }}
                      <template v-if="containerStatus.stats.memory.limit_bytes">
                        / {{ formatBytes(containerStatus.stats.memory.limit_bytes) }}
                      </template>
                    </div>
                    <div class="stat-foot" v-if="containerStatus.stats.memory.percent">
                      {{ formatPercentage(containerStatus.stats.memory.percent) }}
                    </div>
                  </div>
                </div>
              </template>
              <div class="usage-placeholder" v-else>容器已运行，等待采集指标...</div>
            </template>
            <div class="usage-placeholder" v-else>当前运行在宿主机模式，暂无容器指标。</div>
          </div>
          <div class="usage-cell usage-cell--left usage-cell--quota panel-card">
            <div class="usage-title">额度统计</div>
            <div class="stat-grid stat-grid--triple">
              <div class="stat-block" v-for="tier in quotaTiers" :key="tier.key">
                <div class="stat-label">{{ tier.label }}</div>
                <div class="stat-value">{{ formatQuotaValue(tier.value) }}</div>
                <div class="stat-foot" v-if="(tier.value.count || 0) > 0">
                  重置 {{ formatResetTime(tier.value.reset_at) }}
                </div>
              </div>
            </div>
          </div>
          <div class="usage-cell usage-cell--right usage-cell--resource panel-card">
            <div class="usage-title">资源统计</div>
            <div class="stat-grid stat-grid--double">
              <div class="stat-block">
                <div class="stat-label">网络</div>
                <div class="stat-value stat-value--mono">
                  ↓{{ formatRate(containerNetRate.down_bps) }} ↑{{
                    formatRate(containerNetRate.up_bps)
                  }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">存储</div>
                <div class="stat-value stat-value--mono">
                  {{ formatBytes(projectStorage.used_bytes) }}
                  <template v-if="projectStorage.limit_bytes"
                    >/ {{ formatBytes(projectStorage.limit_bytes) }}</template
                  >
                </div>
                <div class="stat-foot" v-if="typeof projectStorage.usage_percent === 'number'">
                  {{ projectStorage.usage_percent.toFixed(1) }}%
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'TokenDrawer' });

import { computed } from 'vue';

const emit = defineEmits<{
  (e: 'toggle'): void;
}>();

const props = defineProps<{
  visible: boolean;
  collapsed: boolean;
  currentConversationTokens: {
    cumulative_input_tokens?: number;
    cumulative_output_tokens?: number;
  };
  currentContextTokens: number;
  containerStatus: any;
  containerNetRate: { down_bps: number | null; up_bps: number | null };
  projectStorage: { used_bytes: number; limit_bytes?: number; usage_percent?: number };
  usageQuota: {
    fast: Record<string, any>;
    thinking: Record<string, any>;
    search: Record<string, any>;
  };
  formatTokenCount: (value: number) => string;
  formatPercentage: (value: number | null | undefined) => string;
  formatBytes: (value: number | null | undefined) => string;
  formatQuotaValue: (quota: Record<string, any>) => string;
  formatResetTime: (value: unknown) => string;
  formatRate: (value: number | null | undefined) => string;
}>();

const quotaTiers = computed(() => [
  { key: 'fast', label: '常规模型', value: props.usageQuota.fast },
  { key: 'thinking', label: '思考模型', value: props.usageQuota.thinking },
  { key: 'search', label: '搜索', value: props.usageQuota.search }
]);

const hasContainerStats = computed(() => {
  const status = props.containerStatus;
  if (!status || !status.stats) {
    return false;
  }
  if (typeof status.stats.cpu_percent !== 'undefined') {
    return true;
  }
  return !!(status.stats.memory && typeof status.stats.memory.used_bytes !== 'undefined');
});

const containerStatusClass = computed(() => {
  const status = props.containerStatus;
  if (!status) {
    return {};
  }
  if (status.mode !== 'docker') {
    return { 'status-pill--host': true };
  }
  const stopped = status.state && status.state.running === false;
  return {
    'status-pill--running': !stopped,
    'status-pill--stopped': stopped
  };
});

const containerStatusText = computed(() => {
  const status = props.containerStatus;
  if (!status) {
    return '未知';
  }
  if (status.mode !== 'docker') {
    return 'Host';
  }
  const stopped = status.state && status.state.running === false;
  return stopped ? '停止' : '运行';
});
</script>

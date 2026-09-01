<template>
  <div class="token-drawer" v-if="visible" :class="{ collapsed }" data-tutorial="token-drawer">
    <div class="token-display-panel">
      <CloseButton
        class="token-close-btn"
        data-tutorial="token-close"
        :label="$t('sidebar.collapseUsage')"
        @click="emit('toggle')"
      />
      <div class="token-panel-content">
        <div class="usage-dashboard">
          <div class="usage-cell usage-cell--left usage-cell--token panel-card">
            <div class="usage-title">{{ $t('sidebar.tokenStats') }}</div>
            <div class="stat-grid stat-grid--triple">
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.currentContext') }}</div>
                <div class="stat-value stat-value--accent">
                  {{ formatTokenCount(currentContextTokens || 0) }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.cumulativeInput') }}</div>
                <div class="stat-value stat-value--success">
                  {{ formatTokenCount(currentConversationTokens.cumulative_input_tokens || 0) }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.cumulativeOutput') }}</div>
                <div class="stat-value stat-value--warning">
                  {{ formatTokenCount(currentConversationTokens.cumulative_output_tokens || 0) }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.cumulativeCachedInput') }}</div>
                <div class="stat-value">
                  {{ formatTokenCount(currentConversationTokens.cumulative_cached_input_tokens || 0) }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.cacheHitRate') }}</div>
                <div class="stat-value">{{ cacheHitRateText }}</div>
              </div>
            </div>
          </div>
          <div class="usage-cell usage-cell--right usage-cell--performance panel-card">
            <div class="usage-title">
              <span>{{ $t('sidebar.performanceStats') }}</span>
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
                    <div class="stat-label">{{ $t('sidebar.memory') }}</div>
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
              <div class="usage-placeholder" v-else>{{ $t('sidebar.containerMetricsPending') }}</div>
            </template>
            <div class="usage-placeholder" v-else>{{ $t('sidebar.hostModeNoContainer') }}</div>
          </div>
          <div class="usage-cell usage-cell--left usage-cell--quota panel-card">
            <div class="usage-title">{{ $t('sidebar.quotaStats') }}</div>
            <div class="stat-grid stat-grid--triple">
              <div class="stat-block" v-for="tier in quotaTiers" :key="tier.key">
                <div class="stat-label">{{ $t(tier.label) }}</div>
                <div class="stat-value">{{ formatQuotaValue(tier.value) }}</div>
                <div class="stat-foot" v-if="(tier.value.count || 0) > 0">
                  {{ $t('sidebar.resetAt', { time: formatResetTime(tier.value.reset_at) }) }}
                </div>
              </div>
            </div>
          </div>
          <div class="usage-cell usage-cell--right usage-cell--resource panel-card">
            <div class="usage-title">{{ $t('sidebar.resourceStats') }}</div>
            <div class="stat-grid stat-grid--double">
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.network') }}</div>
                <div class="stat-value stat-value--mono">
                  ↓{{ formatRate(containerNetRate.down_bps) }} ↑{{
                    formatRate(containerNetRate.up_bps)
                  }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.storage') }}</div>
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
import { t, currentLocale } from '@/locales';
import CloseButton from '@/components/common/CloseButton.vue';

const emit = defineEmits<{
  (e: 'toggle'): void;
}>();

const props = defineProps<{
  visible: boolean;
  collapsed: boolean;
  currentConversationTokens: {
    cumulative_input_tokens?: number;
    cumulative_output_tokens?: number;
    cumulative_cached_input_tokens?: number;
    cache_exempt_input_tokens?: number;
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
  { key: 'fast', label: 'sidebar.quotaTierFast', value: props.usageQuota.fast },
  { key: 'thinking', label: 'sidebar.quotaTierThinking', value: props.usageQuota.thinking },
  { key: 'search', label: 'sidebar.quotaTierSearch', value: props.usageQuota.search }
]);

// 缓存命中率 = 累积缓存命中输入 / (累计总输入 - 冷启动豁免输入)；
// 豁免值由后端累计：首轮及压缩后首轮若未命中缓存，其输入属于建立缓存的成本，不计入分母
const cacheHitRateText = computed(() => {
  const totalInput = props.currentConversationTokens.cumulative_input_tokens || 0;
  const exemptInput = props.currentConversationTokens.cache_exempt_input_tokens || 0;
  const effectiveInput = totalInput - exemptInput;
  if (effectiveInput <= 0) {
    return '--';
  }
  const cached = props.currentConversationTokens.cumulative_cached_input_tokens || 0;
  return `${((cached / effectiveInput) * 100).toFixed(1)}%`;
});

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
  void currentLocale.value;
  const status = props.containerStatus;
  if (!status) {
    return t('sidebar.unknown');
  }
  if (status.mode !== 'docker') {
    return 'Host';
  }
  const stopped = status.state && status.state.running === false;
  return stopped ? t('sidebar.stopped') : t('sidebar.containerRunning');
});
</script>

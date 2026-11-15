<template>
  <div class="usage-cell subscription-quota panel-card">
    <div class="usage-title">
      <span>{{ title }}</span>
      <span v-if="accountLabel" class="subscription-quota__account">{{ accountLabel }}</span>
    </div>
    <!-- 窗口数量决定列数：OpenCode Go 三窗口一行，ChatGPT 两窗口两列；窄屏由样式回落自适应 -->
    <div
      v-if="windows.length"
      class="stat-grid"
      :class="windows.length >= 3 ? 'stat-grid--triple' : 'stat-grid--double'"
    >
      <div v-for="window in windows" :key="window.key" class="stat-block subscription-quota__window">
        <div class="stat-label">{{ window.label }}</div>
        <div class="stat-value" :class="valueClass(window.percent)">
          {{ $t('input.usedPercent', { n: `${window.percent}%` }) }}
        </div>
        <div class="subscription-quota__track" aria-hidden="true">
          <span
            class="subscription-quota__fill"
            :class="fillClass(window.percent)"
            :style="{ width: `${window.percent}%` }"
          ></span>
        </div>
        <div class="stat-foot">
          {{ $t('sidebar.resetAt', { time: window.resetText }) }}
        </div>
      </div>
    </div>
    <div v-else-if="loading" class="usage-placeholder">
      {{ $t('sidebar.chatgptQuotaLoading') }}
    </div>
    <div v-else class="usage-placeholder">
      {{ error || $t('sidebar.subscriptionQuotaUnavailable') }}
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 宿主机模式用量抽屉的订阅额度块（纯展示）。
 *
 * 来源、账号归属与轮询全部由 useSubscriptionQuota 决定，这里只负责渲染，
 * 因此 OpenCode Go 与 ChatGPT 订阅共用同一套布局（窗口数量由数据决定）。
 */
import type { QuotaWindowView } from './useSubscriptionQuota';

defineOptions({ name: 'SubscriptionQuotaPanel' });

defineProps<{
  title: string;
  accountLabel: string;
  windows: QuotaWindowView[];
  loading: boolean;
  error: string;
}>();

// percent 为「已使用」百分比：接近上限时用语义色辅助提示，数值仍是主信息。
const valueClass = (percent: number) => {
  if (percent >= 95) return 'subscription-quota__value--danger';
  if (percent >= 80) return 'subscription-quota__value--warning';
  return '';
};

const fillClass = (percent: number) => {
  if (percent >= 95) return 'subscription-quota__fill--danger';
  if (percent >= 80) return 'subscription-quota__fill--warning';
  return '';
};
</script>

<style scoped lang="scss">
/* 与 Token 统计上下排列：靠上分隔线区分两块内容，不额外套卡片。
   选择器带上 .usage-cell，优先于全局 `.usage-dashboard .panel-card { padding: 0 }`。 */
.usage-cell.subscription-quota {
  padding-top: 20px;
  border-top: 1px solid var(--border-default);
}

.subscription-quota__account {
  min-width: 0;
  max-width: 60%;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subscription-quota__value--warning {
  color: var(--state-warning);
}

.subscription-quota__value--danger {
  color: var(--state-danger);
}

/* 额度进度条：track 用中性表面色，填充默认 --accent，只有接近上限才换成状态色。 */
.subscription-quota__track {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--surface-muted);
}

.subscription-quota__fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.3s ease;
}

.subscription-quota__fill--warning {
  background: var(--state-warning);
}

.subscription-quota__fill--danger {
  background: var(--state-danger);
}
</style>

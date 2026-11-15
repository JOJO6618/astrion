<template>
  <div class="subscription-quota-card" role="dialog" :aria-label="title">
    <div class="subscription-quota-card__head">
      <span class="subscription-quota-card__title">{{ title }}</span>
      <span v-if="accountLabel" class="subscription-quota-card__account">
        {{ accountLabel }}
      </span>
    </div>
    <div v-if="windows.length" class="subscription-quota-card__windows">
      <div v-for="window in windows" :key="window.key" class="subscription-quota-card__window">
        <div class="subscription-quota-card__row">
          <span class="subscription-quota-card__label">{{ window.label }}</span>
          <span class="subscription-quota-card__percent" :class="percentModifier(window.percent)">{{
            $t('input.usedPercent', { n: `${window.percent}%` })
          }}</span>
        </div>
        <div v-if="showBars" class="subscription-quota-card__track" aria-hidden="true">
          <span
            class="subscription-quota-card__fill"
            :class="percentModifier(window.percent)"
            :style="{ width: `${window.percent}%` }"
          ></span>
        </div>
        <div class="subscription-quota-card__reset">
          {{ $t('sidebar.resetAt', { time: window.resetText }) }}
        </div>
      </div>
    </div>
    <div v-else-if="loading" class="subscription-quota-card__note">
      {{ $t('sidebar.chatgptQuotaLoading') }}
    </div>
    <div v-else class="subscription-quota-card__note">
      {{ error || $t('sidebar.subscriptionQuotaUnavailable') }}
    </div>
    <button
      type="button"
      class="subscription-quota-card__context"
      :class="{ 'subscription-quota-card__context--editable': contextEditable }"
      :disabled="!contextEditable"
      :aria-label="$t('input.compressLimitEdit')"
      @click.stop="contextEditable && emit('edit-context')"
    >
      <span class="subscription-quota-card__context-label">{{ $t('sidebar.currentContext') }}</span>
      <span class="subscription-quota-card__context-value">
        {{ $t('input.usedPercent', { n: contextPercentLabel }) }}
        <span class="subscription-quota-card__context-tokens">{{ contextText }}</span>
      </span>
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 输入栏 Token 圆环的订阅额度卡片（纯展示，不发起请求）。
 *
 * 数据由 useSubscriptionQuota 统一提供：OpenCode Go 与 ChatGPT 订阅共用同一形状，
 * 标题与账号标识跟随来源变化，组件本身不关心是哪一家。
 * 用量百分比语义统一是「已使用」，文案走 input.usedPercent。
 */
import type { QuotaWindowView } from './useSubscriptionQuota';

defineOptions({ name: 'SubscriptionQuotaCard' });

withDefaults(
  defineProps<{
    title: string;
    accountLabel: string;
    windows: QuotaWindowView[];
    loading: boolean;
    error: string;
    /** 当前上下文已用百分比（0~100，已取整标签） */
    contextPercentLabel: string;
    /** 当前上下文 / 上限的紧凑文本，例如 84k/200k */
    contextText: string;
    /** hover 小卡只显示数字；点击打开的宽弹窗才显示进度条 */
    showBars?: boolean;
    /** 上下文行是否可点击编辑压缩阈值（运行中/空页时为 false） */
    contextEditable?: boolean;
  }>(),
  { showBars: false, contextEditable: false }
);

const emit = defineEmits<{ 'edit-context': [] }>();

const percentModifier = (percent: number) => {
  if (percent >= 95) return 'subscription-quota-card--danger';
  if (percent >= 80) return 'subscription-quota-card--warning';
  return '';
};
</script>

<style scoped lang="scss">
.subscription-quota-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  padding: 10px 12px 11px;
  border: 1px solid var(--border-default);
  border-radius: 12px;
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.4;
}

.subscription-quota-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.subscription-quota-card__title {
  flex: none;
  font-weight: 600;
}

.subscription-quota-card__account {
  min-width: 0;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subscription-quota-card__windows {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding-top: 9px;
  border-top: 1px solid var(--border-default);
}

.subscription-quota-card__window {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.subscription-quota-card__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  height: 17px;
  min-width: 0;
}

.subscription-quota-card__label {
  min-width: 0;
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subscription-quota-card__percent {
  flex: none;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.subscription-quota-card__track {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--progress-track);
}

.subscription-quota-card__fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.2s ease;
}

.subscription-quota-card--warning {
  &.subscription-quota-card__percent {
    color: var(--state-warning);
  }

  &.subscription-quota-card__fill {
    background: var(--state-warning);
  }
}

.subscription-quota-card--danger {
  &.subscription-quota-card__percent {
    color: var(--state-danger);
  }

  &.subscription-quota-card__fill {
    background: var(--state-danger);
  }
}

.subscription-quota-card__reset {
  color: var(--text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.subscription-quota-card__note {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.subscription-quota-card__context {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  min-width: 0;
  margin: 0;
  padding: 9px 0 0;
  border: 0;
  border-top: 1px solid var(--border-default);
  background: transparent;
  font: inherit;
  text-align: left;
  color: var(--text-tertiary);
  font-size: 11px;
  cursor: default;
}

.subscription-quota-card__context--editable {
  cursor: pointer;
}

.subscription-quota-card__context--editable:hover .subscription-quota-card__context-tokens {
  color: var(--text-primary);
  text-decoration: underline;
}

.subscription-quota-card__context-label {
  flex: none;
}

.subscription-quota-card__context-value {
  min-width: 0;
  overflow: hidden;
  font-variant-numeric: tabular-nums;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subscription-quota-card__context-tokens {
  margin-left: 6px;
  color: var(--text-secondary);
}
</style>

<template>
  <div class="chatgpt-quota-card" role="dialog" :aria-label="$t('sidebar.chatgptQuota')">
    <div class="chatgpt-quota-card__head">
      <span class="chatgpt-quota-card__title">{{ $t('sidebar.chatgptQuota') }}</span>
      <span v-if="accountHash" class="chatgpt-quota-card__account">
        {{ $t('sidebar.chatgptAccount', { hash: accountHash }) }}
      </span>
    </div>
    <template v-if="applicable">
      <div v-if="windows.length" class="chatgpt-quota-card__windows">
        <div v-for="window in windows" :key="window.key" class="chatgpt-quota-card__window">
          <div class="chatgpt-quota-card__row">
            <span class="chatgpt-quota-card__label">{{ window.label }}</span>
            <span class="chatgpt-quota-card__percent" :class="percentModifier(window.percent)">{{
              $t('input.usedPercent', { n: `${window.percent}%` })
            }}</span>
          </div>
          <div v-if="showBars" class="chatgpt-quota-card__track" aria-hidden="true">
            <span
              class="chatgpt-quota-card__fill"
              :class="percentModifier(window.percent)"
              :style="{ width: `${window.percent}%` }"
            ></span>
          </div>
          <div class="chatgpt-quota-card__reset">
            {{ $t('sidebar.resetAt', { time: window.resetText }) }}
          </div>
        </div>
      </div>
      <div v-else-if="loading" class="chatgpt-quota-card__note">
        {{ $t('sidebar.chatgptQuotaLoading') }}
      </div>
      <div v-else class="chatgpt-quota-card__note">
        {{ error || $t('sidebar.chatgptQuotaUnavailable') }}
      </div>
    </template>
    <div v-else class="chatgpt-quota-card__note">
      {{ $t('sidebar.chatgptQuotaNotApplicable') }}
    </div>
    <div class="chatgpt-quota-card__context">
      <span class="chatgpt-quota-card__context-label">{{ $t('sidebar.currentContext') }}</span>
      <span class="chatgpt-quota-card__context-value">
        {{ $t('input.usedPercent', { n: contextPercentLabel }) }}
        <span class="chatgpt-quota-card__context-tokens">{{ contextText }}</span>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 输入栏 Token 圆环的 ChatGPT 订阅额度卡片（纯展示，不发起请求）。
 *
 * - 用量百分比语义统一是「已使用」，与设置页 Codex 用量保持一致；
 *   文案走 input.usedPercent，避免只给数字让用户误读成剩余额度。
 * - 重置时间由 useChatgptQuota 提供为本地日期时间，这里用 sidebar.resetAt 拼装。
 * - 颜色只在接近上限时做辅助提示，主信息仍是文字与数值。
 */
defineOptions({ name: 'ChatgptQuotaCard' });

withDefaults(
  defineProps<{
    accountHash: string;
    windows: Array<{ key: string; label: string; percent: number; resetText: string }>;
    loading: boolean;
    error: string;
    applicable: boolean;
    /** 当前上下文已用百分比（0~100，已取整标签） */
    contextPercentLabel: string;
    /** 当前上下文 / 上限的紧凑文本，例如 84k/200k */
    contextText: string;
    /** hover 小卡只显示数字；点击打开的宽弹窗才显示进度条 */
    showBars?: boolean;
  }>(),
  { showBars: false }
);

const percentModifier = (percent: number) => {
  if (percent >= 95) return 'chatgpt-quota-card--danger';
  if (percent >= 80) return 'chatgpt-quota-card--warning';
  return '';
};
</script>

<style scoped lang="scss">
.chatgpt-quota-card {
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

.chatgpt-quota-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.chatgpt-quota-card__title {
  flex: none;
  font-weight: 600;
}

.chatgpt-quota-card__account {
  min-width: 0;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chatgpt-quota-card__windows {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding-top: 9px;
  border-top: 1px solid var(--border-default);
}

.chatgpt-quota-card__window {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chatgpt-quota-card__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  height: 17px;
  min-width: 0;
}

.chatgpt-quota-card__label {
  min-width: 0;
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chatgpt-quota-card__percent {
  flex: none;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.chatgpt-quota-card__track {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--progress-track);
}

.chatgpt-quota-card__fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.2s ease;
}

.chatgpt-quota-card--warning {
  &.chatgpt-quota-card__percent {
    color: var(--state-warning);
  }

  &.chatgpt-quota-card__fill {
    background: var(--state-warning);
  }
}

.chatgpt-quota-card--danger {
  &.chatgpt-quota-card__percent {
    color: var(--state-danger);
  }

  &.chatgpt-quota-card__fill {
    background: var(--state-danger);
  }
}

.chatgpt-quota-card__reset {
  color: var(--text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.chatgpt-quota-card__note {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.chatgpt-quota-card__context {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
  padding-top: 9px;
  border-top: 1px solid var(--border-default);
  color: var(--text-tertiary);
  font-size: 11px;
}

.chatgpt-quota-card__context-label {
  flex: none;
}

.chatgpt-quota-card__context-value {
  min-width: 0;
  overflow: hidden;
  font-variant-numeric: tabular-nums;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chatgpt-quota-card__context-tokens {
  margin-left: 6px;
  color: var(--text-secondary);
}
</style>

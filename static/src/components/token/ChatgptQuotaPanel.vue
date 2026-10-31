<template>
  <div class="usage-cell chatgpt-quota panel-card">
    <div class="usage-title">
      <span>{{ $t('sidebar.chatgptQuota') }}</span>
      <span v-if="applicable && accountHash" class="chatgpt-quota__account">
        {{ $t('sidebar.chatgptAccount', { hash: accountHash }) }}
      </span>
    </div>
    <template v-if="applicable">
      <div v-if="windows.length" class="stat-grid stat-grid--double">
        <div v-for="window in windows" :key="window.key" class="stat-block chatgpt-quota__window">
          <div class="stat-label">{{ window.label }}</div>
          <div class="stat-value" :class="valueClass(window.percent)">
            {{ $t('input.usedPercent', { n: `${window.percent}%` }) }}
          </div>
          <div class="chatgpt-quota__track" aria-hidden="true">
            <span
              class="chatgpt-quota__fill"
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
      <div v-else class="usage-placeholder">{{ $t('sidebar.chatgptQuotaUnavailable') }}</div>
    </template>
    <div v-else class="usage-placeholder">{{ $t('sidebar.chatgptQuotaUnavailable') }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 宿主机模式的 ChatGPT 订阅额度（5 小时窗口 / 每周窗口）。
 *
 * - 账号选择完全由 @/components/token/useChatgptQuota 决定：显式选中 ChatGPT 订阅模型
 *   （isChatgptModel 且带 providerId）时跟随该账号；其余模型（普通模型、只带 providerId
 *   的提供商同步模型）回退到已登录订阅账号（store.defaultAccountId，即首个账号）。
 *   不会仅仅因为 providerId 存在就把陌生 key 当成 OAuth 账号去查询。
 * - 当前模型按 currentModelKey 在 allModels 精确查找，不用 currentModel 的
 *   fallback（否则模型列表尚未加载时会误落到第一个模型，拿错账号）。
 * - applicable 只表示「管理员可查询」；未登录订阅账号、账号查询失败都显示同一句不可用。
 */
import { useModelStore } from '@/stores/model';
import { useChatgptQuota } from './useChatgptQuota';

defineOptions({ name: 'ChatgptQuotaPanel' });

const props = defineProps<{
  /** 抽屉可见且未收起时才允许拉取/轮询额度 */
  active: boolean;
}>();

const modelStore = useModelStore();

const resolveCurrentModel = () => {
  const key = modelStore.currentModelKey;
  if (!key) {
    return null;
  }
  return modelStore.allModels.find((model) => model.key === key) || null;
};

// useChatgptQuota 返回的是普通对象（字段为 computed ref），模板不会深层解包：
// 若直接写 quota.applicable / quota.windows.length，会得到 ComputedRef 本身
// （永远 truthy / length 为 undefined）。必须顶层解构，让模板对顶层 ref 自动解包。
const { applicable, accountHash, windows, loading } = useChatgptQuota(
  resolveCurrentModel,
  () => props.active
);

// percent 为「已使用」百分比：同时用于文字（明确「已用」）和额度进度条，
// 条本身是点击大面板才有的细粒度展示（hover 小卡片不带条）。
// 接近上限时用语义色辅助提示，数值仍是主信息。
const valueClass = (percent: number) => {
  if (percent >= 95) {
    return 'chatgpt-quota__value--danger';
  }
  if (percent >= 80) {
    return 'chatgpt-quota__value--warning';
  }
  return '';
};

const fillClass = (percent: number) => {
  if (percent >= 95) {
    return 'chatgpt-quota__fill--danger';
  }
  if (percent >= 80) {
    return 'chatgpt-quota__fill--warning';
  }
  return '';
};
</script>

<style scoped lang="scss">
/* 与 Token 统计上下排列：靠上分隔线区分两块内容，不额外套卡片。
   选择器带上 .usage-cell，优先于全局 `.usage-dashboard .panel-card { padding: 0 }`。 */
.usage-cell.chatgpt-quota {
  padding-top: 20px;
  border-top: 1px solid var(--border-default);
}

.chatgpt-quota__account {
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

.chatgpt-quota__value--warning {
  color: var(--state-warning);
}

.chatgpt-quota__value--danger {
  color: var(--state-danger);
}

/* 额度进度条：track 用中性表面色，填充默认 --accent，只有接近上限才换成状态色。 */
.chatgpt-quota__track {
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--surface-muted);
}

.chatgpt-quota__fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.3s ease;
}

.chatgpt-quota__fill--warning {
  background: var(--state-warning);
}

.chatgpt-quota__fill--danger {
  background: var(--state-danger);
}
</style>

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
          <div class="usage-cell usage-cell--token panel-card usage-cell--full">
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
                  {{
                    formatTokenCount(currentConversationTokens.cumulative_cached_input_tokens || 0)
                  }}
                </div>
              </div>
              <div class="stat-block">
                <div class="stat-label">{{ $t('sidebar.cacheHitRate') }}</div>
                <div class="stat-value">{{ cacheHitRateText }}</div>
              </div>
              <CostSummary
                :costs="currentConversationTokens.costs"
                :conversation-id="conversationId"
                :active="visible && !collapsed"
              />
            </div>
          </div>
          <!-- 宿主机模式：订阅额度（OpenCode Go / ChatGPT，来源由设置决定） -->
          <SubscriptionQuotaPanel
            v-if="quotaVisible"
            :title="quotaTitle"
            :account-label="quotaAccountLabel"
            :windows="quotaWindows"
            :loading="quotaLoading"
            :error="quotaError"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'TokenDrawer' });

import { computed } from 'vue';
import { useModelStore } from '@/stores/model';
import CloseButton from '@/components/common/CloseButton.vue';
import CostSummary from './CostSummary.vue';
import SubscriptionQuotaPanel from './SubscriptionQuotaPanel.vue';
import { useSubscriptionQuota } from './useSubscriptionQuota';
import type { ConversationCosts } from './costTypes';

const emit = defineEmits<{
  (e: 'toggle'): void;
}>();

const props = defineProps<{
  visible: boolean;
  collapsed: boolean;
  conversationId: string | null;
  currentConversationTokens: {
    costs?: ConversationCosts | null;
    cumulative_input_tokens?: number;
    cumulative_output_tokens?: number;
    cumulative_cached_input_tokens?: number;
    cache_exempt_input_tokens?: number;
  };
  currentContextTokens: number;
  formatTokenCount: (value: number) => string;
}>();

// 订阅额度：来源与轮询由 useSubscriptionQuota 统一决定
const modelStore = useModelStore();
const resolveCurrentModel = () => {
  const key = modelStore.currentModelKey;
  if (!key) return null;
  return modelStore.allModels.find((model) => model.key === key) || null;
};
const {
  visible: quotaVisible,
  title: quotaTitle,
  accountLabel: quotaAccountLabel,
  windows: quotaWindows,
  loading: quotaLoading,
  error: quotaError
} = useSubscriptionQuota(resolveCurrentModel, () => props.visible && !props.collapsed);

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
</script>

<style scoped lang="scss">
/* Host：Token 统计与订阅额度上下排列 */
.usage-dashboard {
  grid-template-columns: minmax(0, 1fr);
  row-gap: 20px;
}

.usage-dashboard::before {
  display: none;
}

.usage-cell--full {
  padding: 0;
}
</style>

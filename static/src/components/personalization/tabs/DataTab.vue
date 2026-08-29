<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'DataTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  fetchUsageSummary,
  formatTokenCount,
  usageError,
  usageLoading,
  usageSummary,
  usageUpdatedText
} = ctx;
</script>

<template>
                    <section
                      class="settings-page usage-summary-page"
                      data-tutorial="personal-page-usage"
                    >
                      <div class="usage-summary-card settings-stats-card">
                        <div class="usage-summary-header">
                          <div>
                            <p class="usage-summary-eyebrow">{{ $t('personalization.usageEyebrow') }}</p>
                            <h3>{{ $t('personalization.usageTitle') }}</h3>
                            <p class="usage-summary-desc">{{ $t('personalization.usageDesc') }}
                            </p>
                          </div>
                        </div>
                        <div class="usage-summary-grid usage-summary-grid--tokens">
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageInputLabel') }}</div>
                            <div class="value value--success">
                              {{ formatTokenCount(usageSummary.total_input_tokens) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageOutputLabel') }}</div>
                            <div class="value value--warning">
                              {{ formatTokenCount(usageSummary.total_output_tokens) }}
                            </div>
                          </div>
                        </div>
                        <div class="usage-summary-grid usage-summary-grid--counts">
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageConversationsLabel') }}</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_conversations) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageMessagesLabel') }}</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_user_messages) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageToolsLabel') }}</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_tools) }}
                            </div>
                          </div>
                        </div>
                        <div class="usage-summary-meta">
                          <span v-if="usageError" class="usage-summary-error">{{ usageError }}</span
                          ><span v-else-if="usageLoading">{{ $t('personalization.usageSyncing') }}</span
                          ><span v-else>{{ $t('personalization.usageUpdated', { time: usageUpdatedText }) }}</span
                          ><button
                            type="button"
                            class="usage-summary-refresh"
                            @click="fetchUsageSummary"
                            :disabled="usageLoading"
                          >
                            {{ usageLoading ? $t('personalization.usageRefreshing') : $t('personalization.usageRefresh') }}
                          </button>
                        </div>
                      </div>
                    </section>
</template>

<style scoped>
.usage-summary-page {
  display: block;
  padding: 0;
}

.settings-stats-card,
.usage-summary-card {
  width: 100%;
  max-width: 720px;
  padding: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.usage-summary-header h3 {
  margin: 4px 0 6px;
  font-size: 20px;
}

.usage-summary-eyebrow {
  margin: 0;
  font-size: 11px;
  color: var(--accent-strong);
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.usage-summary-desc,
.usage-summary-note,
.usage-summary-meta {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.usage-summary-grid {
  display: grid;
  gap: 10px;
}

.usage-summary-grid--tokens {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.usage-summary-grid--counts {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.usage-summary-item {
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid var(--theme-control-border);
  background: transparent;
}

.usage-summary-item .label {
  font-size: 12px;
  color: var(--text-secondary);
}

.usage-summary-item .value {
  margin-top: 4px;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.usage-summary-item .value--success {
  color: var(--state-success);
}

.usage-summary-item .value--warning {
  color: var(--state-warning);
}

.usage-summary-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.usage-summary-refresh {
  border: 1px solid var(--theme-control-border);
  border-radius: 999px;
  padding: 7px 14px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}
</style>

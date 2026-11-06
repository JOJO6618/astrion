<script setup lang="ts">
import { computed } from 'vue';
import { formatInt, formatMoney, formatPercent, formatTokens, splitModelKey } from './format';
import type { UsageModelRow } from './types';

defineOptions({ name: 'UsageModelTable' });

const props = defineProps<{ models: UsageModelRow[]; currency: string; rate: number | null }>();

const total = computed(() => props.models.reduce((sum, row) => sum + row.tokens, 0));
const hasPartial = computed(() =>
  props.models.some((row) => row.priced_requests > 0 && row.unpriced_requests > 0)
);
const maxTokens = computed(() => Math.max(1, ...props.models.map((row) => row.tokens)));

const rows = computed(() =>
  props.models.map((row) => ({
    ...row,
    ...splitModelKey(row.model_key),
    share: formatPercent(row.tokens, total.value),
    width: `${((row.tokens / maxTokens.value) * 100).toFixed(1)}%`,
    speed: row.speed_ms > 0 ? (row.speed_tokens / row.speed_ms) * 1000 : null,
    ttft: row.ttft_n > 0 ? row.ttft_ms / row.ttft_n / 1000 : null
  }))
);
</script>

<template>
  <div class="usage-table-scroll">
    <table class="usage-table">
      <thead>
        <tr>
          <th>{{ $t('usageStats.colModel') }}</th>
          <th class="usage-col-share">{{ $t('usageStats.colShare') }}</th>
          <th class="is-num">{{ $t('usageStats.colTokens') }}</th>
          <th class="is-num">{{ $t('usageStats.colCost') }}</th>
          <th class="is-num is-optional">{{ $t('usageStats.colRequests') }}</th>
          <th class="is-num is-optional">{{ $t('usageStats.colSpeed') }}</th>
          <th class="is-num is-optional">{{ $t('usageStats.colTtft') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.model_key">
          <td>
            <div class="usage-model">
              <code>{{ row.model }}</code>
              <small v-if="row.provider">{{ row.provider }}</small>
            </div>
          </td>
          <td class="usage-col-share">
            <div class="usage-share-bar"><i :style="{ width: row.width }"></i></div>
          </td>
          <td class="is-num">
            {{ formatTokens(row.tokens) }} <span class="usage-muted">{{ row.share }}%</span>
          </td>
          <td class="is-num">
            <span v-if="!row.priced_requests" class="usage-note">{{ $t('usageStats.unpriced') }}</span>
            <template v-else>
              {{ formatMoney(row.cost_usd, currency, rate) }}
              <span v-if="row.unpriced_requests" class="usage-note">*</span>
            </template>
          </td>
          <td class="is-num is-optional">{{ formatInt(row.requests) }}</td>
          <td class="is-num is-optional">{{ row.speed === null ? '—' : `${row.speed.toFixed(0)} t/s` }}</td>
          <td class="is-num is-optional">{{ row.ttft === null ? '—' : `${row.ttft.toFixed(1)} s` }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="7" class="usage-muted">{{ $t('usageStats.noRequests') }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="hasPartial" class="usage-footnote">* {{ $t('usageStats.partialPriced') }}</p>
  </div>
</template>

<style scoped>
.usage-table-scroll {
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}

.usage-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.usage-table th {
  height: 28px;
  padding: 0 0 6px;
  text-align: left;
  font-weight: 500;
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.usage-table td {
  height: 44px;
  padding: 0;
  border-top: 1px solid var(--border-default);
  vertical-align: middle;
  white-space: nowrap;
}

.usage-table th + th,
.usage-table td + td {
  padding-left: 14px;
}

.usage-table .is-num {
  text-align: right;
}

.usage-col-share {
  width: 28%;
  min-width: 80px;
}

.usage-model {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: 260px;
  line-height: 1.35;
}

.usage-model code {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12.5px;
  color: var(--text-primary);
}

.usage-model small {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: var(--text-tertiary);
}

.usage-share-bar {
  height: 6px;
  border-radius: 3px;
  background: var(--heat-0);
  overflow: hidden;
}

.usage-share-bar i {
  display: block;
  height: 100%;
  background: var(--heat-3);
}

.usage-footnote {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.usage-muted {
  color: var(--text-tertiary);
}

.usage-note {
  color: var(--text-secondary);
}

@media (max-width: 760px) {
  .usage-table .is-optional {
    display: none;
  }
}
</style>

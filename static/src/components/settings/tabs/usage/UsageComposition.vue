<script setup lang="ts">
import { computed } from 'vue';
import { t } from '@/locales';
import { formatPercent, formatTokens, sourceLabel } from './format';
import type { UsageBreakdown, UsageSource } from './types';

defineOptions({ name: 'UsageComposition' });

const props = defineProps<{ breakdown: UsageBreakdown; sources: UsageSource[]; showSources: boolean }>();

interface Part {
  key: string;
  label: string;
  value: number;
  color: string;
}

const SERIES = ['--series-1', '--series-2', '--series-3', '--series-4'];

const KIND_LABELS: Record<string, string> = {
  main: 'usageStats.kindMain',
  sub_agent: 'usageStats.kindSub',
  thinking_summary: 'usageStats.kindThinking',
  review_agent: 'usageStats.kindReview'
};

/** 超过分类色数量的部分合并为「其它」，避免颜色重复让人误读 */
function toParts(entries: [string, string, number][]): Part[] {
  const sorted = entries.filter((entry) => entry[2] > 0).sort((a, b) => b[2] - a[2]);
  const head = sorted.slice(0, SERIES.length - 1);
  const rest = sorted.slice(SERIES.length - 1);
  const parts = head.map(([key, label, value], index) => ({ key, label, value, color: SERIES[index] }));
  if (rest.length === 1) {
    const [key, label, value] = rest[0];
    parts.push({ key, label, value, color: SERIES[SERIES.length - 1] });
  } else if (rest.length > 1) {
    parts.push({
      key: '__other__',
      label: t('usageStats.kindOther'),
      value: rest.reduce((sum, entry) => sum + entry[2], 0),
      color: SERIES[SERIES.length - 1]
    });
  }
  return parts;
}

const rows = computed(() => {
  const kinds = Object.entries(props.breakdown.kinds).map(
    ([key, value]) => [key, KIND_LABELS[key] ? t(KIND_LABELS[key]) : key, value] as [string, string, number]
  );
  const modes = Object.entries(props.breakdown.modes).map(
    ([key, value]) =>
      [key, t(key === 'chat' ? 'usageStats.modeChat' : 'usageStats.modeAgent'), value] as [string, string, number]
  );
  const result = [
    { title: t('usageStats.compByActor'), parts: toParts(kinds) },
    { title: t('usageStats.compByMode'), parts: toParts(modes) }
  ];
  if (props.showSources) {
    const byId = new Map(props.sources.map((source) => [source.id, source]));
    const sources = props.breakdown.sources.map(
      (item) =>
        [item.id, sourceLabel(byId.get(item.id) || { id: item.id, label: '' }), item.tokens] as [
          string,
          string,
          number
        ]
    );
    result.push({ title: t('usageStats.compBySource'), parts: toParts(sources) });
  }
  return result.map((row) => ({ ...row, total: row.parts.reduce((sum, part) => sum + part.value, 0) }));
});
</script>

<template>
  <div class="usage-comp">
    <div v-for="row in rows" :key="row.title" class="usage-comp-row">
      <div class="usage-comp-head">
        <span>{{ row.title }}</span>
        <span class="usage-num">{{ formatTokens(row.total) }}</span>
      </div>
      <div class="usage-stack">
        <i
          v-for="part in row.parts"
          :key="part.key"
          :style="{ width: `${(part.value / Math.max(1, row.total)) * 100}%`, background: `var(${part.color})` }"
        ></i>
      </div>
      <div class="usage-keys">
        <span v-for="part in row.parts" :key="part.key" class="usage-key">
          <span class="usage-swatch" :style="{ background: `var(${part.color})` }"></span>
          <span class="usage-key-label">{{ part.label }}</span>
          <span class="usage-key-value usage-num">{{ formatPercent(part.value, row.total) }}%</span>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.usage-comp {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.usage-comp-head {
  display: flex;
  justify-content: space-between;
  height: 20px;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}

.usage-stack {
  display: flex;
  gap: 2px;
  height: 10px;
  border-radius: 5px;
  overflow: hidden;
  background: var(--heat-0);
}

.usage-stack i {
  display: block;
  height: 100%;
}

.usage-keys {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 6px 14px;
  margin-top: 10px;
  font-size: 12px;
}

.usage-key {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 18px;
  min-width: 0;
  color: var(--text-secondary);
}

.usage-key-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-swatch {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 2px;
}

.usage-key-value {
  margin-left: auto;
  color: var(--text-tertiary);
}

.usage-num {
  font-variant-numeric: tabular-nums;
}
</style>

<script setup lang="ts">
import { computed } from 'vue';
import { t } from '@/locales';
import { categoryMeta } from './catalog';
import { formatBytes, formatShare } from './format';
import type { StorageCategory } from './types';

const props = defineProps<{
  categories: StorageCategory[];
  total: number;
}>();

const items = computed(() =>
  props.categories.map((item) => {
    const meta = categoryMeta(item.id);
    return {
      id: item.id,
      label: t(meta.key),
      color: meta.color,
      size: formatBytes(item.bytes),
      share: formatShare(item.bytes, props.total)
    };
  })
);
</script>

<template>
  <div class="storage-legend">
    <div v-for="item in items" :key="item.id" class="storage-legend-item">
      <span class="storage-legend-dot" :style="{ background: item.color }"></span>
      <div class="storage-legend-text">
        <span class="storage-legend-name">{{ item.label }}</span>
        <span class="storage-legend-value">{{ item.size }} · {{ item.share }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.storage-legend {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px 20px;
  padding: 18px 0 4px;
}

.storage-legend-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.storage-legend-dot {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 50%;
  flex: none;
}

.storage-legend-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.storage-legend-name {
  font-size: 12px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-legend-value {
  font-size: 13px;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

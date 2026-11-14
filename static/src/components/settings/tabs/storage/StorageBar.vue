<script setup lang="ts">
import { computed } from 'vue';
import { categoryMeta } from './catalog';
import type { StorageCategory } from './types';

/** 按真实占比堆叠的占用条（不设最小宽度，避免夸大差异）。 */
const props = defineProps<{
  categories: StorageCategory[];
  total: number;
}>();

const segments = computed(() => {
  const total = Math.max(1, props.total);
  return props.categories
    .filter((item) => item.bytes > 0)
    .map((item) => ({
      id: item.id,
      width: (item.bytes / total) * 100,
      color: categoryMeta(item.id).color
    }));
});
</script>

<template>
  <div class="storage-bar" aria-hidden="true">
    <span
      v-for="segment in segments"
      :key="segment.id"
      class="storage-bar-segment"
      :style="{ width: `${segment.width}%`, background: segment.color }"
    ></span>
  </div>
</template>

<style scoped>
.storage-bar {
  display: flex;
  height: 14px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--surface-muted);
}

.storage-bar-segment {
  display: block;
  height: 100%;
  flex: none;
}
</style>

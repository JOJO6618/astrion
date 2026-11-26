<template>
  <div v-if="html" v-html="html"></div>
  <div v-else class="result-item"><pre>{{ fallbackText }}</pre></div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { currentLocale } from '@/locales';
import { renderEnhancedToolResult } from './actions/toolRenderers';

type SearchFormatter = (filters: Record<string, any>) => string;
const props = defineProps<{
  action: { tool?: { result?: unknown } };
  formatSearchTopic?: SearchFormatter;
  formatSearchTime?: SearchFormatter;
  formatSearchDomains?: SearchFormatter;
}>();

const emptyFormat = () => '';
// 每条已挂载结果独立跟踪依赖；追加其他步骤时复用已生成的 HTML。
const html = computed(() => {
  void currentLocale.value;
  return renderEnhancedToolResult(
    props.action,
    props.formatSearchTopic || emptyFormat,
    props.formatSearchTime || emptyFormat,
    props.formatSearchDomains || emptyFormat
  );
});
const fallbackText = computed(() => {
  const result = props.action.tool?.result;
  return typeof result === 'string' ? result : JSON.stringify(result, null, 2);
});
</script>

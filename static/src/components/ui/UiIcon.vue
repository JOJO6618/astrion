<script setup lang="ts">
/**
 * 通用图标：/static/icons/<name>.svg 以 mask 渲染，颜色 = currentColor。
 * 尺寸由使用方通过 width/height 控制（默认 16）。
 */
import { computed } from 'vue';

const props = defineProps<{ name: string }>();
const style = computed(() => ({ '--ui-icon-src': `url('/static/icons/${props.name}.svg')` }));
</script>

<template><span class="ui-icon" :style="style" aria-hidden="true" /></template>

<style scoped>
.ui-icon {
  display: inline-block;
  width: 16px;
  height: 16px;
  flex: none;
  background: currentColor;
  mask: var(--ui-icon-src) center / contain no-repeat;
  -webkit-mask: var(--ui-icon-src) center / contain no-repeat;
}
</style>

<script setup lang="ts">
/**
 * 下拉触发器（DESIGN.md §6.5）：32 高 / R8 / 13px，左右 11／10，右侧向下箭头 12。
 * 底色 --surface-soft（分组卡片内由设置页样式改为 --group-card-control），无边框；
 * hover 与展开时在底色上叠一层 --hover-bg，键盘焦点时边框变 --border-strong。
 */
import { ref } from 'vue';
import UiIcon from './UiIcon.vue';

defineProps<{
  text: string;
  open: boolean;
  /** 未选值时以占位色显示 */
  placeholder?: boolean;
  /** 可选的前置图标 */
  icon?: string;
  disabled?: boolean;
  label?: string;
  haspopup?: 'listbox' | 'dialog';
}>();

const el = ref<HTMLButtonElement | null>(null);
defineExpose({ el });
</script>

<template>
  <button
    ref="el"
    type="button"
    class="ui-select-trigger"
    :class="{ 'is-placeholder': placeholder }"
    :disabled="disabled"
    :aria-haspopup="haspopup || 'listbox'"
    :aria-expanded="open"
    :aria-label="label || undefined"
  >
    <UiIcon v-if="icon" :name="icon" class="ui-select-icon" />
    <span class="ui-select-text">{{ text }}</span>
    <UiIcon name="chevron-down" class="ui-select-chevron" />
  </button>
</template>

<style scoped>
.ui-select-trigger {
  height: 32px;
  min-width: 120px;
  max-width: 240px;
  padding: 0 10px 0 11px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background-color: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition:
    background-color 140ms ease,
    border-color 140ms ease;
}

.ui-select-trigger:not(:disabled):hover,
.ui-select-trigger[aria-expanded='true'] {
  background-image: linear-gradient(var(--hover-bg), var(--hover-bg));
}

.ui-select-trigger:focus-visible {
  outline: none;
  border-color: var(--border-strong);
}

.ui-select-trigger:disabled {
  cursor: default;
  opacity: 0.5;
}

.ui-select-icon {
  width: 14px;
  height: 14px;
  flex: none;
  color: var(--text-secondary);
}

.ui-select-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ui-select-trigger.is-placeholder .ui-select-text {
  color: var(--text-tertiary);
}

.ui-select-chevron {
  width: 12px;
  height: 12px;
  margin-left: auto;
  color: var(--text-tertiary);
}
</style>

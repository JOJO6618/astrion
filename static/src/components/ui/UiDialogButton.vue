<script setup lang="ts">
/**
 * 弹窗底部按钮（DESIGN.md §6.7）：高 34、最小宽 60、左右 14、13/500；G3 参数 次 5／主 6。
 * primary 墨色、quiet 跟随轮廓描边、danger 危险色。
 */
import { ref } from 'vue';
import { useG3 } from '@/composables/useG3';
import './uiG3.css';

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'quiet' | 'danger';
    type?: 'button' | 'submit';
    disabled?: boolean;
  }>(),
  { variant: 'quiet', type: 'button', disabled: false }
);

const el = ref<HTMLButtonElement>();
useG3(el, () => (props.variant === 'quiet' ? 5 : 6));
</script>

<template>
  <button
    ref="el"
    :type="type"
    class="ui-dialog-button ui-g3"
    :class="`is-${variant}`"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>

<style scoped>
.ui-dialog-button {
  height: 34px;
  min-width: 60px;
  padding: 0 14px;
  flex: none;
  border: 0;
  border-radius: 0;
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 140ms ease;
}

.is-quiet {
  background: var(--surface-raised);
  color: var(--text-primary);
}

.is-quiet:not(:disabled):hover {
  --g3-border: var(--border-strong);
  background: var(--surface-soft);
}

.is-primary {
  --g3-border: transparent;
  background: var(--text-primary);
  color: var(--surface-base);
}

.is-primary:not(:disabled):hover {
  background: var(--text-secondary);
}

.is-danger {
  --g3-border: transparent;
  background: var(--state-danger);
  color: var(--on-accent);
}

.is-danger:not(:disabled):hover {
  background: var(--state-danger-strong);
}

/* 遮罩会裁掉外侧 outline，焦点框向内收 */
.ui-dialog-button:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: -4px;
}

.is-primary:focus-visible,
.is-danger:focus-visible {
  outline-color: var(--surface-base);
}

.ui-dialog-button:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>

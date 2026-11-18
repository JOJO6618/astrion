<template>
  <button
    type="button"
    class="user-bubble-action-btn message-action-btn"
    :class="{ 'message-action-btn--text': !icon, copied }"
    :style="icon ? { '--message-action-icon': icon } : undefined"
    :aria-label="label"
  >
    <slot />
  </button>
</template>

<script setup lang="ts">
defineProps<{
  /** mask 图像的 CSS 值，传 ICONS.xxx（`var(--svg-…)`），不要传地址 */
  icon?: string;
  label: string;
  copied?: boolean;
}>();
</script>

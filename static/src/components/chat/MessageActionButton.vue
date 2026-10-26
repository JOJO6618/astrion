<template>
  <button
    type="button"
    class="user-bubble-action-btn message-action-btn"
    :class="{ 'message-action-btn--text': !icon, copied }"
    :style="icon ? { '--message-action-icon': `url(${icon})` } : undefined"
    :aria-label="label"
  >
    <slot />
  </button>
</template>

<script setup lang="ts">
defineProps<{
  icon?: string;
  label: string;
  copied?: boolean;
}>();
</script>

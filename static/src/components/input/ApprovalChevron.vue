<template>
  <svg viewBox="0 0 16 16" fill="none" aria-hidden="true" class="approval-chevron">
    <path :d="up ? 'M4 10L8 6L12 10' : 'M4 6L8 10L12 6'" />
  </svg>
</template>

<script setup lang="ts">
defineProps<{ up?: boolean }>();
</script>

<style scoped>
.approval-chevron {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>

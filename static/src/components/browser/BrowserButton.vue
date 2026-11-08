<template>
  <button type="button" class="nb-button" :aria-label="label" :data-tooltip="label">
    <BrowserIcon :name="name" />
  </button>
</template>
<script setup lang="ts">
import { ICONS } from '@/utils/icons';
import BrowserIcon from './BrowserIcon.vue';
defineProps<{ name: keyof typeof ICONS; label: string }>();
</script>
<style scoped>
.nb-button {
  position: relative;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  display: inline-flex;
  justify-content: center;
  align-items: center;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}
.nb-button:hover:not(:disabled) {
  background: var(--surface-muted);
  color: var(--text-primary);
}
.nb-button:disabled {
  opacity: 0.35;
  cursor: default;
}
.nb-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.nb-button:hover::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 30;
  height: 26px;
  display: flex;
  align-items: center;
  padding: 0 8px;
  border: 1px solid var(--border-default);
  border-radius: 4px;
  color: var(--text-primary);
  background: var(--surface-base);
  white-space: nowrap;
  font-size: 11px;
  pointer-events: none;
  animation: nb-tooltip 0s 0.65s both;
}
@keyframes nb-tooltip {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .nb-button:hover::after {
    animation: none;
  }
}
</style>

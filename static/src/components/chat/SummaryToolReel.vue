<template>
  <span class="summary-tool-reel-window">
    <span
      class="summary-tool-reel-track"
      :class="phase"
      :style="{ transform: `translateY(${offsetPx}px)` }"
    >
      <span v-for="(item, index) in items" :key="`${index}-${item}`" class="summary-tool-reel-item">
        {{ item }}
      </span>
    </span>
  </span>
</template>

<script setup lang="ts">
import type { SummaryReelView } from '@/composables/useSummaryToolReel';
defineProps<SummaryReelView>();
</script>

<style scoped>
.summary-tool-reel-window {
  position: relative;
  display: inline-block;
  width: min(100%, 36em);
  height: 26px;
  overflow: hidden;
  vertical-align: top;
}

.summary-tool-reel-window::before,
.summary-tool-reel-window::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  z-index: 1;
  height: 6px;
  pointer-events: none;
}

.summary-tool-reel-window::before {
  top: 0;
  background: linear-gradient(180deg, var(--surface-base), transparent);
}

.summary-tool-reel-window::after {
  bottom: 0;
  background: linear-gradient(0deg, var(--surface-base), transparent);
}

.summary-tool-reel-track {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  display: block;
  will-change: transform;
}

.summary-tool-reel-track.rolling {
  transition: transform 520ms cubic-bezier(0.22, 0.9, 0.25, 1);
}

.summary-tool-reel-track.settle {
  transition: transform 170ms cubic-bezier(0.2, 0.72, 0.26, 1);
}

.summary-tool-reel-item {
  display: block;
  height: 26px;
  line-height: 26px;
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(body[data-theme='light']) .summary-tool-reel-item {
  color: var(--text-tertiary);
}

@media (prefers-reduced-motion: reduce) {
  .summary-tool-reel-track.rolling,
  .summary-tool-reel-track.settle {
    transition: none;
  }
}
</style>

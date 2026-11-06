<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';
import type { UsageTooltipState } from './types';

defineOptions({ name: 'UsageTooltip' });

const props = defineProps<{ state: UsageTooltipState }>();

const el = ref<HTMLElement | null>(null);
const position = ref({ left: '0px', top: '0px' });

/** 优先放在锚点上方，空间不足时放到下方；左右夹在视口内 */
watch(
  () => [props.state.visible, props.state.anchor, props.state.title, props.state.main],
  async () => {
    if (!props.state.visible || !props.state.anchor) return;
    await nextTick();
    const box = el.value;
    if (!box) return;
    const anchor = props.state.anchor;
    const width = box.offsetWidth;
    const height = box.offsetHeight;
    let top = anchor.top - height - 8;
    if (top < 8) top = anchor.bottom + 8;
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, anchor.left + anchor.width / 2 - width / 2));
    position.value = { left: `${left}px`, top: `${top}px` };
  }
);
</script>

<template>
  <Teleport to="body">
    <div v-if="state.visible" ref="el" class="usage-tooltip" :style="position" role="tooltip">
      <div class="usage-tooltip-title">{{ state.title }}</div>
      <div v-if="state.main" class="usage-tooltip-main">{{ state.main }}</div>
      <div v-if="state.rows.length" class="usage-tooltip-rows">
        <template v-for="row in state.rows" :key="row[0]">
          <span>{{ row[0] }}</span><b>{{ row[1] }}</b>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 与对话费用浮层同一视觉（实体面板、中性投影、不透明） */
.usage-tooltip {
  position: fixed;
  z-index: 10020;
  max-width: min(280px, calc(100vw - 16px));
  padding: 9px 12px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-primary);
  box-shadow: var(--shadow-soft);
  font-size: 12px;
  line-height: 1.55;
  pointer-events: none;
  font-variant-numeric: tabular-nums;
}

.usage-tooltip-title {
  font-weight: 600;
}

.usage-tooltip-main {
  font-size: 13px;
}

.usage-tooltip-rows {
  margin-top: 4px;
  display: grid;
  grid-template-columns: auto auto;
  column-gap: 14px;
  color: var(--text-tertiary);
}

.usage-tooltip-rows b {
  font-weight: 500;
  color: var(--text-primary);
  text-align: right;
  white-space: nowrap;
}
</style>

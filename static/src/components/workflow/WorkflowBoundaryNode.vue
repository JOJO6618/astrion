<template>
  <div
    class="boundary-node"
    :class="[
      `boundary-node--${data.node.kind}`,
      {
        'boundary-node--selected': selected,
        'boundary-node--issue': data.hasIssue,
      },
    ]"
  >
    <!-- 结束节点左侧入桩：每条入线一个桩（含 1 个常驻空桩），上下均布 -->
    <Handle
      v-if="data.node.kind === 'end'"
      v-for="i in data.inCount + 1"
      :key="`in-${i - 1}`"
      :id="`in-${i - 1}`"
      type="target"
      :position="Position.Left"
      :class="{ 'boundary-node__in--empty': i > data.inCount }"
      :style="{ top: `${(i / (data.inCount + 2)) * 100}%` }"
    />
    <span
      class="icon boundary-node__icon"
      :style="iconSrc(data.node.kind === 'start' ? ICONS.play : ICONS.octagon)"
      aria-hidden="true"
    ></span>
    <span class="boundary-node__label">{{ data.node.name || (data.node.kind === 'start' ? '开始' : '结束') }}</span>
    <Handle v-if="data.node.kind === 'start'" id="out-0" type="source" :position="Position.Right" />
  </div>
</template>

<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { ICONS } from '@/utils/icons';
import type { WorkflowEndDef, WorkflowStartDef } from './workflowModel';

interface BoundaryNodeData {
  node: WorkflowStartDef | WorkflowEndDef;
  hasIssue: boolean;
  /** 结束节点左入桩已占用数量（组件渲染 inCount + 1，含 1 个常驻空桩） */
  inCount: number;
  outCount: number;
}

defineProps<{
  id: string;
  data: BoundaryNodeData;
  selected?: boolean;
}>();

function iconSrc(url: string) {
  return { '--icon-src': `url(${url})` } as Record<string, string>;
}
</script>

<style scoped lang="scss">
.boundary-node {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 16px;
  border-radius: 999px;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  font-size: 12px;
  color: var(--text-secondary);
  user-select: none;
  cursor: grab;
  transition: border-color 0.15s ease;

  &:active {
    cursor: grabbing;
  }

  &--selected {
    border-color: var(--accent);
  }

  &--issue {
    border-color: var(--state-danger);
  }

  /* 空入桩弱化：仅作拉新线的入口 */
  :deep(.boundary-node__in--empty) {
    opacity: 0.35;
  }

  &__icon {
    --icon-size: 13px;
    flex-shrink: 0;
  }

  &--start {
    color: var(--text-primary);
    border-color: var(--state-success);

    .boundary-node__icon {
      color: var(--state-success);
    }

    &.boundary-node--selected {
      border-color: var(--accent);
    }

    &.boundary-node--issue {
      border-color: var(--state-danger);
    }
  }

  &--end {
    border-style: dashed;
  }
}
</style>

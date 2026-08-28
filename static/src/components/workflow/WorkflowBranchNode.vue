<template>
  <div
    class="branch-node"
    :class="{
      'branch-node--selected': selected,
      'branch-node--issue': data.hasIssue,
    }"
    :style="{ minHeight: `${bodyHeight}px` }"
  >
    <!-- 左：入桩 × n + 1 个常驻空桩（拉新线用，弱化显示；与右侧行为一致） -->
    <Handle
      v-for="i in data.inCount + 1"
      :key="`in-${i - 1}`"
      :id="`in-${i - 1}`"
      type="target"
      :position="Position.Left"
      :class="{ 'branch-node__in--empty': i > data.inCount }"
      :style="{ top: `${(i / (data.inCount + 2)) * 100}%` }"
    />
    <!-- 右：出桩 × n + 1 个常驻空桩（拉新线用，弱化显示） -->
    <Handle
      v-for="i in data.outCount + 1"
      :key="`out-${i - 1}`"
      :id="`out-${i - 1}`"
      type="source"
      :position="Position.Right"
      :class="{ 'branch-node__out--empty': i > data.outCount }"
      :style="{ top: `${(i / (data.outCount + 2)) * 100}%` }"
    />
    <!-- 上/下：驳回红线接收入口（语义相同，按相对位置自动选向，只进不出） -->
    <Handle id="in-top" type="target" :position="Position.Top" class="branch-node__reject-in" />
    <Handle id="in-bottom" type="target" :position="Position.Bottom" class="branch-node__reject-in" />
    <div class="branch-node__body">
      <span class="icon branch-node__icon" :style="iconSrc(ICONS.gitBranch)" aria-hidden="true"></span>
      <span class="branch-node__name">{{ data.node.name || data.node.id }}</span>
      <span class="branch-node__meta">{{ $t('workflow.branchOutMeta', { n: data.outCount }) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import { ICONS } from '@/utils/icons';
import type { WorkflowBranchDef } from './workflowModel';

interface BranchNodeData {
  node: WorkflowBranchDef;
  isEntry: boolean;
  hasIssue: boolean;
  /** 入桩数量（= 入线数量，至少 1） */
  inCount: number;
  /** 已占用出桩数量（= 右出线数量）；实际渲染 outCount + 1（1 个空桩） */
  outCount: number;
}

const props = defineProps<{
  id: string;
  data: BranchNodeData;
  selected?: boolean;
}>();

// 高度随左右桩数多的一方自适应（各含 1 个空桩），桩位不挤
const bodyHeight = computed(() => {
  const rows = Math.max(props.data.inCount + 1, props.data.outCount + 1, 2);
  return Math.max(64, rows * 22 + 28);
});

function iconSrc(url: string) {
  return { '--icon-src': `url(${url})` } as Record<string, string>;
}
</script>

<style scoped lang="scss">
.branch-node {
  width: 150px;
  background: var(--surface-raised);
  border: 1px dashed var(--border-strong);
  border-radius: 10px;
  box-shadow: var(--shadow-soft);
  font-family: inherit;
  cursor: grab;
  transition: border-color 0.15s ease;

  &:active {
    cursor: grabbing;
  }

  &--selected {
    border-color: var(--accent);
    border-style: solid;
  }

  &--issue {
    border-color: var(--state-danger);
    border-style: solid;
  }

  /* 空桩弱化：仅作拉新线的入口 */
  :deep(.branch-node__out--empty),
  :deep(.branch-node__in--empty) {
    opacity: 0.35;
  }

  /* 驳回入口（上/下）视觉弱化：仅红线落点 */
  :deep(.branch-node__reject-in) {
    opacity: 0.5;
  }

  &__body {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    min-height: inherit;
    padding: 8px 14px;
    pointer-events: none;
  }

  &__icon {
    --icon-size: 14px;
    color: var(--text-secondary);
  }

  &__name {
    max-width: 100%;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__meta {
    font-size: 10px;
    color: var(--text-tertiary);
    white-space: nowrap;
  }
}
</style>

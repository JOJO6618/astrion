<template>
  <div
    class="review-node"
    :class="{
      'review-node--selected': selected,
      'review-node--issue': data.hasIssue,
    }"
  >
    <!-- 菱形衬底：内联 SVG polygon（clip-path 会裁掉描边，故用矢量描边） -->
    <svg class="review-node__shape" viewBox="0 0 170 96" preserveAspectRatio="none" aria-hidden="true">
      <polygon class="review-node__polygon" points="85,1 169,48 85,95 1,48" />
    </svg>
    <!-- 左：前进入桩 -->
    <Handle id="in" type="target" :position="Position.Left" />
    <!-- 右：通过出桩（蓝线，至多 1 条） -->
    <Handle id="out-0" type="source" :position="Position.Right" class="review-node__pass-out" />
    <!-- 上/下：驳回出桩（红线，同一 rejectTo，方向按目标相对位置自动选） -->
    <Handle id="reject-out" type="source" :position="Position.Top" class="review-node__reject-out" />
    <Handle id="reject-out-b" type="source" :position="Position.Bottom" class="review-node__reject-out" />
    <div class="review-node__body">
      <span class="icon review-node__eye" :style="iconSrc(ICONS.eye)" aria-hidden="true"></span>
      <span class="review-node__name">{{ data.node.name || data.node.id }}</span>
      <span class="review-node__meta">{{ $t('workflow.rejectLimitMeta', { n: data.node.maxRejects }) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { ICONS } from '@/utils/icons';
import type { WorkflowReviewDef } from './workflowModel';

interface ReviewNodeData {
  node: WorkflowReviewDef;
  isEntry: boolean;
  hasIssue: boolean;
}

defineProps<{
  id: string;
  data: ReviewNodeData;
  selected?: boolean;
}>();

function iconSrc(url: string) {
  return { '--icon-src': `url(${url})` } as Record<string, string>;
}
</script>

<style scoped lang="scss">
.review-node {
  width: 170px;
  height: 96px;
  position: relative;
  font-family: inherit;
  cursor: grab;

  &:active {
    cursor: grabbing;
  }

  &__shape {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  &__polygon {
    fill: var(--surface-raised);
    stroke: var(--border-default);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
    transition: stroke 0.15s ease;
  }

  &--selected &__polygon {
    stroke: var(--accent);
  }

  &--issue &__polygon {
    stroke: var(--state-danger);
  }

  /* 通过出桩染蓝、驳回归出桩染红：颜色即语义 */
  :deep(.review-node__pass-out) {
    background: var(--state-info);
  }

  :deep(.review-node__reject-out) {
    background: var(--state-danger);
  }

  &__body {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    padding: 0 28px;
    pointer-events: none;
  }

  &__eye {
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

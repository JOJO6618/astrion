<template>
  <div
    class="stage-node"
    :class="{
      'stage-node--selected': selected,
      'stage-node--issue': data.hasIssue,
    }"
  >
    <!-- 左：前进入桩（白/蓝线） -->
    <Handle id="in" type="target" :position="Position.Left" />
    <!-- 右：前退出桩（白线，至多 1 条） -->
    <Handle id="out-0" type="source" :position="Position.Right" />
    <!-- 上/下：驳回红线接收入口（语义相同，按相对位置自动选向，只进不出） -->
    <Handle id="in-top" type="target" :position="Position.Top" class="stage-node__reject-in" />
    <Handle id="in-bottom" type="target" :position="Position.Bottom" class="stage-node__reject-in" />
    <div class="stage-node__head">
      <span
        v-if="data.isEntry"
        class="icon stage-node__flag stage-node__flag--entry"
        :style="iconSrc(ICONS.flag)"
        :aria-label="$t('workflow.entryStageAriaLabel')"
      ></span>
      <span
        v-else-if="data.isTerminal"
        class="icon stage-node__flag stage-node__flag--terminal"
        :style="iconSrc(ICONS.octagon)"
        :aria-label="$t('workflow.terminalStageAriaLabel')"
      ></span>
      <span v-else class="stage-node__dot" aria-hidden="true"></span>
      <span class="stage-node__name">{{ data.node.name || data.node.id }}</span>
    </div>
    <div class="stage-node__goal" :class="{ 'stage-node__goal--empty': !data.node.goal }">
      {{ data.node.goal || $t('workflow.stageGoalUnset') }}
    </div>
    <div class="stage-node__foot">
      <span class="stage-node__id">{{ data.node.id }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core';
import { ICONS } from '@/utils/icons';
import type { WorkflowStageDef } from './workflowModel';

interface StageNodeData {
  node: WorkflowStageDef;
  isEntry: boolean;
  isTerminal: boolean;
  hasIssue: boolean;
}

defineProps<{
  id: string;
  data: StageNodeData;
  selected?: boolean;
}>();

function iconSrc(url: string) {
  return { '--icon-src': `url(${url})` } as Record<string, string>;
}
</script>

<style scoped lang="scss">
.stage-node {
  width: 210px;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
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
  }

  &--issue {
    border-color: var(--state-danger);
  }

  /* 驳回入口（上/下）视觉弱化：仅红线落点 */
  :deep(.stage-node__reject-in) {
    opacity: 0.5;
  }

  &__head {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 10px;
    border-bottom: 1px solid var(--border-default);
  }

  &__flag {
    --icon-size: 13px;
    flex-shrink: 0;

    &--entry {
      color: var(--state-success);
    }

    &--terminal {
      color: var(--text-tertiary);
    }
  }

  &__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-tertiary);
    flex-shrink: 0;
    /* 与 13px 图标视觉体积对齐：圆点略小，居中补偿 */
    margin: 0 3.5px;
  }

  &__name {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__goal {
    height: 30px;
    line-height: 30px;
    padding: 0 10px;
    font-size: 12px;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;

    &--empty {
      color: var(--text-tertiary);
      font-style: italic;
    }
  }

  &__foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 26px;
    padding: 0 10px;
    border-top: 1px solid var(--border-default);
    font-size: 11px;
    color: var(--text-tertiary);
  }

  &__id {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}
</style>

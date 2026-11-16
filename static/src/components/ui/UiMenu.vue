<script setup lang="ts">
/**
 * 菜单（DESIGN.md §6.4）：基于 UiPopover。选项 32 高、R8、左右 8、图标 16 与文字间距 10、行间 2；
 * hover 与键盘焦点用 --hover-bg；危险项 --state-danger；禁用项透明度 0.4。
 * actions=true 为操作菜单（无选中勾）；否则为选项菜单，当前值显示选中勾。
 */
import { computed } from 'vue';
import UiPopover from './UiPopover.vue';
import UiIcon from './UiIcon.vue';

export interface UiMenuItem {
  value: string;
  label: string;
  icon?: string;
  /** 次文字：显示在主文字下方（设置项下拉，§6.4） */
  description?: string;
  danger?: boolean;
  disabled?: boolean;
}

const props = withDefaults(
  defineProps<{
    open: boolean;
    anchor?: HTMLElement | null;
    items: UiMenuItem[];
    value?: string;
    actions?: boolean;
    label?: string;
    alignEnd?: boolean;
    /** 宽度下限；默认操作菜单 165、选项菜单 205，带次文字的选项菜单 280。 */
    minWidth?: number;
  }>(),
  { anchor: null, value: undefined, actions: false, label: '', alignEnd: false, minWidth: 0 }
);

const hasDescription = computed(() => props.items.some((item) => item.description));
const width = computed(
  () => props.minWidth || (props.actions ? 165 : hasDescription.value ? 280 : 205)
);

const emit = defineEmits<{
  (event: 'select', value: string): void;
  (event: 'close', restoreFocus: boolean): void;
}>();

function choose(item: UiMenuItem) {
  if (item.disabled) return;
  emit('close', true);
  emit('select', item.value);
}
</script>

<template>
  <UiPopover
    :open="open"
    :anchor="anchor"
    :role="actions ? 'menu' : 'listbox'"
    :label="label"
    :min-width="width"
    :align-end="alignEnd"
    @close="emit('close', $event)"
  >
    <div class="ui-popover-scroll">
      <button
        v-for="item in props.items"
        :key="item.value"
        type="button"
        class="ui-menu-item"
        :class="{ 'is-danger': item.danger, 'has-desc': item.description }"
        :role="actions ? 'menuitem' : 'option'"
        :aria-selected="actions ? undefined : item.value === value"
        :disabled="item.disabled"
        data-popover-item
        @click="choose(item)"
      >
        <UiIcon v-if="item.icon" :name="item.icon" class="ui-menu-icon" />
        <span v-if="item.description" class="ui-menu-copy">
          <span class="ui-menu-label">{{ item.label }}</span>
          <span class="ui-menu-desc">{{ item.description }}</span>
        </span>
        <span v-else class="ui-menu-label">{{ item.label }}</span>
        <UiIcon v-if="!actions && item.value === value" name="check" class="ui-menu-check" />
      </button>
    </div>
  </UiPopover>
</template>

<style scoped>
/* 滚动放在内层：滚动条随面板内边距内缩，不压到外框圆角上 */
.ui-popover-scroll {
  flex: 1;
  min-height: 0;
  display: grid;
  gap: 2px;
  align-content: start;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.ui-popover-scroll::-webkit-scrollbar {
  width: 4px;
}

.ui-popover-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 4px;
}

.ui-menu-item {
  width: 100%;
  height: 32px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 120ms ease;
}

.ui-menu-item:not(:disabled):hover,
.ui-menu-item:focus-visible {
  outline: none;
  background: var(--hover-bg);
}

.ui-menu-item:disabled {
  cursor: default;
}

.ui-menu-item:disabled > * {
  opacity: 0.4;
}

.ui-menu-icon {
  width: 16px;
  height: 16px;
  color: var(--text-secondary);
}

.ui-menu-item:not(:disabled):hover .ui-menu-icon,
.ui-menu-item:focus-visible .ui-menu-icon {
  color: var(--text-primary);
}

.ui-menu-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 两行选项：主文字 13/20，次文字 12/18；上下 6 让两行块在 R8 热区里居中 */
.ui-menu-item.has-desc {
  height: auto;
  min-height: 32px;
  padding: 6px 8px;
  white-space: normal;
}

.ui-menu-copy {
  min-width: 0;
  display: grid;
}

.ui-menu-copy .ui-menu-label {
  line-height: 20px;
  white-space: nowrap;
}

.ui-menu-desc {
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 18px;
}

.ui-menu-check {
  flex: none;
  width: 14px;
  height: 14px;
  margin-left: auto;
  color: var(--text-secondary);
}

.ui-menu-item.is-danger,
.ui-menu-item.is-danger .ui-menu-icon {
  color: var(--state-danger);
}
</style>

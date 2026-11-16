<script setup lang="ts">
import SchedulePopover from './SchedulePopover.vue';
import ScheduleIcon from './ScheduleIcon.vue';
import type { ScheduleMenuItem } from './useSchedules';

const props = withDefaults(
  defineProps<{
    open: boolean;
    anchor?: HTMLElement | null;
    items: ScheduleMenuItem[];
    /** 选项模式下的当前值；操作菜单不显示勾选。 */
    value?: string;
    actions?: boolean;
    label?: string;
    alignEnd?: boolean;
  }>(),
  { anchor: null, value: undefined, actions: false, label: '', alignEnd: false }
);
const emit = defineEmits<{
  (event: 'select', value: string): void;
  (event: 'close', restoreFocus: boolean): void;
}>();

function choose(item: ScheduleMenuItem) {
  if (item.disabled) return;
  emit('close', true);
  emit('select', item.value);
}
</script>
<template>
  <SchedulePopover
    :open="open"
    :anchor="anchor"
    :role="actions ? 'menu' : 'listbox'"
    :label="label"
    :min-width="actions ? 165 : 205"
    :align-end="alignEnd"
    @close="emit('close', $event)"
  >
    <button
      v-for="item in props.items"
      :key="item.value"
      type="button"
      class="sch-popover-option"
      :class="{ 'is-danger': item.danger }"
      :role="actions ? 'menuitem' : 'option'"
      :aria-selected="actions ? undefined : String(item.value) === String(value)"
      :disabled="item.disabled"
      data-popover-item
      @click="choose(item)"
    >
      <ScheduleIcon v-if="item.icon" :name="item.icon" />
      <span class="sch-popover-label">{{ item.label }}</span>
      <ScheduleIcon
        v-if="!actions && String(item.value) === String(value)"
        name="check"
        class="sch-option-check"
      />
    </button>
  </SchedulePopover>
</template>

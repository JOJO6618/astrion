<script setup lang="ts">
/**
 * 下拉选择（DESIGN.md §6.5）：UiSelectTrigger + 选项菜单 UiMenu。
 * 选项带 description 时为「主文字 + 次文字」两行模板，否则为单行主文字模板。
 */
import { computed, ref } from 'vue';
import { useUiStore } from '@/stores/ui';
import UiMenu, { type UiMenuItem } from './UiMenu.vue';
import UiSelectTrigger from './UiSelectTrigger.vue';

const props = withDefaults(
  defineProps<{
    modelValue: string;
    options: UiMenuItem[];
    placeholder?: string;
    label?: string;
    icon?: string;
    disabled?: boolean;
    /** 规则锁定时点击只提示原因，不展开。 */
    lockedReason?: string;
    /** 触发器靠右（设置行右列）时菜单右缘对齐触发器 */
    alignEnd?: boolean;
  }>(),
  {
    placeholder: '',
    label: '',
    icon: '',
    disabled: false,
    lockedReason: '',
    alignEnd: false
  }
);
const ui = useUiStore();
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>();

const trigger = ref<InstanceType<typeof UiSelectTrigger> | null>(null);
const open = ref(false);
const selected = computed(() => props.options.find((item) => item.value === props.modelValue));

function close(restoreFocus: boolean) {
  open.value = false;
  if (restoreFocus) trigger.value?.el?.focus({ preventScroll: true });
}
function toggle() {
  if (props.lockedReason) {
    ui.pushToast({ message: props.lockedReason, type: 'info' });
    return;
  }
  open.value = !open.value;
}
</script>

<template>
  <UiSelectTrigger
    ref="trigger"
    :text="selected?.label || placeholder"
    :placeholder="!selected"
    :open="open"
    :icon="icon"
    :disabled="disabled"
    :label="label"
    @click="toggle"
  />
  <UiMenu
    :open="open"
    :anchor="trigger?.el"
    :items="options"
    :value="modelValue"
    :label="label"
    :align-end="alignEnd"
    @select="emit('update:modelValue', $event)"
    @close="close"
  />
</template>

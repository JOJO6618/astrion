<script setup lang="ts">
import { computed, ref } from 'vue';
import { useUiStore } from '@/stores/ui';
import ScheduleIcon from './ScheduleIcon.vue';
import ScheduleMenu from './ScheduleMenu.vue';

const props = withDefaults(
  defineProps<{
    modelValue: string | number | undefined;
    options: { value: string; label: string }[];
    label: string;
    icon?: string;
    /** 选项被规则锁定时点击只提示原因，不展开。 */
    lockedReason?: string;
    placeholder?: string;
  }>(),
  { icon: '', lockedReason: '', placeholder: '' }
);
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>();
const ui = useUiStore();
const trigger = ref<HTMLButtonElement>();
const open = ref(false);
const current = computed(() =>
  props.options.find((item) => String(item.value) === String(props.modelValue ?? ''))
);
const text = computed(
  () => current.value?.label || props.placeholder || String(props.modelValue ?? '')
);

function toggle() {
  if (props.lockedReason) {
    ui.pushToast({ message: props.lockedReason, type: 'info' });
    return;
  }
  open.value = !open.value;
}
function close(restoreFocus: boolean) {
  open.value = false;
  if (restoreFocus) trigger.value?.focus({ preventScroll: true });
}
</script>
<template>
  <button
    ref="trigger"
    type="button"
    class="sch-select-trigger"
    aria-haspopup="listbox"
    :aria-expanded="open"
    :aria-label="label"
    @click="toggle"
  >
    <ScheduleIcon v-if="icon" :name="icon" />
    <span class="sch-select-label">{{ text }}</span>
    <ScheduleIcon name="chevron-down" class="sch-select-chevron" />
  </button>
  <ScheduleMenu
    :open="open"
    :anchor="trigger"
    :items="options"
    :value="String(modelValue ?? '')"
    :label="label"
    @close="close"
    @select="emit('update:modelValue', $event)"
  />
</template>

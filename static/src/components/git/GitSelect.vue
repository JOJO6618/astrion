<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import GitIcon from './GitIcon.vue';
const props = defineProps<{
  modelValue: string;
  options: Array<{ value: string; label: string; secondary?: string }>;
  label: string;
  open?: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void;
  (event: 'update:open', value: boolean): void;
}>();
const root = ref<HTMLElement>();
const internalOpen = ref(false);
const expanded = computed(() => props.open ?? internalOpen.value);
const selected = computed(
  () => props.options.find((option) => option.value === props.modelValue)?.label || props.label
);
function setOpen(value: boolean) {
  if (value && props.disabled) return;
  internalOpen.value = value;
  emit('update:open', value);
}
function close(event: FocusEvent) {
  if (!root.value?.contains(event.relatedTarget as Node)) setOpen(false);
}
function choose(value: string) {
  emit('update:modelValue', value);
  setOpen(false);
  root.value?.querySelector<HTMLButtonElement>('.git-select__trigger')?.focus();
}
async function navigate(event: KeyboardEvent) {
  const direction = event.key === 'ArrowUp' ? -1 : 1;
  if (!expanded.value) {
    setOpen(true);
    await nextTick();
  }
  const options = Array.from(
    root.value?.querySelectorAll<HTMLButtonElement>('[role="option"]') || []
  );
  const index = options.indexOf(document.activeElement as HTMLButtonElement);
  const next =
    index < 0
      ? direction > 0
        ? 0
        : options.length - 1
      : (index + direction + options.length) % options.length;
  options[next]?.focus();
}
function escape() {
  setOpen(false);
  root.value?.querySelector<HTMLButtonElement>('.git-select__trigger')?.focus();
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) setOpen(false);
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>

<template>
  <div
    ref="root"
    class="git-select"
    @focusout="close"
    @keydown.esc.stop="escape"
    @keydown.down.prevent="navigate"
    @keydown.up.prevent="navigate"
  >
    <button
      type="button"
      class="git-button git-select__trigger"
      :disabled="disabled"
      :aria-label="label"
      :aria-expanded="expanded"
      aria-haspopup="listbox"
      @click="setOpen(!expanded)"
    >
      <span>{{ selected }}</span
      ><GitIcon name="chevron" />
    </button>
    <div v-if="expanded" class="git-select__menu" role="listbox" :aria-label="label">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        role="option"
        :aria-selected="option.value === modelValue"
        @click="choose(option.value)"
      >
        <span class="git-select__option"
          ><span>{{ option.label }}</span
          ><small v-if="option.secondary">{{ option.secondary }}</small></span
        >
        <span v-if="option.value === modelValue" aria-hidden="true">✓</span>
      </button>
      <div v-if="!options.length" class="git-empty">{{ $t('git.noOptions') }}</div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.git-select {
  position: relative;
  min-width: 0;
}
.git-select__trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.git-select__trigger > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-select__menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 30;
  width: max-content;
  min-width: 100%;
  max-width: min(360px, calc(100vw - 24px));
  max-height: min(320px, 60vh);
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px;
  box-sizing: border-box;
  overflow: auto;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  box-shadow: var(--shadow-mid);
  scrollbar-width: thin;
}
.git-select__menu button {
  width: 100%;
  height: 42px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.git-select__menu button:hover,
.git-select__menu button[aria-selected='true'] {
  background: var(--hover-bg);
}
.git-select__option {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  min-width: 0;
}
.git-select__option > span,
.git-select__option > small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-select__option > small {
  color: var(--text-muted);
  font-size: 11px;
}
</style>

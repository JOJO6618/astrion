<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import GitIcon from './GitIcon.vue';
const props = withDefaults(
  defineProps<{
    modelValue: string;
    options: Array<{ value: string; label: string; secondary?: string }>;
    label: string;
    open?: boolean | null;
    disabled?: boolean;
    /** Escape clipping in a scrollable form; all behavior remains shared. */
    floating?: boolean;
    compact?: boolean;
  }>(),
  { open: null, disabled: false, floating: false, compact: false }
);
const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void;
  (event: 'update:open', value: boolean): void;
}>();
const root = ref<HTMLElement>();
const menu = ref<HTMLElement>();
const internalOpen = ref(false);
const expanded = computed(() => props.open ?? internalOpen.value);
const selected = computed(
  () => props.options.find((option) => option.value === props.modelValue)?.label || props.label
);
const direction = ref('down');
const position = ref<Record<string, string>>({});
function place() {
  if (!root.value || !menu.value) return;
  const rect = root.value.getBoundingClientRect();
  const below = window.innerHeight - rect.bottom - 12;
  const above = rect.top - 12;
  const up = below < Math.min(menu.value.scrollHeight, 240) && above > below;
  direction.value = up ? 'up' : 'down';
  if (props.floating) {
    const width = Math.min(
      360,
      Math.max(rect.width, menu.value.scrollWidth),
      window.innerWidth - 24
    );
    position.value = {
      position: 'fixed',
      left: `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
      top: up ? 'auto' : `${rect.bottom + 6}px`,
      bottom: up ? `${window.innerHeight - rect.top + 6}px` : 'auto',
      width: `${width}px`,
      minWidth: `${Math.min(rect.width, window.innerWidth - 24)}px`,
      maxHeight: `${Math.max(50, Math.min(320, up ? above : below))}px`
    };
  } else position.value = up ? { top: 'auto', bottom: 'calc(100% + 6px)' } : {};
}
async function setOpen(value: boolean) {
  if (value && props.disabled) return;
  internalOpen.value = value;
  emit('update:open', value);
  if (value) {
    await nextTick();
    place();
  }
}
function contains(element: Node | null) {
  return !!element && (root.value?.contains(element) || menu.value?.contains(element));
}
function close(event: FocusEvent) {
  if (!contains(event.relatedTarget as Node)) void setOpen(false);
}
function choose(value: string) {
  emit('update:modelValue', value);
  void setOpen(false);
  root.value?.querySelector<HTMLButtonElement>('.git-select__trigger')?.focus();
}
async function navigate(event: KeyboardEvent) {
  const direction = event.key === 'ArrowUp' ? -1 : 1;
  if (!expanded.value) {
    await setOpen(true);
    await nextTick();
  }
  const options = Array.from(
    menu.value?.querySelectorAll<HTMLButtonElement>('[role="option"]') || []
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
  void setOpen(false);
  root.value?.querySelector<HTMLButtonElement>('.git-select__trigger')?.focus();
}
function outside(event: PointerEvent) {
  if (!contains(event.target as Node)) void setOpen(false);
}
function scroll(event: Event) {
  if (expanded.value && !menu.value?.contains(event.target as Node)) place();
}
onMounted(() => {
  document.addEventListener('pointerdown', outside);
  window.addEventListener('resize', place);
  document.addEventListener('scroll', scroll, true);
});
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside);
  window.removeEventListener('resize', place);
  document.removeEventListener('scroll', scroll, true);
});
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
    <Teleport to="body" :disabled="!floating">
      <Transition name="git-select-popover" @enter="place">
        <div
          v-if="expanded"
          ref="menu"
          class="git-select__menu"
          :class="{
            'git-select__menu--compact': compact,
            'git-select__menu--up': direction === 'up'
          }"
          :style="position"
          role="listbox"
          :aria-label="label"
          @focusout="close"
          @keydown.esc.stop="escape"
          @keydown.down.stop.prevent="navigate"
          @keydown.up.stop.prevent="navigate"
        >
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
            ><span v-if="option.value === modelValue" aria-hidden="true">✓</span>
          </button>
          <div v-if="!options.length" class="git-empty">{{ $t('git.noOptions') }}</div>
        </div>
      </Transition>
    </Teleport>
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
  z-index: 2100;
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
  color: var(--text-primary);
  transform-origin: top center;
}
.git-select__menu--up {
  transform-origin: bottom center;
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
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.git-select__menu--compact {
  gap: 2px;
  border-radius: 12px;
}
.git-select__menu--compact button {
  height: 32px;
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
.git-select-popover-enter-active,
.git-select-popover-leave-active {
  transition:
    transform 140ms ease,
    opacity 120ms ease;
}
.git-select-popover-leave-active {
  pointer-events: none;
}
.git-select-popover-enter-from,
.git-select-popover-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}
.git-select__menu--up.git-select-popover-enter-from,
.git-select__menu--up.git-select-popover-leave-to {
  transform: translateY(4px) scale(0.98);
}
@media (prefers-reduced-motion: reduce) {
  .git-select-popover-enter-active,
  .git-select-popover-leave-active {
    transition: none;
  }
}
</style>

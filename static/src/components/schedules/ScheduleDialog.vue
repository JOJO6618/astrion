<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { observeG3 } from '@/utils/g3Contours';
import ScheduleIcon from './ScheduleIcon.vue';

const props = defineProps<{ title: string; wide?: boolean; busy?: boolean }>();
const emit = defineEmits<{ (event: 'close'): void }>();
const panel = ref<HTMLElement>();
let pressedScrim = false;
let release: (() => void) | undefined;
let previous: HTMLElement | null = null;

function close() {
  if (!props.busy) emit('close');
}
function onScrimDown() {
  pressedScrim = true;
}
function onScrimClick() {
  if (pressedScrim) close();
  pressedScrim = false;
}
function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close();
}
function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !panel.value) return;
  const focusable = Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [contenteditable="plaintext-only"], [tabindex="0"]'
    )
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
onMounted(async () => {
  previous = document.activeElement as HTMLElement | null;
  document.addEventListener('keydown', onDocumentKeydown);
  await nextTick();
  if (!panel.value) return;
  release = observeG3(panel.value);
  (
    panel.value.querySelector<HTMLElement>('input, textarea') ||
    panel.value.querySelector<HTMLElement>('button')
  )?.focus();
});
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onDocumentKeydown);
  release?.();
  if (previous?.isConnected) previous.focus({ preventScroll: true });
});
</script>
<template>
  <div class="sch-modal-scrim" @pointerdown.self="onScrimDown" @click.self="onScrimClick">
    <div class="sch-modal-shell" :class="{ 'is-wide': wide }">
      <section
        ref="panel"
        class="sch-modal sch-g3"
        data-g3="18"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        @keydown="trapFocus"
      >
        <header class="sch-modal-header">
          <h2>{{ title }}</h2>
          <button
            type="button"
            class="sch-icon-button sch-modal-close"
            :aria-label="t('schedules.closeDialog')"
            :disabled="busy"
            @click="close"
          >
            <ScheduleIcon name="x" />
          </button>
        </header>
        <div class="sch-modal-body"><slot /></div>
        <footer class="sch-modal-actions"><slot name="actions" /></footer>
      </section>
    </div>
  </div>
</template>

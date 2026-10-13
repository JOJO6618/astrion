<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import GitIcon from './GitIcon.vue';
const props = defineProps<{ subject: string; message?: string }>();
const root = ref<HTMLElement>();
const line = ref<HTMLElement>();
const open = ref(false);
const overflow = ref(false);
const fullMessage = computed(() => props.message?.trim() || props.subject);
const expandable = computed(() => fullMessage.value !== props.subject.trim() || overflow.value);
let observer: ResizeObserver | undefined;
function measure() {
  if (!line.value) return;
  // Ignore the symbol's own width so the threshold remains stable when it appears.
  overflow.value =
    line.value.scrollWidth > line.value.clientWidth + (expandable.value ? 18 : 0) + 1;
}
async function toggle() {
  open.value = !open.value;
  if (open.value) {
    await nextTick();
    root.value?.querySelector<HTMLButtonElement>('.git-message__close')?.focus();
  }
}
function close() {
  open.value = false;
  root.value?.querySelector<HTMLButtonElement>('.git-message__toggle')?.focus();
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) open.value = false;
}
watch(
  () => [props.subject, props.message],
  async () => {
    open.value = false;
    overflow.value = false;
    await nextTick();
    measure();
  }
);
onMounted(() => {
  observer = new ResizeObserver(measure);
  if (line.value) observer.observe(line.value);
  document.addEventListener('pointerdown', outside);
  measure();
});
onBeforeUnmount(() => {
  observer?.disconnect();
  document.removeEventListener('pointerdown', outside);
});
</script>

<template>
  <div ref="root" class="git-message" @keydown.esc.stop="close">
    <div class="git-message__line">
      <button
        v-if="expandable"
        type="button"
        class="git-message__toggle"
        :aria-label="$t('git.expandMessage')"
        :aria-expanded="open"
        @click="toggle"
      >
        <GitIcon name="chevron" />
      </button>
      <h2 ref="line">{{ subject }}</h2>
    </div>
    <div v-if="open" class="git-message__popover" role="dialog" :aria-label="$t('git.fullMessage')">
      <header>
        <span>{{ $t('git.fullMessage') }}</span
        ><button
          type="button"
          class="git-icon-button git-message__close"
          :aria-label="$t('common.close')"
          @click="close"
        >
          <GitIcon name="close" />
        </button>
      </header>
      <pre>{{ fullMessage }}</pre>
    </div>
  </div>
</template>

<style scoped lang="scss">
.git-message {
  position: relative;
  min-width: 0;
}
.git-message__line {
  height: 28px;
  display: flex;
  align-items: center;
  min-width: 0;
}
.git-message h2 {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 17px;
  line-height: 28px;
  font-weight: 600;
}
.git-message__toggle {
  width: 18px;
  height: 28px;
  flex: 0 0 18px;
  display: flex;
  align-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
}
.git-message__popover {
  position: absolute;
  z-index: 25;
  top: calc(100% + 8px);
  left: 0;
  width: min(680px, calc(100vw - 44px));
  max-height: min(420px, 60vh);
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 7px;
  box-shadow: var(--shadow-mid);
}
.git-message__popover header {
  height: 42px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px 0 16px;
  border-bottom: 1px solid var(--border-default);
  color: var(--text-secondary);
  font-size: 12px;
}
.git-message__popover pre {
  margin: 0;
  padding: 14px 16px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  line-height: 1.65;
  scrollbar-width: thin;
}
</style>

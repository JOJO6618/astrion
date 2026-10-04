<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onBeforeUnmount } from 'vue';
import { t } from '@/locales';

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: string]; submit: [] }>();
const input = ref<HTMLTextAreaElement>(),
  marker = ref<HTMLElement>(),
  mirror = ref<HTMLElement>();
const lineHeight = 31,
  maxLines = 5,
  inputHeight = ref(lineHeight);
const focused = ref(false),
  composing = ref(false),
  selected = ref(false),
  caretIndex = ref(0);
const caret = ref({ left: 0, top: 0, visible: false });
const prefix = computed(() => props.modelValue.slice(0, caretIndex.value));
const suffix = computed(() => props.modelValue.slice(caretIndex.value));
let observer: ResizeObserver;

async function syncCaret() {
  const element = input.value;
  if (!element) return;
  caretIndex.value = element.selectionStart;
  selected.value = element.selectionStart !== element.selectionEnd;
  await nextTick();
  if (!marker.value || !input.value) return;
  // The mirror wraps at the textarea's exact content width. It measures both
  // explicit newlines and soft wraps without temporarily collapsing the input.
  if (mirror.value) {
    mirror.value.style.width = `${element.clientWidth}px`;
    const rows = Math.max(1, Math.ceil(mirror.value.scrollHeight / lineHeight));
    inputHeight.value = Math.min(maxLines, rows) * lineHeight;
    await nextTick();
  }
  const left = marker.value.offsetLeft - element.scrollLeft;
  const line = Math.floor(marker.value.offsetTop / lineHeight);
  const top = line * lineHeight + (lineHeight - marker.value.offsetHeight) / 2 - element.scrollTop;
  caret.value = {
    left,
    top,
    visible: left >= 0 && left <= element.clientWidth && top >= 0 && top < element.clientHeight
  };
}
function change(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value);
  void syncCaret();
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || composing.value || event.keyCode === 229) return;
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    emit('submit');
  }
}
function selectionChanged() {
  if (document.activeElement === input.value) void syncCaret();
}
function focus() {
  input.value?.focus();
}
watch(
  () => props.modelValue,
  () => {
    void syncCaret();
  },
  { flush: 'post' }
);
onMounted(() => {
  observer = new ResizeObserver(() => {
    void syncCaret();
  });
  if (input.value) observer.observe(input.value);
  document.addEventListener('selectionchange', selectionChanged);
  void syncCaret();
});
onBeforeUnmount(() => {
  observer?.disconnect();
  document.removeEventListener('selectionchange', selectionChanged);
});
defineExpose({ focus });
</script>

<template>
  <div
    class="quick-prompt"
    :class="{ 'is-composing': composing }"
    :style="{ height: `${inputHeight}px` }"
  >
    <textarea
      ref="input"
      :value="modelValue"
      rows="1"
      wrap="soft"
      :placeholder="t('quickEntry.placeholder')"
      :aria-label="t('quickEntry.send')"
      @input="change"
      @keydown="keydown"
      @keyup="syncCaret"
      @click="syncCaret"
      @select="syncCaret"
      @scroll="syncCaret"
      @focus="
        focused = true;
        syncCaret();
      "
      @blur="focused = false"
      @compositionstart="composing = true"
      @compositionend="
        composing = false;
        syncCaret();
      "
    />
    <div ref="mirror" class="prompt-mirror" aria-hidden="true">
      <span>{{ prefix }}</span
      ><span ref="marker" class="caret-marker" /><span>{{ suffix }}</span>
    </div>
    <span
      v-show="focused && !composing && !selected && caret.visible"
      class="prompt-caret"
      :style="{ left: `${caret.left}px`, top: `${caret.top}px` }"
      aria-hidden="true"
    />
  </div>
</template>

<style scoped>
.quick-prompt {
  position: relative;
  flex: 1;
  min-width: 70px;
  margin-left: 6px;
  height: 31px;
  font-family: 'Baskerville', 'Iowan Old Style', 'Songti SC', serif;
  font-size: 20px;
  font-weight: 500;
  line-height: 31px;
  letter-spacing: -0.35px;
}
.quick-prompt textarea,
.prompt-mirror {
  box-sizing: border-box;
  width: 100%;
  padding: 0;
  margin: 0;
  border: 0;
  font: inherit;
  letter-spacing: inherit;
  white-space: pre-wrap;
  overflow-wrap: break-word;
}
.quick-prompt textarea {
  display: block;
  height: 100%;
  resize: none;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
  caret-color: transparent;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-width: none;
}
.quick-prompt textarea::placeholder {
  color: var(--text-secondary);
  opacity: 1;
}
.quick-prompt.is-composing textarea {
  caret-color: var(--quick-entry-caret);
}
.prompt-mirror {
  position: absolute;
  left: 0;
  top: 0;
  min-height: 31px;
  visibility: hidden;
  pointer-events: none;
}
.caret-marker {
  display: inline-block;
  width: 0;
  height: 21px;
  vertical-align: middle;
}
.prompt-caret {
  position: absolute;
  width: 2px;
  height: 21px;
  border-radius: 1px;
  pointer-events: none;
  background: var(--quick-entry-caret);
  animation: quick-caret-fade 1.75s ease-in-out infinite;
}
@keyframes quick-caret-fade {
  0%,
  12%,
  100% {
    opacity: 1;
  }
  48%,
  68% {
    opacity: 0;
  }
}
@media (max-width: 760px) {
  .quick-prompt {
    font-size: 17px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .prompt-caret {
    animation: none;
  }
}
</style>

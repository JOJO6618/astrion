<template>
  <form class="chat-message-editor" @submit.prevent="$emit('save', text, false)">
    <textarea
      ref="input"
      v-model="text"
      class="chat-message-editor__input"
      :aria-label="$t('chatMode.edit')"
      autofocus
      @keydown.esc.prevent="$emit('cancel')"
    />
    <div class="chat-message-editor__actions">
      <button type="button" @click="$emit('cancel')">{{ $t('chatMode.cancel') }}</button>
      <button type="submit" :disabled="!text.trim() || saving">{{ $t('chatMode.save') }}</button>
      <button
        v-if="message.role === 'user'"
        type="button"
        :disabled="!text.trim() || busy || saving"
        @click="$emit('save', text, true)"
      >
        {{ $t('chatMode.send') }}
      </button>
    </div>
  </form>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ChatMessage } from '@/chat/types';
import { textOf } from '@/chat/types';
const props = defineProps<{ message: ChatMessage; busy: boolean; saving: boolean }>();
defineEmits(['save', 'cancel']);
const text = ref(textOf(props.message));
const input = ref<HTMLTextAreaElement>();
function resize() {
  if (!input.value) return;
  const element = input.value;
  const scrollTop = element.scrollTop;
  element.style.height = 'auto';
  element.style.height = `${element.scrollHeight + 2}px`;
  element.scrollTop = scrollTop;
}
watch(text, resize, { flush: 'post' });
onMounted(() => {
  resize();
  input.value?.focus({ preventScroll: true });
  window.addEventListener('resize', resize);
});
onBeforeUnmount(() => window.removeEventListener('resize', resize));
</script>
<style scoped lang="scss">
.chat-message-editor {
  padding: 16px 0;
}
.chat-message-editor__input {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-height: 200px;
  max-height: min(840px, 75dvh);
  appearance: none;
  resize: none;
  overflow-y: auto;
  padding: 14px 16px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  outline: none;
  box-shadow: none;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  line-height: 1.7;
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}
.chat-message-editor__input:focus {
  border-color: var(--text-secondary);
}
.chat-message-editor__input::-webkit-scrollbar {
  width: 5px;
}
.chat-message-editor__input::-webkit-scrollbar-thumb {
  border-radius: 3px;
  background: var(--border-default);
}
.chat-message-editor__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
button {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  color: var(--text-primary);
  cursor: pointer;
}
button:hover:not(:disabled) {
  background: var(--hover-bg);
}
button:focus-visible {
  outline: 1px solid var(--text-secondary);
  outline-offset: 2px;
}
button:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>

<template>
  <div ref="root" class="chat-choice" @keydown.esc="open = false">
    <button
      type="button"
      :disabled="disabled"
      class="chat-choice__trigger"
      :aria-label="label"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span>{{ options.find((option) => option.value === modelValue)?.label || label }}</span>
      <span aria-hidden="true">⌄</span>
    </button>
    <div v-if="open" class="chat-choice__options" role="listbox" :aria-label="label">
      <button
        v-for="option in options"
        :key="String(option.value)"
        type="button"
        role="option"
        :aria-selected="option.value === modelValue"
        @click="choose(option.value)"
      >
        <span>{{ option.label }}</span
        ><span v-if="option.value === modelValue" aria-hidden="true">✓</span>
      </button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
defineProps<{
  modelValue: string | null;
  label: string;
  disabled?: boolean;
  options: Array<{ value: string | null; label: string }>;
}>();
const emit = defineEmits<{ (event: 'update:modelValue', value: string | null): void }>();
const root = ref<HTMLElement>();
const open = ref(false);
function choose(value: string | null) {
  emit('update:modelValue', value);
  open.value = false;
}
function outside(event: Event) {
  if (!root.value?.contains(event.target as Node)) open.value = false;
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside));
</script>
<style scoped lang="scss">
.chat-choice {
  position: relative;
  min-width: 0;
}
.chat-choice button {
  color: var(--text-primary);
  font: inherit;
  height: 38px;
  cursor: pointer;
  border: 0;
}
.chat-choice__trigger {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 280px;
  background: transparent;
  border-radius: 6px;
  padding: 0 10px;
}
.chat-choice__trigger > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-choice button:disabled {
  opacity: 0.45;
  cursor: default;
}
.chat-choice__options {
  position: absolute;
  top: 44px;
  left: 0;
  min-width: 190px;
  max-width: min(360px, 85vw);
  max-height: 340px;
  overflow: auto;
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 5px;
  box-shadow: var(--shadow-mid);
  z-index: 25;
}
.chat-choice__options button {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  width: 100%;
  background: transparent;
  border-radius: 5px;
  padding: 0 10px;
}
.chat-choice__options button > span:first-child {
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.chat-choice button:hover {
  background: var(--hover-bg);
}
</style>

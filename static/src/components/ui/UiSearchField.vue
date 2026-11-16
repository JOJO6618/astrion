<script setup lang="ts">
/**
 * 常驻搜索框（DESIGN.md §2.2：搜索本质是常驻输入时直接呈现完整输入框）。
 * 34 高 / R8，放在悬浮面板内；有内容时右侧出现清除按钮，Esc 先清空再失焦。
 * 方向键与回车透传给父组件（keydown 事件），用于驱动结果列表。
 */
import { ref } from 'vue';
import UiIcon from './UiIcon.vue';

const model = defineModel<string>({ default: '' });
defineProps<{ placeholder: string; clearLabel: string }>();
const emit = defineEmits<{ (event: 'keydown', value: KeyboardEvent): void }>();

const input = ref<HTMLInputElement | null>(null);

const clear = () => {
  model.value = '';
  input.value?.focus();
};

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && model.value) {
    event.preventDefault();
    event.stopPropagation();
    model.value = '';
    return;
  }
  if (event.key === 'Escape') {
    input.value?.blur();
    return;
  }
  emit('keydown', event);
};

defineExpose({ focus: () => input.value?.focus() });
</script>

<template>
  <div class="ui-search-field">
    <UiIcon name="search" class="ui-search-icon" />
    <input
      ref="input"
      v-model="model"
      type="text"
      autocomplete="off"
      spellcheck="false"
      :placeholder="placeholder"
      :aria-label="placeholder"
      @keydown="onKeydown"
    />
    <button
      v-if="model"
      type="button"
      class="ui-search-clear"
      :aria-label="clearLabel"
      @click="clear"
    >
      <UiIcon name="x" />
    </button>
  </div>
</template>

<style scoped>
.ui-search-field {
  height: 34px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 5px 0 9px;
  background: var(--surface-base);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  transition: border-color 140ms ease;
}

.ui-search-field:focus-within {
  border-color: var(--border-strong);
}

.ui-search-icon {
  width: 14px;
  height: 14px;
  color: var(--text-tertiary);
}

.ui-search-field input {
  min-width: 0;
  flex: 1;
  height: 30px;
  padding: 0;
  border: 0;
  outline: none;
  background: transparent;
  box-shadow: none;
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
}

.ui-search-field input::placeholder {
  color: var(--text-tertiary);
}

.ui-search-clear {
  width: 24px;
  height: 24px;
  flex: none;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
}

.ui-search-clear .ui-icon {
  width: 12px;
  height: 12px;
}

.ui-search-clear:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.ui-search-clear:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: 1px;
}
</style>

<script setup lang="ts">
/**
 * 图标按钮（DESIGN.md §6.2）：32×32、R8、图标 17，颜色 --text-secondary，
 * hover 底色 --hover-bg 且图标变 --text-primary。必须提供可访问名称。
 */
import UiIcon from './UiIcon.vue';

defineProps<{ icon: string; label: string; disabled?: boolean }>();
const emit = defineEmits<{ (event: 'click', value: MouseEvent): void }>();
</script>

<template>
  <button
    type="button"
    class="ui-icon-button"
    :aria-label="label"
    :disabled="disabled"
    @click="emit('click', $event)"
  >
    <UiIcon :name="icon" />
  </button>
</template>

<style scoped>
.ui-icon-button {
  width: 32px;
  height: 32px;
  display: inline-grid;
  place-items: center;
  flex: none;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background-color 140ms ease,
    color 140ms ease;
}

.ui-icon-button .ui-icon {
  width: 17px;
  height: 17px;
}

.ui-icon-button:not(:disabled):hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.ui-icon-button:disabled {
  opacity: 0.4;
  cursor: default;
}

.ui-icon-button:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: 2px;
}
</style>

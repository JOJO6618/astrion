<script setup lang="ts">
/**
 * 药丸开关（对齐设计稿 .switch：36×20，on 时 accent 底 + 滑块右移）。
 * 自定义按钮实现（role=switch），不使用原生 checkbox 外观。
 */
withDefaults(
  defineProps<{
    on: boolean;
    disabled?: boolean;
    label?: string;
  }>(),
  {
    disabled: false,
    label: ''
  }
);

const emit = defineEmits<{ (e: 'toggle'): void }>();
</script>

<template>
  <button
    type="button"
    class="pill-switch"
    :class="{ on }"
    role="switch"
    :aria-checked="on"
    :aria-label="label"
    :title="label"
    :disabled="disabled"
    @click="emit('toggle')"
  >
    <span class="pill-switch__knob" aria-hidden="true"></span>
  </button>
</template>

<style scoped>
.pill-switch {
  width: 36px;
  height: 20px;
  border: 0;
  border-radius: 999px;
  background: var(--switch-track);
  position: relative;
  transition: background 0.15s ease;
  flex-shrink: 0;
  cursor: pointer;
  padding: 0;
}

.pill-switch__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--surface-raised);
  transition: transform 0.15s ease;
}

.pill-switch.on {
  background: var(--accent);
}

.pill-switch.on .pill-switch__knob {
  transform: translateX(16px);
}

.pill-switch:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>

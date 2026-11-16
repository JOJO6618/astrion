<script setup lang="ts">
/**
 * 药丸开关（DESIGN.md §6.8）：36×20，开启为固定系统蓝（三主题一致），滑块位置与轨道颜色同时过渡。
 *
 * 两种用法：
 * - 交互式（默认）：自身是 role=switch 按钮，点击 emit('toggle')。
 * - decorative：只渲染外观，放进 <label> 里与隐藏的 checkbox 配合（label 负责交互与语义），
 *   避免在 label 内嵌套按钮导致点击不落到 checkbox。
 */
withDefaults(
  defineProps<{
    checked: boolean;
    disabled?: boolean;
    label?: string;
    decorative?: boolean;
  }>(),
  { disabled: false, label: '', decorative: false }
);

const emit = defineEmits<{ (event: 'toggle'): void }>();
</script>

<template>
  <span
    v-if="decorative"
    class="ui-switch"
    :class="{ 'is-on': checked, 'is-disabled': disabled }"
    aria-hidden="true"
  >
    <span class="ui-switch-knob" />
  </span>
  <button
    v-else
    type="button"
    class="ui-switch"
    :class="{ 'is-on': checked, 'is-disabled': disabled }"
    role="switch"
    :aria-checked="checked"
    :aria-label="label || undefined"
    :disabled="disabled"
    @click="emit('toggle')"
  >
    <span class="ui-switch-knob" aria-hidden="true" />
  </button>
</template>

<style scoped>
.ui-switch {
  position: relative;
  display: inline-block;
  width: 36px;
  height: 20px;
  flex: none;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: var(--switch-track);
  cursor: pointer;
  transition: background-color 180ms ease;
}

.ui-switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--switch-knob);
  box-shadow: 0 1px 2px var(--shadow-color);
  transform: translateX(0);
  transition: transform 180ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.ui-switch.is-on {
  background: var(--switch-on);
}

.ui-switch.is-on .ui-switch-knob {
  transform: translateX(16px);
}

.ui-switch.is-disabled {
  opacity: 0.4;
  cursor: default;
}

button.ui-switch:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: 2px;
}

/* label 包裹的装饰用法：键盘焦点落在隐藏 checkbox 上，焦点环画到开关 */
:global(input:focus-visible + .ui-switch) {
  outline: 2px solid var(--text-secondary);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .ui-switch,
  .ui-switch-knob {
    transition: none;
  }
}
</style>

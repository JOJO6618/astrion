<script setup lang="ts">
// 开关：滑块位置与轨道颜色同时过渡；元素被复用而非重建，点击动画不会被重绘吞掉。
defineProps<{ checked: boolean; label: string; disabled?: boolean }>();
const emit = defineEmits<{ (event: 'toggle'): void }>();
</script>
<template>
  <button
    type="button"
    class="sch-switch"
    role="switch"
    :aria-checked="checked"
    :aria-label="label"
    :disabled="disabled"
    @click="emit('toggle')"
  >
    <span class="sch-switch-thumb" aria-hidden="true" />
  </button>
</template>

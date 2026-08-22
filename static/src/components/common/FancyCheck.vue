<script setup lang="ts">
/**
 * 打勾框（fancy-check）——全局唯一的自定义 checkbox 图标实现。
 *
 * 未勾选时描出圆角方框轮廓，勾选时同一条路径通过 dasharray/offset
 * 动画「变形」为对勾。所有开关统一引用本组件，禁止再内联复制 SVG。
 *
 * 用法：
 *   <label class="xxx-toggle-row">
 *     <input type="checkbox" :checked="form.x" @change="..." />
 *     <FancyCheck :checked="form.x" />
 *   </label>
 *
 * - checked：与配套 input 的 checked 保持一致（本组件不处理交互，仅渲染）
 * - size：svg 边长，默认 22；小号场景（如「默认」标记）传 14
 * - accent-checked：勾选后描边变为 --accent 强调色（默认不变色，仅形状变形）
 */
withDefaults(
  defineProps<{
    checked?: boolean;
    size?: number;
    accentChecked?: boolean;
  }>(),
  {
    checked: false,
    size: 22,
    accentChecked: false
  }
);
</script>

<template>
  <span
    class="fancy-check"
    :class="{ 'is-checked': checked, 'accent-checked': accentChecked }"
    aria-hidden="true"
  >
    <svg viewBox="0 0 64 64" :style="{ width: `${size}px`, height: `${size}px` }">
      <path
        d="M 0 16 V 56 A 8 8 90 0 0 8 64 H 56 A 8 8 90 0 0 64 56 V 8 A 8 8 90 0 0 56 0 H 8 A 8 8 90 0 0 0 8 V 16 L 32 48 L 64 16 V 8 A 8 8 90 0 0 56 0 H 8 A 8 8 90 0 0 0 8 V 56 A 8 8 90 0 0 8 64 H 56 A 8 8 90 0 0 64 56 V 16"
        pathLength="575.0541381835938"
        class="fancy-path"
      ></path>
    </svg>
  </span>
</template>

<style scoped>
.fancy-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
}

.fancy-check svg {
  display: block;
  overflow: visible;
}

.fancy-path {
  fill: none;
  stroke: var(--text-secondary);
  stroke-width: 5;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition:
    stroke-dasharray 0.5s ease,
    stroke-dashoffset 0.5s ease,
    stroke 0.2s ease;
  stroke-dasharray: 241 9999999;
  stroke-dashoffset: 0;
}

.fancy-check.is-checked .fancy-path {
  stroke-dasharray: 70.5096664428711 9999999;
  stroke-dashoffset: -262.2723388671875;
}

.fancy-check.is-checked.accent-checked .fancy-path {
  stroke: var(--accent);
}
</style>

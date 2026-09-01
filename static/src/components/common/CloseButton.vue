<script setup lang="ts">
/**
 * 全局统一关闭按钮（X）——两种变体，全站所有「关闭」语义按钮统一引用。
 *
 * 变体：
 *  - boxed（默认）：用于一切「有容器承载」场景（窗口/弹窗/抽屉/侧边栏/面板/通知卡片）。
 *    28×28px（sm=24×24px），圆角 7px（sm=6px），透明底，hover/active 出现 --hover-bg 底色块。
 *  - bare：用于「悬空无容器」场景（当前仅 ImageLightbox 灯箱），无背景/无圆角/无底色块，
 *    仅字形变色；颜色通过 `--close-btn-color` / `--close-btn-color-hover` CSS 变量由父容器覆盖。
 *
 * 接口：
 *  - variant: 'boxed' | 'bare'（默认 boxed）
 *  - size: 'sm' | 'md'（默认 md=28px）
 *  - label: aria-label 文案（必传，调用方给 i18n 文案）
 *  - emit: click
 *
 * 字形复用项目图标机制 iconStyle('x')（等价 VersioningDialog 中的
 * `<span class="icon icon-sm" :style="iconStyle('x')" />`），通过在组件内直接由 ICONS
 * 构造 `--icon-src` 的 style 对象实现，无需依赖父组件透传 iconStyle prop。
 */
import { ICONS } from '../../utils/icons';

withDefaults(
  defineProps<{
    variant?: 'boxed' | 'bare';
    size?: 'sm' | 'md';
    label: string;
    disabled?: boolean;
  }>(),
  {
    variant: 'boxed',
    size: 'md',
    disabled: false
  }
);

defineEmits<{
  (e: 'click', evt: MouseEvent): void;
}>();

// 等价 iconStyle('x')：mask 使用 --icon-src 指向 x.svg，字形即图标
const iconStyle = { '--icon-src': `url(${ICONS.x})` };
</script>

<template>
  <button
    type="button"
    class="close-btn"
    :class="[`close-btn--${variant}`, `close-btn--${size}`]"
    :aria-label="label"
    :disabled="disabled"
    v-bind="$attrs"
    @click="$emit('click', $event)"
  >
    <span class="icon icon-sm" :style="iconStyle" aria-hidden="true"></span>
  </button>
</template>

<style scoped>
.close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  border: 0;
  padding: 0;
  background: transparent;
  cursor: pointer;
}

.close-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ===== boxed：有容器承载 ===== */
.close-btn--boxed {
  --_size: 28px;
  --_radius: 7px;
  width: var(--_size);
  height: var(--_size);
  border-radius: var(--_radius);
  color: var(--text-secondary);
  transition:
    background 140ms ease,
    color 140ms ease;
}

.close-btn--boxed.close-btn--sm {
  --_size: 24px;
  --_radius: 6px;
}

@media (hover: hover) {
  .close-btn--boxed:hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }
}

.close-btn--boxed:active {
  background: var(--hover-bg);
  color: var(--text-primary);
}

/* ===== bare：悬空无容器 ===== */
.close-btn--bare {
  width: 36px;
  height: 36px;
  color: var(--close-btn-color);
  transition: color 140ms ease;
}

/* 默认配色用 :where() 零特异性声明：父容器（如 ImageLightbox）在同元素上覆盖
   --close-btn-color / --close-btn-color-hover 时始终能赢过默认值。
   注意：禁止写成 var(--x, 兜底) 形式，会被 stylelint 颜色规则拦截。 */
:where(.close-btn--bare) {
  --close-btn-color: var(--text-secondary);
  --close-btn-color-hover: var(--text-primary);
}

.close-btn--bare .icon {
  --icon-size: 20px;
}

@media (hover: hover) {
  .close-btn--bare:hover {
    color: var(--close-btn-color-hover);
  }
}
</style>

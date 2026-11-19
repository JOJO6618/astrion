<script lang="ts">
/** 打开中的弹窗栈：Esc 只关最上层；弹层（UiPopover）在捕获阶段先消费 Esc。 */
const openStack: symbol[] = [];
/**
 * 弹窗层级：2200 起（高于移动端面板遮罩 2000），后打开的叠在上面；
 * Teleport 的节点顺序取决于挂载顺序而非打开顺序，所以层级按栈位置算。弹层 UiPopover 在 2500。
 */
const DIALOG_LAYER = 2200;
</script>

<script setup lang="ts">
/**
 * 大弹窗基座（DESIGN.md §6.7 / §6.10）。
 *
 * 管：G3 外框、E=20 骨架、标题 20/28 + 关闭按钮、三档宽度、max-height + 正文内滚、
 *     250ms 进出动画（遮罩淡入 + 模糊 0→12，面板 translateY(18) scale(.985)，精确反向）、
 *     减少动画、打开聚焦、Tab 焦点圈、Esc／遮罩关闭（按下与抬起都在遮罩上）、忙碌禁关、关闭即恢复焦点。
 * 不管：正文内容。各弹窗只写自己的正文和按钮。
 */
import { nextTick, onBeforeUnmount, ref, useId, useSlots, watch } from 'vue';
import { t } from '@/locales';
import UiIcon from './UiIcon.vue';
import { useG3 } from '@/composables/useG3';
import './uiG3.css';

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    /** 480 / 590 / 1040（第三档，列表 + 详情或大内容） */
    size?: 'default' | 'wide' | 'xl';
    /** 忙碌时禁止 Esc、遮罩与关闭按钮 */
    busy?: boolean;
    closeOnScrim?: boolean;
  }>(),
  { size: 'default', busy: false, closeOnScrim: true }
);
const emit = defineEmits<{ (event: 'close'): void }>();
const slots = useSlots();

const titleId = useId();
const panel = ref<HTMLElement>();
const token = Symbol('ui-dialog');
let previous: HTMLElement | null = null;
let pressedOnScrim = false;
const layer = ref(DIALOG_LAYER);

useG3(panel, () => 18);

function requestClose() {
  if (!props.busy) emit('close');
}

function onScrimPointerDown(event: PointerEvent) {
  pressedOnScrim = event.target === event.currentTarget;
}

function onScrimClick(event: MouseEvent) {
  if (pressedOnScrim && event.target === event.currentTarget && props.closeOnScrim) requestClose();
  pressedOnScrim = false;
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (openStack[openStack.length - 1] !== token) return;
  event.preventDefault();
  requestClose();
}

const FOCUSABLE =
  'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !panel.value) return;
  const items = Array.from(panel.value.querySelectorAll<HTMLElement>(FOCUSABLE));
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** 首个输入，否则底部首个按钮（不落到右上角关闭按钮上） */
function focusInitial() {
  const root = panel.value;
  if (!root) return;
  const target =
    root.querySelector<HTMLElement>('[data-autofocus]') ||
    root.querySelector<HTMLElement>(
      '.ui-dialog-body input:not(:disabled), .ui-dialog-body textarea:not(:disabled)'
    ) ||
    root.querySelector<HTMLElement>('.ui-dialog-actions button:not(:disabled)') ||
    root;
  target.focus({ preventScroll: true });
}

function activate() {
  previous = document.activeElement as HTMLElement | null;
  layer.value = DIALOG_LAYER + openStack.length;
  openStack.push(token);
  document.documentElement.classList.add('ui-dialog-open');
  document.addEventListener('keydown', onDocumentKeydown);
  nextTick(focusInitial);
}

function deactivate() {
  const index = openStack.indexOf(token);
  if (index < 0) return;
  openStack.splice(index, 1);
  if (!openStack.length) document.documentElement.classList.remove('ui-dialog-open');
  document.removeEventListener('keydown', onDocumentKeydown);
  // 交互不等退出动画：立即恢复焦点（退出期间面板 pointer-events: none）
  if (previous?.isConnected) previous.focus({ preventScroll: true });
  previous = null;
}

watch(
  () => props.open,
  (open) => (open ? activate() : deactivate()),
  { immediate: true }
);
onBeforeUnmount(deactivate);
</script>

<template>
  <Teleport to="body">
    <Transition name="ui-dialog" appear>
      <div
        v-if="open"
        class="ui-dialog-scrim"
        :style="{ zIndex: layer }"
        @pointerdown="onScrimPointerDown"
        @click="onScrimClick"
      >
        <div class="ui-dialog-shell" :class="`is-${size}`">
          <section
            ref="panel"
            class="ui-dialog ui-g3"
            role="dialog"
            aria-modal="true"
            :aria-labelledby="titleId"
            :aria-busy="busy || undefined"
            tabindex="-1"
            @keydown="trapFocus"
          >
            <header class="ui-dialog-header">
              <h2 :id="titleId">{{ title }}</h2>
              <button
                type="button"
                class="ui-dialog-close"
                :aria-label="t('common.close')"
                :disabled="busy"
                @click="requestClose"
              >
                <UiIcon name="x" />
              </button>
            </header>
            <div class="ui-dialog-body" :class="{ 'is-last': !slots.actions }">
              <slot />
            </div>
            <footer v-if="slots.actions" class="ui-dialog-actions">
              <div v-if="slots.status" class="ui-dialog-status"><slot name="status" /></div>
              <slot name="actions" />
            </footer>
          </section>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style lang="scss">
/* 不加 scoped：正文里的 .ui-dialog-* 由各弹窗的插槽内容使用。 */

/* 弹窗打开期间：内容不需要滚动时滚轮会传到页面根节点，根节点不回弹、背后页面也不跟着滚 */
html.ui-dialog-open {
  overflow: hidden;
  overscroll-behavior: none;
}

.ui-dialog-scrim {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--overlay-scrim);
  backdrop-filter: blur(12px);
  color: var(--text-primary);
  font-family:
    -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;

  *,
  *::before,
  *::after {
    box-sizing: border-box;
  }
}

.ui-dialog-shell {
  width: min(480px, 100%);
  min-width: 0;
  filter: drop-shadow(0 6px 20px var(--shadow-color));

  &.is-wide {
    width: min(590px, 100%);
  }

  &.is-xl {
    width: min(1040px, 100%);
  }
}

/* ---------- 进出：同一路径互为反向 ---------- */
.ui-dialog-enter-active,
.ui-dialog-leave-active {
  transition:
    opacity 250ms ease,
    backdrop-filter 250ms ease;

  .ui-dialog-shell {
    transition:
      transform 250ms ease,
      opacity 250ms ease;
  }
}

.ui-dialog-leave-active {
  pointer-events: none;
}

.ui-dialog-enter-from,
.ui-dialog-leave-to {
  opacity: 0;
  backdrop-filter: blur(0);

  .ui-dialog-shell {
    opacity: 0;
    transform: translateY(18px) scale(0.985);
  }
}

/* 减少动画：直接到终态 */
@media (prefers-reduced-motion: reduce) {
  .ui-dialog-enter-active,
  .ui-dialog-leave-active,
  .ui-dialog-enter-active .ui-dialog-shell,
  .ui-dialog-leave-active .ui-dialog-shell {
    transition: none;
  }
}

/* ---------- 骨架：一个 E = 20 ---------- */
.ui-dialog {
  --e: 20px;
  width: 100%;
  max-height: min(85dvh, calc(100dvh - 48px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface-raised);
  outline: none;
}

.ui-dialog-header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--e);
  padding: var(--e) var(--e) 0;

  h2 {
    min-width: 0;
    height: 28px;
    margin: 0;
    overflow: hidden;
    font-size: 20px;
    line-height: 28px;
    font-weight: 600;
    letter-spacing: -0.4px;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
}

/* 32 热区上下 −2，头部高度只由 28 标题决定；热区右缘落在 E */
.ui-dialog-close {
  width: 32px;
  height: 32px;
  margin: -2px 0;
  flex: none;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background-color 140ms ease,
    color 140ms ease;

  .ui-icon {
    width: 17px;
    height: 17px;
  }

  &:not(:disabled):hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }

  &:focus-visible {
    outline: 2px solid var(--text-secondary);
    outline-offset: -2px;
  }

  &:disabled {
    opacity: 0.4;
    cursor: default;
  }
}

.ui-dialog-body {
  min-height: 0;
  padding: var(--e) var(--e) 0;
  overflow-x: hidden;
  overflow-y: auto;
  /* none 而不是 contain：contain 只断开滚动链，滚到头仍会回弹 */
  overscroll-behavior: none;

  &.is-last {
    padding-bottom: var(--e);
  }

  &::-webkit-scrollbar {
    width: 4px;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--border-strong);
    border-radius: 2px;
  }

  > * + * {
    margin-top: var(--e);
  }
}

.ui-dialog-actions {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: var(--e);
}

/* 底部左侧的状态文字（如「已选 2 张」），不放控件 */
.ui-dialog-status {
  min-width: 0;
  margin-right: auto;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 13px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* ---------- 正文积木 ---------- */
.ui-dialog-text {
  margin: 0;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 22px;
  white-space: pre-wrap;
}

.ui-dialog-note {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 13px;
  line-height: 20px;
}

.ui-dialog-error {
  margin: 0;
  color: var(--state-danger);
  font-size: 13px;
  line-height: 20px;
}

.ui-dialog-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.ui-dialog-label {
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 20px;
}

/* 标签行右侧的文字操作：28 高热区上下 −4，不撑高 20 标签行 */
.ui-dialog-link {
  height: 28px;
  margin: -4px -8px -4px 0;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  transition:
    background-color 140ms ease,
    color 140ms ease;

  &:not(:disabled):hover {
    background: var(--hover-bg);
    color: var(--text-primary);
  }

  &:disabled {
    opacity: 0.4;
    cursor: default;
  }
}

.ui-dialog-input,
.ui-dialog-textarea {
  width: 100%;
  border: 1px solid transparent;
  border-radius: 10px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 14px;
  line-height: 22px;
  outline: none;
  transition: border-color 140ms ease;

  &::placeholder {
    color: var(--text-tertiary);
  }

  &:focus {
    border-color: var(--border-strong);
  }

  &:disabled {
    opacity: 0.5;
  }
}

.ui-dialog-input {
  height: 40px;
  padding: 0 12px;
}

.ui-dialog-textarea {
  display: block;
  min-height: 130px;
  padding: 8px 12px;
  resize: none;
  overscroll-behavior: none;

  &.is-mono {
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
    font-size: 13px;
  }

  &::-webkit-scrollbar {
    width: 4px;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--border-strong);
    border-radius: 2px;
  }
}

/* 只读路径、日志等等宽块：与输入框同底色同圆角，不加描边 */
.ui-dialog-code {
  margin: 0;
  padding: 9px 12px;
  border-radius: 10px;
  background: var(--surface-soft);
  color: var(--text-secondary);
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 20px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

/* 文字 + 右侧开关：与设置行同形态，开关右缘落在 E */
.ui-dialog-switch-row {
  min-height: 22px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--text-primary);
  font-size: 14px;
  line-height: 22px;
  cursor: pointer;

  /* 隐藏的 checkbox 承担交互与语义，外观由紧随其后的 UiSwitch decorative 绘制 */
  > input[type='checkbox'] {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }
}

/* 进度条：4 高、中性墨色，不用品牌色 */
.ui-dialog-progress {
  height: 4px;
  overflow: hidden;
  border-radius: 2px;
  background: var(--surface-soft);

  > span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--text-primary);
    transition: width 300ms ease;
  }
}

/* 弹窗触发器：40 / R10 / 14px（§6.5），撑满字段宽度 */
.ui-dialog .ui-select-trigger {
  width: 100%;
  max-width: none;
  height: 40px;
  padding: 0 12px;
  border-radius: 10px;
  font-size: 14px;

  .ui-select-text {
    flex: 1;
  }
}
</style>

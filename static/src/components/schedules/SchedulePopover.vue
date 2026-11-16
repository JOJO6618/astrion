<script lang="ts">
// 同一时刻只保留一个弹层：打开新弹层时先让旧弹层沿原路收回。
let closeActive: (() => void) | null = null;
</script>
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    open: boolean;
    anchor?: HTMLElement | null;
    role?: 'menu' | 'listbox' | 'dialog';
    label?: string;
    /** 宽度下限：选项 205，操作菜单 165；不足时取触发器宽度。 */
    minWidth?: number;
    /** 右对齐到触发按钮（面板右上角的新建按钮）。 */
    alignEnd?: boolean;
    /** 打开后聚焦的元素；缺省聚焦已选项或第一项。 */
    focusSelector?: string;
    panelClass?: string;
  }>(),
  {
    anchor: null,
    role: 'listbox',
    label: '',
    minWidth: 205,
    alignEnd: false,
    focusSelector: '',
    panelClass: ''
  }
);
const emit = defineEmits<{ (event: 'close', restoreFocus: boolean): void }>();
const panel = ref<HTMLElement>();

function requestClose(restoreFocus = false) {
  if (props.open) emit('close', restoreFocus);
}
const closeSelf = () => requestClose(false);

// 定位与动画方向共享同一个 up/down 结果：向上翻转时变换原点与位移同步反转。
function place(element: HTMLElement) {
  const anchor = props.anchor;
  if (!anchor) return;
  const rect = anchor.getBoundingClientRect();
  const width = Math.min(Math.max(rect.width, props.minWidth), window.innerWidth - 24);
  element.style.width = `${width}px`;
  const desired = props.alignEnd ? rect.right - width : rect.left;
  const left = Math.min(Math.max(12, desired), window.innerWidth - width - 12);
  element.style.left = `${left}px`;
  element.style.top = '0px';
  element.style.maxHeight = '';
  const height = element.offsetHeight;
  const below = window.innerHeight - rect.bottom - 12;
  const above = rect.top - 12;
  const up = below < height && above > below;
  const available = Math.max(32, (up ? above : below) - 5);
  element.style.maxHeight = `${Math.min(320, window.innerHeight * 0.65, available)}px`;
  const finalHeight = element.offsetHeight;
  element.style.top = `${up ? Math.max(12, rect.top - finalHeight - 5) : rect.bottom + 5}px`;
  element.style.setProperty('--popover-offset', up ? '5px' : '-5px');
  const originX = Math.min(width, Math.max(0, rect.left + rect.width / 2 - left));
  element.style.transformOrigin = `${originX}px ${up ? 'bottom' : 'top'}`;
}

function onEnter(element: Element) {
  const el = element as HTMLElement;
  place(el);
  const target =
    (props.focusSelector && el.querySelector<HTMLElement>(props.focusSelector)) ||
    el.querySelector<HTMLElement>('[aria-selected="true"]') ||
    el.querySelector<HTMLElement>('button:not(:disabled), input');
  target?.focus({ preventScroll: true });
  // 只滚动弹层自身的列表，把已选项带到可见区域；不触发外层滚动（外层滚动会关闭弹层）。
  const box = target?.closest<HTMLElement>('.sch-model-list') || el;
  if (target && box.scrollHeight > box.clientHeight) {
    const offset =
      target.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    box.scrollTop = Math.max(0, offset - (box.clientHeight - target.offsetHeight) / 2);
  }
}

function onKeydown(event: KeyboardEvent) {
  const el = panel.value;
  if (!el) return;
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    const buttons = Array.from(
      el.querySelectorAll<HTMLElement>('[data-popover-item]:not(:disabled)')
    );
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement as HTMLElement);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  } else if (event.key === 'Tab') requestClose(false);
}

function onPointerDown(event: PointerEvent) {
  const target = event.target as Node;
  if (panel.value?.contains(target) || props.anchor?.contains(target)) return;
  requestClose(false);
}
function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return;
  // 弹层优先消费 Escape，不连带关闭所在的弹窗。
  event.stopPropagation();
  event.preventDefault();
  requestClose(true);
}
function onScroll(event: Event) {
  if (!panel.value?.contains(event.target as Node)) requestClose(false);
}

function attach() {
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('keydown', onDocumentKeydown, true);
  document.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', closeSelf);
}
function detach() {
  document.removeEventListener('pointerdown', onPointerDown, true);
  document.removeEventListener('keydown', onDocumentKeydown, true);
  document.removeEventListener('scroll', onScroll, true);
  window.removeEventListener('resize', closeSelf);
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      if (closeActive && closeActive !== closeSelf) closeActive();
      closeActive = closeSelf;
      attach();
    } else {
      if (closeActive === closeSelf) closeActive = null;
      detach();
    }
  },
  { immediate: true }
);
onBeforeUnmount(() => {
  if (closeActive === closeSelf) closeActive = null;
  detach();
});
</script>
<template>
  <Teleport to="body">
    <Transition enter-active-class="is-entering" leave-active-class="is-leaving" @enter="onEnter">
      <div
        v-if="open"
        ref="panel"
        class="sch-popover"
        :class="panelClass"
        :role="role"
        :aria-label="label || undefined"
        @keydown="onKeydown"
      >
        <slot />
      </div>
    </Transition>
  </Teleport>
</template>

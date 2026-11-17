<script lang="ts">
// 同一时刻只保留一条弹层链（DESIGN.md §6.3）：打开新弹层时，不相干的旧弹层沿原路收回；
// 触发器位于某个已打开弹层内部时（如提交面板里的分支下拉），新弹层作为它的子层叠在上面。
interface PopoverEntry {
  close: () => void;
  panel: () => HTMLElement | undefined;
}
const stack: PopoverEntry[] = [];
</script>

<script setup lang="ts">
/**
 * 弹层基座（DESIGN.md §6.3 / §6.10）：挂到 body、fixed 定位，距触发器 5px，空间不足自动上翻；
 * 定位与进出动画共享同一个上下方向结果。外框只裁切不滚动，滚动交给内容里的 .ui-popover-scroll。
 * 由定时任务页 SchedulePopover 提取而来，数值按规范统一（内边距 4、位移 4px）。
 */
import { onBeforeUnmount, ref, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    open: boolean;
    anchor?: HTMLElement | null;
    role?: 'menu' | 'listbox' | 'dialog';
    label?: string;
    /** 宽度下限：选项菜单 205，操作菜单 165；不足时取触发器宽度。 */
    minWidth?: number;
    /** 右缘对齐触发器（触发器靠右时使用）。 */
    alignEnd?: boolean;
    /** 面板内边距：菜单 4；带搜索框的模型选择 6 */
    padding?: number;
    /** 最大高度上限（默认 320），实际还受 65dvh 与可用空间限制 */
    maxHeight?: number;
  }>(),
  {
    anchor: null,
    role: 'menu',
    label: '',
    minWidth: 165,
    alignEnd: false,
    padding: 4,
    maxHeight: 320
  }
);
const emit = defineEmits<{ (event: 'close', restoreFocus: boolean): void }>();
const panel = ref<HTMLElement>();

function requestClose(restoreFocus = false) {
  if (props.open) emit('close', restoreFocus);
}
const closeSelf = () => requestClose(false);

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
  element.style.maxHeight = `${Math.min(props.maxHeight, window.innerHeight * 0.65, available)}px`;
  const finalHeight = element.offsetHeight;
  element.style.top = `${up ? Math.max(12, rect.top - finalHeight - 5) : rect.bottom + 5}px`;
  element.style.setProperty('--ui-popover-offset', up ? '4px' : '-4px');
  const originX = Math.min(width, Math.max(0, rect.left + rect.width / 2 - left));
  element.style.transformOrigin = `${originX}px ${up ? 'bottom' : 'top'}`;
}

function onEnter(element: Element) {
  const el = element as HTMLElement;
  place(el);
  const target =
    el.querySelector<HTMLElement>('[aria-selected="true"]') ||
    el.querySelector<HTMLElement>('[data-popover-item]:not(:disabled)') ||
    el.querySelector<HTMLElement>('input');
  target?.focus({ preventScroll: true });
}

function onKeydown(event: KeyboardEvent) {
  const el = panel.value;
  if (!el) return;
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    const items = Array.from(
      el.querySelectorAll<HTMLElement>('[data-popover-item]:not(:disabled)')
    );
    if (!items.length) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? items.length - 1
          : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  } else if (event.key === 'Tab') {
    requestClose(false);
  }
}

const entry: PopoverEntry = { close: closeSelf, panel: () => panel.value };

/** 自身面板或叠在上面的子弹层内的目标，都不算「外部」 */
function insideChain(target: Node) {
  if (panel.value?.contains(target)) return true;
  const index = stack.indexOf(entry);
  return index >= 0 && stack.slice(index + 1).some((child) => child.panel()?.contains(target));
}

function onPointerDown(event: PointerEvent) {
  const target = event.target as Node;
  if (insideChain(target) || props.anchor?.contains(target)) return;
  requestClose(false);
}

function onDocumentKeydown(event: KeyboardEvent) {
  // 只有最上层的弹层消费 Escape，子弹层关闭后父弹层保留
  if (event.key !== 'Escape' || stack[stack.length - 1] !== entry) return;
  // 弹层优先消费 Escape，不连带关闭所在页面或弹窗
  event.stopPropagation();
  event.preventDefault();
  requestClose(true);
}

function onScroll(event: Event) {
  if (!insideChain(event.target as Node)) requestClose(false);
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

function leaveStack() {
  const index = stack.indexOf(entry);
  if (index < 0) return;
  // 父弹层关闭时，叠在上面的子弹层一起收回
  const children = stack.splice(index);
  children.slice(1).forEach((child) => child.close());
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      if (!stack.includes(entry)) {
        const anchor = props.anchor;
        let parent = -1;
        for (let i = stack.length - 1; i >= 0; i--) {
          if (anchor && stack[i].panel()?.contains(anchor)) {
            parent = i;
            break;
          }
        }
        stack.splice(parent + 1).forEach((other) => other.close());
        stack.push(entry);
      }
      attach();
    } else {
      leaveStack();
      detach();
    }
  },
  { immediate: true }
);

onBeforeUnmount(() => {
  leaveStack();
  detach();
});
</script>

<template>
  <Teleport to="body">
    <Transition enter-active-class="is-entering" leave-active-class="is-leaving" @enter="onEnter">
      <div
        v-if="open"
        ref="panel"
        class="ui-popover"
        :role="role"
        :aria-label="label || undefined"
        :style="padding === 4 ? undefined : { padding: `${padding}px` }"
        @keydown="onKeydown"
      >
        <slot />
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ui-popover {
  position: fixed;
  z-index: 2100;
  display: flex;
  flex-direction: column;
  max-height: min(320px, 65dvh);
  max-width: calc(100vw - 24px);
  padding: 4px;
  overflow: hidden;
  box-sizing: border-box;
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  background: var(--popover-surface);
  box-shadow: var(--popover-shadow);
  color: var(--text-primary);
  font-family:
    -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  -webkit-font-smoothing: antialiased;
}

.ui-popover.is-entering {
  animation: ui-popover-in 140ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.ui-popover.is-leaving {
  pointer-events: none;
  animation: ui-popover-out 100ms cubic-bezier(0.4, 0, 1, 1) both;
}

@keyframes ui-popover-in {
  from {
    opacity: 0;
    transform: translateY(var(--ui-popover-offset)) scale(0.98);
  }

  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes ui-popover-out {
  from {
    opacity: 1;
    transform: translateY(0) scale(1);
  }

  to {
    opacity: 0;
    transform: translateY(var(--ui-popover-offset)) scale(0.98);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ui-popover.is-entering,
  .ui-popover.is-leaving {
    animation: none;
  }
}
</style>

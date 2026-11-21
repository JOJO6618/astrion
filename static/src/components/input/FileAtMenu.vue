<template>
  <transition
    name="file-at-menu-motion"
    @before-enter="$emit('transitioning', true)"
    @after-enter="$emit('transitioning', false)"
    @before-leave="$emit('transitioning', true)"
    @after-leave="$emit('transitioning', false)"
  >
    <div
      v-if="visible"
      class="file-at-menu-wrapper"
      role="listbox"
      :aria-label="$t('quickdock.atMenuAria')"
      :style="menuStyle"
      @click.stop
    >
      <div ref="fileAtList" class="file-at-menu">
        <button
          type="button"
          class="file-at-item file-at-item--picker"
          :class="{ 'file-at-item--active': activeIndex === 0 }"
          role="option"
          :aria-selected="activeIndex === 0"
          @mouseenter="$emit('hover', 0)"
          @mousedown.prevent="$emit('select', 0)"
        >
          <span class="file-at-item__name">{{ $t('quickdock.atMenuPicker') }}</span>
          <span class="file-at-item__description">{{ $t('quickdock.atMenuPickerDesc') }}</span>
        </button>
        <button
          v-for="(item, index) in fileItems"
          :key="item.path"
          type="button"
          class="file-at-item"
          :class="{ 'file-at-item--active': displayIndex(index) === activeIndex }"
          role="option"
          :aria-selected="displayIndex(index) === activeIndex"
          @mouseenter="$emit('hover', displayIndex(index))"
          @mousedown.prevent="$emit('select', displayIndex(index))"
        >
          <span class="file-at-item__name">{{ item.name }}</span>
          <span class="file-at-item__description">{{ item.path }}</span>
        </button>
        <div v-if="loading" class="file-at-empty">{{ $t('quickdock.atMenuSearching') }}</div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue';

export interface FileAtItem {
  name: string;
  path: string;
  type: 'file' | 'directory';
  extension?: string;
}

const props = defineProps<{
  visible: boolean;
  items: FileAtItem[];
  activeIndex: number;
  loading: boolean;
  menuStyle: Record<string, string>;
}>();

defineEmits<{
  (e: 'transitioning', active: boolean): void;
  (e: 'select', index: number): void;
  (e: 'hover', index: number): void;
}>();

const fileAtList = ref<HTMLElement | null>(null);

const fileItems = computed(() => props.items || []);

const displayIndex = (fileIndex: number) => fileIndex + 1;

watch(
  () => props.visible,
  () => {
    if (props.visible) {
      nextTick(() => {
        const list = fileAtList.value;
        if (list) list.scrollTop = 0;
      });
    }
  }
);

const scrollActiveIntoView = (index: number) => {
  const list = fileAtList.value;
  if (!list) return;
  // 行高 32 + 行间 2（DESIGN.md §6.4）
  const rowHeight = 32;
  const targetTop = index * (rowHeight + 2);
  const targetBottom = targetTop + rowHeight;
  if (targetTop < list.scrollTop) {
    list.scrollTop = targetTop;
  } else if (targetBottom > list.scrollTop + list.clientHeight) {
    list.scrollTop = targetBottom - list.clientHeight;
  }
};

const focusActive = () => {
  nextTick(() => {
    scrollActiveIntoView(props.activeIndex);
  });
};

const publicApi = {
  focusActive,
  setManualScroll: () => {}
};

defineExpose(publicApi);
</script>

<style scoped>
/* @ 文件菜单（DESIGN.md §6.3／§6.4「@ 文件菜单」）：浮层基座 R12、内边距 4；
   选项 32 高、R8、行间 2，无图标，文件名 13px + 右侧路径次文字 12px。
   定位由 InputComposer 按光标计算（上下方向与动画共用 --file-at-offset）。 */
.file-at-menu-wrapper {
  position: fixed;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  min-width: 240px;
  max-width: calc(100vw - 24px);
  max-height: min(320px, 65dvh);
  padding: 4px;
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  background: var(--popover-surface);
  box-shadow: var(--popover-shadow);
  font-family:
    -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  pointer-events: auto;
  overflow: hidden;
}

.file-at-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-at-menu::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.file-at-item {
  width: 100%;
  height: 32px;
  flex: 0 0 32px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: none;
  border-radius: 8px;
  padding: 0 8px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 120ms ease;
}

.file-at-item--active {
  background: var(--hover-bg);
}

.file-at-item__name {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--text-primary);
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-at-item__description {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-at-empty {
  height: 32px;
  flex: 0 0 32px;
  display: flex;
  align-items: center;
  padding: 0 8px;
  color: var(--text-tertiary);
  font-size: 12px;
}

/* 出现 140ms、消失 100ms 精确反向；位移朝向光标（§6.10） */
.file-at-menu-motion-enter-active {
  transition:
    opacity 140ms cubic-bezier(0.2, 0.8, 0.2, 1),
    transform 140ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.file-at-menu-motion-leave-active {
  transition:
    opacity 100ms cubic-bezier(0.4, 0, 1, 1),
    transform 100ms cubic-bezier(0.4, 0, 1, 1);
  pointer-events: none;
}

.file-at-menu-motion-enter-from,
.file-at-menu-motion-leave-to {
  opacity: 0;
  transform: translateY(var(--file-at-offset, 4px)) scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .file-at-menu-motion-enter-active,
  .file-at-menu-motion-leave-active {
    transition: none;
  }
}
</style>

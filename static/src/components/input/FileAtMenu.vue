<template>
  <transition name="file-at-menu-motion">
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
          v-if="hostMode"
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
        <div v-if="!hostMode && !fileItems.length && !loading" class="file-at-empty">
          {{ $t('quickdock.atMenuNoMatch') }}
        </div>
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
  hostMode: boolean;
  menuStyle: Record<string, string>;
}>();

defineEmits<{
  (e: 'select', index: number): void;
  (e: 'hover', index: number): void;
}>();

const fileAtList = ref<HTMLElement | null>(null);

const hasPicker = computed(() => props.hostMode);
const fileItems = computed(() => props.items || []);

const displayIndex = (fileIndex: number) => (hasPicker.value ? fileIndex + 1 : fileIndex);

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
  const rowHeight = 31;
  const targetTop = index * rowHeight;
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
.file-at-menu-wrapper {
  --file-at-row-height: 28px;
  --file-at-gap: 3px;
  --file-at-radius: 9px;
  --file-at-pad-x: 4px;
  --file-at-pad-y: 4px;
  position: fixed;
  z-index: 1000;
  min-width: 240px;
  max-width: min(840px, calc(100vw - 24px));
  max-height: calc(
    ((var(--file-at-row-height) * 7) + (var(--file-at-gap) * 8) + (var(--file-at-pad-y) * 2)) * 1.5
  );
  border: 1px solid var(--border-default);
  border-radius: var(--file-at-radius);
  background: var(--surface-soft);
  box-shadow: 0 8px 24px var(--shadow-color);
  pointer-events: auto;
  overflow: hidden;
}

.file-at-menu {
  height: 100%;
  max-height: inherit;
  overflow-y: auto;
  scrollbar-width: none;
  padding: var(--file-at-pad-y) var(--file-at-pad-x) 6px;
  display: flex;
  flex-direction: column;
  gap: var(--file-at-gap);
}

.file-at-menu::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.file-at-item {
  width: 100%;
  height: var(--file-at-row-height);
  min-height: var(--file-at-row-height);
  flex: 0 0 var(--file-at-row-height);
  display: flex;
  align-items: center;
  gap: 10px;
  border: none;
  border-radius: 7px;
  padding: 3px 9px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}

.file-at-item:first-child {
  border-top-left-radius: calc(var(--file-at-radius) - var(--file-at-gap));
  border-top-right-radius: calc(var(--file-at-radius) - var(--file-at-gap));
}

.file-at-item:hover,
.file-at-item--active {
  background: var(--hover-bg);
  box-shadow: 0 1px 2px var(--shadow-color);
}

.file-at-item__name {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-at-item__description {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-at-empty {
  height: var(--file-at-row-height);
  min-height: var(--file-at-row-height);
  flex: 0 0 var(--file-at-row-height);
  display: flex;
  align-items: center;
  padding: 3px 9px;
  color: var(--text-secondary);
  font-size: 11px;
}

body[data-theme='dark'] .file-at-menu-wrapper {
  background: var(--badge-bg);
  border-color: var(--border-default);
}

.file-at-menu-motion-enter-active,
.file-at-menu-motion-leave-active {
  transition:
    opacity 0.16s ease,
    transform 0.16s ease;
  transform-origin: bottom left;
}

.file-at-menu-motion-enter-from,
.file-at-menu-motion-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.98);
}
</style>

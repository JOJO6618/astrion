<template>
  <div ref="root" class="chat-choice" @keydown.esc="open = false">
    <button
      ref="trigger"
      type="button"
      :disabled="disabled"
      class="chat-choice__trigger"
      :aria-label="label"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="toggle"
    >
      <span>{{ options.find((option) => option.value === modelValue)?.label || label }}</span>
      <svg class="chat-choice__chevron" :class="{ open }" viewBox="0 0 10 10" aria-hidden="true">
        <path d="M2.75 3.75L5 6.25L7.25 3.75" />
      </svg>
    </button>
    <!-- 浮层挂到 body：弹窗/面板常带 max-height + overflow，绝对定位会被裁剪 -->
    <Teleport to="body">
      <div
        v-if="open"
        ref="menu"
        class="chat-choice__options"
        :style="menuStyle"
        role="listbox"
        :aria-label="label"
        @keydown.esc.stop="open = false"
      >
        <button
          v-for="option in options"
          :key="String(option.value)"
          type="button"
          role="option"
          :aria-selected="option.value === modelValue"
          @click="choose(option.value)"
        >
          <span>{{ option.label }}</span>
          <svg
            v-if="option.value === modelValue"
            class="chat-choice__check"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path d="M5 12.5 9.5 17 19 7" />
          </svg>
        </button>
      </div>
    </Teleport>
  </div>
</template>
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
defineProps<{
  modelValue: string | null;
  label: string;
  disabled?: boolean;
  options: Array<{ value: string | null; label: string }>;
}>();
const emit = defineEmits<{ (event: 'update:modelValue', value: string | null): void }>();
const root = ref<HTMLElement>();
const trigger = ref<HTMLElement>();
const menu = ref<HTMLElement>();
const menuStyle = ref<Record<string, string>>({});
const open = ref(false);
const MENU_MAX_HEIGHT = 340;

/** 固定定位跟随触发按钮，并按可用空间翻转/收高，避免超出视口。 */
function updatePosition() {
  const anchor = trigger.value;
  if (!anchor || !open.value || typeof window === 'undefined') return;
  const rect = anchor.getBoundingClientRect();
  const padding = 8;
  const gap = 6;
  const width = Math.min(360, Math.max(190, window.innerWidth - 32));
  const left = Math.max(padding, Math.min(rect.left, window.innerWidth - width - padding));
  const measured = menu.value?.getBoundingClientRect().height;
  const menuHeight = Math.min(
    measured && measured > 0 ? measured : MENU_MAX_HEIGHT,
    MENU_MAX_HEIGHT
  );
  const spaceBelow = window.innerHeight - rect.bottom - padding;
  const spaceAbove = rect.top - padding;
  let top: number;
  let maxHeight: number | undefined;
  if (spaceBelow >= menuHeight + gap) {
    top = rect.bottom + gap;
  } else if (spaceAbove >= menuHeight + gap) {
    top = rect.top - menuHeight - gap;
  } else if (spaceBelow >= spaceAbove) {
    maxHeight = Math.max(120, spaceBelow - gap);
    top = rect.bottom + gap;
  } else {
    maxHeight = Math.max(120, spaceAbove - gap);
    top = Math.max(padding, rect.top - maxHeight - gap);
  }
  menuStyle.value = {
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
    ...(maxHeight !== undefined ? { maxHeight: `${Math.round(maxHeight)}px` } : {})
  };
}

function toggle() {
  open.value = !open.value;
}
function choose(value: string | null) {
  emit('update:modelValue', value);
  open.value = false;
}
function outside(event: Event) {
  if (!open.value) return;
  const target = event.target as Node | null;
  if (!target) return;
  if (root.value?.contains(target) || menu.value?.contains(target)) return;
  open.value = false;
}
watch(open, async (isOpen) => {
  if (!isOpen) {
    menuStyle.value = {};
    return;
  }
  await nextTick();
  updatePosition();
});
onMounted(() => {
  document.addEventListener('pointerdown', outside, true);
  window.addEventListener('resize', updatePosition);
  window.addEventListener('scroll', updatePosition, true);
});
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside, true);
  window.removeEventListener('resize', updatePosition);
  window.removeEventListener('scroll', updatePosition, true);
});
</script>
<style scoped lang="scss">
.chat-choice {
  position: relative;
  min-width: 0;
}
.chat-choice button {
  color: var(--text-primary);
  font: inherit;
  height: 38px;
  cursor: pointer;
  border: 0;
}
.chat-choice__trigger {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 280px;
  background: transparent;
  border-radius: 6px;
  padding: 0 10px;
}
.chat-choice__trigger > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-choice__chevron,
.chat-choice__check {
  display: block;
  flex-shrink: 0;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.chat-choice__chevron {
  width: 12px;
  height: 12px;
  padding: 1px;
  box-sizing: border-box;
  transition: transform 0.15s ease;
}
.chat-choice__chevron.open {
  transform: rotate(180deg);
}
.chat-choice__check {
  width: 16px;
  height: 16px;
}
.chat-choice button:disabled {
  opacity: 0.45;
  cursor: default;
}
.chat-choice__options {
  position: fixed;
  z-index: 3000;
  min-width: 190px;
  max-height: 340px;
  overflow: auto;
  box-sizing: border-box;
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 5px;
  box-shadow: var(--shadow-mid);
}
.chat-choice__options button {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  width: 100%;
  background: transparent;
  border-radius: 5px;
  padding: 0 10px;
}
.chat-choice__options button > span:first-child {
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.chat-choice button:hover {
  background: var(--hover-bg);
}
</style>

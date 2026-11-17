<template>
  <div class="nb-menu" role="menu" :aria-label="$t('browser.more')" @keydown="keys">
    <button
      v-for="item in topItems"
      :key="item.action"
      role="menuitem"
      :disabled="!store.active || !!store.section"
      @click="$emit('action', item.action)"
    >
      <BrowserIcon :name="item.icon" /><span>{{ $t(`browser.${item.label}`) }}</span
      ><kbd>{{ item.key }}</kbd>
    </button>
    <div class="nb-menu-zoom">
      <span>{{ $t('browser.zoom') }}</span
      ><button
        :disabled="!store.active || !!store.section"
        :aria-label="$t('browser.zoomOut')"
        @click="zoom(-10)"
      >
        −</button
      ><output>{{ store.active?.zoom || 100 }}%</output
      ><button
        :disabled="!store.active || !!store.section"
        :aria-label="$t('browser.zoomIn')"
        @click="zoom(10)"
      >
        +</button
      ><button :disabled="!store.active || !!store.section" @click="store.command('zoom', 100)">
        {{ $t('browser.reset') }}
      </button>
    </div>
    <button
      role="menuitem"
      :disabled="!store.active || !!store.section"
      :aria-expanded="devices"
      aria-haspopup="menu"
      @click="devices = !devices"
    >
      <BrowserIcon name="monitor" /><span>{{ $t('browser.device') }}</span
      ><BrowserIcon name="chevronDown" />
    </button>
    <div v-if="devices" class="nb-device-choices">
      <button role="menuitem" @click="$emit('action', 'device-desktop')">
        {{ $t('browser.desktop') }}</button
      ><button role="menuitem" @click="$emit('action', 'device-mobile')">
        {{ $t('browser.mobile') }}
      </button>
    </div>
    <button
      role="menuitem"
      :disabled="!store.active || !!store.section"
      @click="$emit('action', 'capture')"
    >
      <BrowserIcon name="camera" /><span>{{ $t('browser.screenshot') }}</span>
    </button>
    <div class="nb-menu-divider"></div>
    <button
      v-for="item in sections"
      :key="item.action"
      role="menuitem"
      @click="$emit('action', item.action)"
    >
      <BrowserIcon :name="item.icon" /><span>{{ $t(`browser.${item.label}`) }}</span>
    </button>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { isWindowsDesktopShell } from '@/utils/desktopPlatform';
import BrowserIcon from './BrowserIcon.vue';
const store = useNativeBrowserStore();
defineEmits<{ action: [action: string] }>();
const devices = ref(false);
const modifier = isWindowsDesktopShell() ? 'Ctrl+' : '⌘';
const topItems = [
  { action: 'find', label: 'find', icon: 'search', key: `${modifier}F` },
  { action: 'print', label: 'print', icon: 'file', key: `${modifier}P` }
] as const;
const sections = [
  { action: 'import', label: 'import', icon: 'folderOpen' },
  { action: 'passwords', label: 'passwords', icon: 'keyboard' },
  { action: 'history', label: 'history', icon: 'clock' },
  { action: 'clear', label: 'clear', icon: 'trash' },
  { action: 'settings', label: 'settings', icon: 'settings' }
] as const;
function zoom(delta: number) {
  void store.command('zoom', (store.active?.zoom || 100) + delta);
}
function keys(event: KeyboardEvent) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const buttons = [
    ...(event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button')
  ];
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
  buttons[
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
  ]?.focus();
}
onMounted(() => {
  document.querySelector<HTMLButtonElement>('.nb-menu button')?.focus();
});
</script>
<style scoped>
.nb-menu {
  position: absolute;
  top: 88px;
  right: 9px;
  width: 266px;
  max-width: calc(100% - 18px);
  max-height: calc(100% - 100px);
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
  z-index: 12;
  padding: 4px;
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  background: var(--popover-surface);
}
.nb-menu > button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.nb-menu > button:hover,
.nb-menu button:focus-visible {
  background: var(--hover-bg);
  outline: none;
}
.nb-menu > button + button,
.nb-menu > * + .nb-menu-zoom,
.nb-menu > .nb-menu-zoom + button,
.nb-menu > .nb-device-choices + button {
  margin-top: 2px;
}
.nb-menu > button > span:not(.nb-icon) {
  flex: 1;
}
.nb-menu :deep(.nb-icon) {
  width: 16px;
  height: 16px;
  flex: none;
  color: var(--text-secondary);
}
.nb-menu > button:hover :deep(.nb-icon),
.nb-menu > button:focus-visible :deep(.nb-icon) {
  color: var(--text-primary);
}
.nb-menu kbd {
  font: inherit;
  font-size: 12px;
  color: var(--text-tertiary);
}
.nb-menu-divider {
  height: 1px;
  background: var(--popover-border);
  margin: 2px 8px;
}
/* 缩放行与设备选项没有图标，文字缩进到选项文字线：内边距 8 + 图标 16 + 间距 10 = 34 */
.nb-menu-zoom {
  height: 32px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 2px 0 34px;
  font-size: 13px;
}
.nb-menu-zoom > span {
  flex: 1;
}
.nb-menu-zoom button {
  border: 0;
  background: transparent;
  color: var(--text-primary);
  height: 28px;
  min-width: 28px;
  border-radius: 8px;
  cursor: pointer;
}
.nb-menu-zoom button:hover {
  background: var(--hover-bg);
}
.nb-menu-zoom output {
  min-width: 40px;
  text-align: center;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.nb-device-choices {
  display: flex;
  padding: 2px 4px 2px 34px;
  gap: 4px;
}
.nb-device-choices button {
  height: 32px;
  flex: 1;
  background: var(--surface-soft);
  color: var(--text-primary);
  border: 0;
  border-radius: 8px;
  font-size: 12px;
}
.nb-device-choices button:hover {
  background: var(--surface-muted);
}
.nb-menu {
  transform-origin: top right;
}
</style>

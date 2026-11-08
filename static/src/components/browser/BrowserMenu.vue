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
import BrowserIcon from './BrowserIcon.vue';
const store = useNativeBrowserStore();
defineEmits<{ action: [action: string] }>();
const devices = ref(false);
const topItems = [
  { action: 'find', label: 'find', icon: 'search', key: '⌘F' },
  { action: 'print', label: 'print', icon: 'file', key: '⌘P' }
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
  padding: 6px;
  border: 1px solid var(--border-default);
  border-radius: 9px;
  background: var(--surface-base);
}
.nb-menu > button {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  height: 35px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  font-size: 12px;
  cursor: pointer;
}
.nb-menu > button:hover,
.nb-menu button:focus-visible {
  background: var(--surface-muted);
  outline: none;
}
.nb-menu > button > span:not(.nb-icon) {
  flex: 1;
}
.nb-menu :deep(.nb-icon) {
  width: 15px;
  height: 15px;
  color: var(--text-secondary);
}
.nb-menu kbd {
  font: inherit;
  font-size: 10px;
  color: var(--text-tertiary);
}
.nb-menu-divider {
  height: 1px;
  background: var(--border-default);
  margin: 5px 0;
}
.nb-menu-zoom {
  height: 39px;
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 0 7px 0 36px;
  font-size: 12px;
}
.nb-menu-zoom > span {
  flex: 1;
}
.nb-menu-zoom button {
  border: 0;
  background: transparent;
  color: var(--text-primary);
  height: 27px;
  min-width: 25px;
  border-radius: 4px;
  cursor: pointer;
}
.nb-menu-zoom button:hover {
  background: var(--surface-muted);
}
.nb-menu-zoom output {
  min-width: 38px;
  text-align: center;
  font-size: 11px;
}
.nb-device-choices {
  display: flex;
  padding: 3px 10px 5px 36px;
  gap: 5px;
}
.nb-device-choices button {
  height: 28px;
  flex: 1;
  background: var(--surface-soft);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  font-size: 11px;
}
</style>

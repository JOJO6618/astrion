<template>
  <ConversationTabStrip />
  <DesktopSidebar v-if="sidebarAvailable" />
</template>

<script setup lang="ts">
import { defineAsyncComponent, onBeforeUnmount, onMounted } from 'vue';
import ConversationTabStrip from '@/components/chrome/ConversationTabStrip.vue';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import { desktopSidebarBridge } from './desktopSidebarBridge';

const DesktopSidebar = defineAsyncComponent(() => import('./DesktopSidebar.vue'));
const sidebarAvailable = Boolean(desktopSidebarBridge()?.setSidebarWidth);
const tabsStore = useConversationTabsStore();
let themeTimer = 0;
let removeShellRequests = () => {};

function syncTheme() {
  try {
    const theme = window.localStorage.getItem('agents_ui_theme');
    if (theme === 'classic' || theme === 'light' || theme === 'dark') {
      document.documentElement.setAttribute('data-theme', theme);
      document.body?.setAttribute('data-theme', theme);
    }
  } catch {
    // Preserve the current theme when storage is unavailable.
  }
}

onMounted(() => {
  tabsStore.startPolling();
  removeShellRequests = tabsStore.installShellRequests();
  syncTheme();
  themeTimer = window.setInterval(syncTheme, 2000);
  window.addEventListener('storage', syncTheme);
});
onBeforeUnmount(() => {
  tabsStore.stopPolling();
  removeShellRequests();
  if (themeTimer) window.clearInterval(themeTimer);
  window.removeEventListener('storage', syncTheme);
});
</script>

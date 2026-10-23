<template>
  <ConversationTabStrip />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';
import ConversationTabStrip from '@/components/chrome/ConversationTabStrip.vue';
import { useConversationTabsStore } from '@/stores/conversationTabs';

const tabsStore = useConversationTabsStore();
let themeTimer = 0;

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
  syncTheme();
  themeTimer = window.setInterval(syncTheme, 2000);
  window.addEventListener('storage', syncTheme);
});
onBeforeUnmount(() => {
  tabsStore.stopPolling();
  if (themeTimer) window.clearInterval(themeTimer);
  window.removeEventListener('storage', syncTheme);
});
</script>

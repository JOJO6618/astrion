<template><BrowserPanel ref="panel" /></template>
<script setup lang="ts">
// 对话浏览器独立窗口的根组件：常开 BrowserPanel，连接主进程为本窗口固定的对话，
// 并把 document.title 设为对话标题（主进程据此设置窗口标题）。
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { browserBridge, type BrowserScope } from './types';
import BrowserPanel from './BrowserPanel.vue';

const TITLE_POLL_MS = 3000;
const THEME_POLL_MS = 2000;
const store = useNativeBrowserStore();
const panel = ref<InstanceType<typeof BrowserPanel> | null>(null);
const params = new URLSearchParams(window.location.search);
// 仅用于查找标题；主进程忽略 renderer 传入的 scope。
const scope: BrowserScope = {
  mode: params.get('mode') === 'chat' ? 'chat' : 'agent',
  workspace: params.get('workspace') || '',
  conversation: params.get('conversation') || ''
};
let conversationTitle = '';
let titleTimer = 0;
let themeTimer = 0;
const subscriptions: Array<() => void> = [];

/** 对话标题来自桌面对话标签（/new 阶段 conversation 即稳定标签 key）；查不到时保留上次标题。 */
async function refreshTitle() {
  try {
    const response = await fetch('/api/conversation-tabs', {
      credentials: 'same-origin',
      cache: 'no-store'
    });
    const data = await response.json();
    const tabs: Array<{ key?: string; conversation_id?: string; title?: string }> = Array.isArray(
      data?.tabs
    )
      ? data.tabs
      : [];
    const tab = tabs.find(
      (row) => row.conversation_id === scope.conversation || row.key === scope.conversation
    );
    if (tab?.title) conversationTitle = String(tab.title);
  } catch {
    // Keep the last known title while the backend is unavailable.
  }
  document.title = conversationTitle || t('browser.title');
}

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

store.open = true;
onMounted(() => {
  const bridge = browserBridge();
  if (bridge) {
    subscriptions.push(bridge.onState((snapshot) => store.apply(snapshot)));
    subscriptions.push(
      bridge.onShortcut((event) => {
        if (event.lease === store.snapshot?.lease) panel.value?.shortcut(event);
      })
    );
    subscriptions.push(
      bridge.onContextMenu((event) => {
        if (event.lease === store.snapshot?.lease) panel.value?.pageContext(event);
      })
    );
    subscriptions.push(
      bridge.onMessage((event) => {
        if (event.lease === store.snapshot?.lease) store.error = event.message || '';
      })
    );
    if (bridge.onWindowState)
      subscriptions.push(
        bridge.onWindowState((state) =>
          document.documentElement.classList.toggle('nb-window-fullscreen', state.fullScreen)
        )
      );
    void store.connect(scope);
  }
  document.title = t('browser.title');
  void refreshTitle();
  titleTimer = window.setInterval(() => void refreshTitle(), TITLE_POLL_MS);
  syncTheme();
  themeTimer = window.setInterval(syncTheme, THEME_POLL_MS);
  window.addEventListener('storage', syncTheme);
});
onBeforeUnmount(() => {
  store.suspend();
  subscriptions.forEach((unsubscribe) => unsubscribe());
  window.clearInterval(titleTimer);
  window.clearInterval(themeTimer);
  window.removeEventListener('storage', syncTheme);
});
</script>

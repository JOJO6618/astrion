<template>
  <ConversationTabStrip />
</template>

<script setup lang="ts">
// 桌面壳 chrome webview 根组件（顶部对话标签条）。
// 职责：镜像模式初始化 + 周期轮询服务端标签快照收敛 + 跨 webview 主题同步。
// 标签数据单一写者是主页面；本页操作全部乐观镜像 + dispatch（见组件内注释）。
import { onBeforeUnmount, onMounted } from 'vue';
import ConversationTabStrip from '@/components/chrome/ConversationTabStrip.vue';
import { useConversationTabsStore } from '@/stores/conversationTabs';

// 标签操作乐观镜像即时生效，快照轮询负责收敛标题/运行点等非操作态变化，
// 600ms 让「侧边栏点开对话 → 标签出现」的感知延迟压到一秒以内
const SNAPSHOT_POLL_MS = 600;
// 主题在主页面的个人空间里修改（写 localStorage），chrome 是另一个 webview，
// storage 事件不跨 webview，用低频轮询兜底（主题变化极低频）。
const THEME_POLL_MS = 2000;

const tabsStore = useConversationTabsStore();
tabsStore.mirrorMode = true;

let snapshotTimer = 0;
let themeTimer = 0;

async function pollSnapshot() {
  try {
    const resp = await fetch('/api/conversation-tabs', { credentials: 'same-origin' });
    const payload = await resp.json().catch(() => null);
    if (payload?.success) {
      tabsStore.applyServerSnapshot(payload.tabs, payload.active_key);
    }
  } catch {
    // 拉取失败保持当前镜像，下轮再试
  }
}

function syncTheme() {
  try {
    const theme = window.localStorage.getItem('agents_ui_theme');
    if (theme === 'classic' || theme === 'light' || theme === 'dark') {
      document.documentElement.setAttribute('data-theme', theme);
      document.body?.setAttribute('data-theme', theme);
    }
  } catch {
    // localStorage 不可用时保持当前主题
  }
}

onMounted(async () => {
  await tabsStore.hydrate();
  await pollSnapshot();
  snapshotTimer = window.setInterval(pollSnapshot, SNAPSHOT_POLL_MS);
  themeTimer = window.setInterval(syncTheme, THEME_POLL_MS);
  window.addEventListener('storage', syncTheme);
});

onBeforeUnmount(() => {
  if (snapshotTimer) window.clearInterval(snapshotTimer);
  if (themeTimer) window.clearInterval(themeTimer);
  window.removeEventListener('storage', syncTheme);
});
</script>

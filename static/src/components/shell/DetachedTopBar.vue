<template>
  <header
    v-if="tabs.detached"
    class="detached-top-bar"
    :class="{ 'is-mac': isMac, 'is-windows': isWindows }"
  >
    <span class="detached-top-bar__title" :title="title">{{ title }}</span>
  </header>
</template>

<script setup lang="ts">
// 独立对话窗口顶栏（设计 §5.4）：对话标题 + 窗口拖动区，按平台为红绿灯/窗口控制让位。
// 本视图固定绑定一个对话；侧边栏、新建/切换对话入口由布局样式隐藏。
import { computed, onBeforeUnmount, watch } from 'vue';
import { t } from '@/locales';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import { isMacDesktopShell, isWindowsDesktopShell } from '@/utils/desktopPlatform';

const tabs = useConversationTabsStore();
const isMac = isMacDesktopShell();
const isWindows = isWindowsDesktopShell();
// 空白标签还没有标题：与标签栏一致显示「新对话」，不落到「未命名对话」。
const title = computed(() =>
  tabs.viewTab?.kind === 'new'
    ? t('appUi.tabStripNewTab')
    : tabs.viewTab?.title || t('overlay.unnamedConversation')
);

// 布局由根节点类驱动：就地切换，不重载页面。
watch(
  () => tabs.detached,
  (on) => document.documentElement.classList.toggle('desktop-detached', on),
  { immediate: true }
);
onBeforeUnmount(() => document.documentElement.classList.remove('desktop-detached'));
</script>

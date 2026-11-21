<template>
  <header
    v-if="tabs.detached"
    class="detached-top-bar"
    :class="{ 'is-mac': isMac, 'is-windows': isWindows }"
  >
    <span class="detached-top-bar__title" :title="title">{{ title }}</span>
    <!-- 浏览器开关：独立窗口隐藏了侧边栏，浏览器的唯一入口挪到顶栏。
         mac 靠右（margin-left:auto），Windows 紧跟标题右侧；控件区退出窗口拖动。 -->
    <button
      v-if="browserAvailable"
      type="button"
      class="detached-top-bar__browser"
      :class="{ 'is-open': browserOpen }"
      :aria-label="$t('browser.title')"
      :aria-pressed="browserOpen"
      @click="toggleBrowser"
    >
      <span class="icon" aria-hidden="true" :style="iconSrcStyle('globe')"></span>
    </button>
  </header>
</template>

<script setup lang="ts">
// 独立对话窗口顶栏（设计 §5.4）：对话标题 + 浏览器开关 + 窗口拖动区，
// 按平台为红绿灯/窗口控制让位。本视图固定绑定一个对话；
// 侧边栏、新建/切换对话入口由布局样式隐藏。
import { computed, onBeforeUnmount, watch } from 'vue';
import { t } from '@/locales';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import { useNativeBrowserStore, nativeBrowserAvailable } from '@/stores/nativeBrowser';
import { iconSrcStyle } from '@/utils/icons';
import { isMacDesktopShell, isWindowsDesktopShell } from '@/utils/desktopPlatform';

// 内容视图壳层桥只用到浏览器开关；参数由壳层按可信 sender 定位本视图的对话。
interface DetachedShellBridge {
  toggleBrowserPanel?: () => void;
}
function detachedShellBridge(): DetachedShellBridge | undefined {
  return (window as Window & { astrionShell?: DetachedShellBridge }).astrionShell;
}

const tabs = useConversationTabsStore();
const shell = detachedShellBridge();
const browser = useNativeBrowserStore();
const isMac = isMacDesktopShell();
const isWindows = isWindowsDesktopShell();
// 空白标签还没有标题：与标签栏一致显示「新对话」，不落到「未命名对话」。
const title = computed(() =>
  tabs.viewTab?.kind === 'new'
    ? t('appUi.tabStripNewTab')
    : tabs.viewTab?.title || t('overlay.unnamedConversation')
);
// 与侧边栏一致：浏览器面板只服务对话/空白标签，独立页面标签不提供开关。
// 壳层没有内容视图开关通道时（旧壳）不渲染，避免出现点了没反应的按钮。
const browserAvailable = computed(
  () =>
    Boolean(shell?.toggleBrowserPanel) &&
    nativeBrowserAvailable() &&
    (tabs.viewTab?.kind === 'conv' || tabs.viewTab?.kind === 'new')
);
// 内嵌面板开合状态沿用既有桥：NativeBrowserHost 订阅壳层状态并写回 store。
const browserOpen = computed(() => browser.open);

function toggleBrowser() {
  if (!browserAvailable.value) return;
  shell?.toggleBrowserPanel?.();
}

// 布局由根节点类驱动：就地切换，不重载页面。
watch(
  () => tabs.detached,
  (on) => document.documentElement.classList.toggle('desktop-detached', on),
  { immediate: true }
);
onBeforeUnmount(() => document.documentElement.classList.remove('desktop-detached'));
</script>

<template>
  <header
    class="chrome-tab-strip"
    :class="{ 'is-windows': isWindows, 'sidebar-fuse': sidebarOpen }"
    @mousedown="onBarMouseDown"
    @dblclick="onBarDoubleClick"
  >
    <button
      v-if="isWindows"
      class="chrome-icon settings-btn"
      :aria-label="t('appUi.tabStripSettingsHint')"
      @click="onOpenSettings"
    >
      <span class="settings-icon" :style="settingsIconStyle" aria-hidden="true"></span>
    </button>
    <button class="chrome-icon new-tab-btn" :aria-label="newTabHint" @click="onNewTab">
      <svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10" /></svg>
    </button>
    <button
      v-if="tabsStore.commandError || tabsStore.syncError"
      class="chrome-sync-error"
      :aria-label="tabsStore.commandError || tabsStore.syncError"
      @click="retrySync"
    >
      {{ t('appUi.tabStripSyncFailed') }}
    </button>
    <div class="tab-strip">
      <div ref="tabsEl" class="tabs" role="tablist">
        <div
          v-for="tab in renderedTabs"
          :key="tab.key"
          class="tab"
          :class="{
            active: tab.key === tabsStore.activeKey,
            'has-status': Boolean(statusOf(tab)),
            closing: closingKeys.has(tab.key)
          }"
          :data-key="tab.key"
          role="tab"
          tabindex="0"
          :aria-selected="tab.key === tabsStore.activeKey"
          :aria-busy="tab.key === tabsStore.requestedKey"
          :aria-label="
            tab.error ? `${titleOf(tab)}: ${t('appUi.tabStripLoadFailed')}` : titleOf(tab)
          "
          @keydown.enter.prevent="onTabClick(tab)"
          @keydown.space.prevent="onTabClick(tab)"
          @click="onTabClick(tab)"
          @auxclick="onTabAuxClick(tab, $event)"
        >
          <i class="foot foot-l"></i><i class="foot foot-r"></i>
          <span v-if="statusOf(tab)" class="tab-status" :class="statusOf(tab)"></span>
          <span class="tab-title">{{ titleOf(tab) }}</span>
          <button
            v-if="canClose(tab)"
            class="tab-close"
            :aria-label="closeHint"
            @keydown.stop
            @click.stop="requestClose(tab)"
          >
            <svg viewBox="0 0 10 10"><path d="M1.5 1.5l7 7M8.5 1.5l-7 7" /></svg>
          </button>
        </div>
      </div>
    </div>
    <div v-if="isWindows" class="window-controls">
      <button
        class="wc-btn"
        :aria-label="t('appUi.tabStripWinMinimize')"
        @click="onWindowControl('minimize')"
      >
        <svg viewBox="0 0 12 12"><path d="M2 6h8" /></svg>
      </button>
      <button
        class="wc-btn"
        :aria-label="maximized ? t('appUi.tabStripWinRestore') : t('appUi.tabStripWinMaximize')"
        @click="onWindowControl('maximize-toggle')"
      >
        <svg v-if="!maximized" viewBox="0 0 12 12">
          <rect x="2.5" y="2.5" width="7" height="7" rx="1" />
        </svg>
        <svg v-else viewBox="0 0 12 12">
          <path
            d="M4 3.2V2.8A1.3 1.3 0 0 1 5.3 1.5h4.2a1.3 1.3 0 0 1 1.3 1.3v4.2a1.3 1.3 0 0 1-1.3 1.3h-.4"
          />
          <rect x="1.5" y="4" width="6.5" height="6.5" rx="1" />
        </svg>
      </button>
      <button
        class="wc-btn wc-close"
        :aria-label="t('appUi.tabStripWinClose')"
        @click="onWindowControl('close')"
      >
        <svg viewBox="0 0 12 12"><path d="M2.8 2.8l6.4 6.4M9.2 2.8L2.8 9.2" /></svg>
      </button>
    </div>
  </header>
</template>

<script setup lang="ts">
// Independent chrome view. Server commands own structure; animations only project presentation.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { t } from '@/locales';
import { ICONS } from '@/utils/icons';
import { isWindowsDesktopShell } from '@/utils/desktopPlatform';
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/stores/ui';
import { useConversationTabsStore, type ConversationTab } from '@/stores/conversationTabs';

const tabsStore = useConversationTabsStore();
const { activeKey } = storeToRefs(tabsStore);
const settingsIconStyle = { '--icon-src': `url(${ICONS.settings})` };
const isWindows = isWindowsDesktopShell();
const modKey = isWindows ? 'Ctrl' : '⌘';
const newTabHint = computed(() => t('appUi.tabStripNewTabHint', { mod: modKey }));
const closeHint = computed(() => t('appUi.tabStripCloseHint', { mod: modKey }));
const sidebarOpen = ref(false);
let sidebarPollTimer = 0;
function syncSidebarState() {
  try {
    sidebarOpen.value = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '0';
  } catch {
    /* Preserve the current sidebar projection. */
  }
}

const tabsEl = ref<HTMLElement | null>(null);
const closingTabs = ref(new Map<string, { tab: ConversationTab; index: number }>());
const closingKeys = computed(() => new Set(closingTabs.value.keys()));
const closeTimers = new Set<number>();
let disposed = false;
const renderedTabs = computed(() => {
  const rows = [...tabsStore.tabs];
  for (const [key, value] of closingTabs.value) {
    if (!rows.some((tab) => tab.key === key))
      rows.splice(Math.min(value.index, rows.length), 0, value.tab);
  }
  return rows;
});
const donePending = ref(new Set<string>());
const prevRunning = new Map<string, boolean>();
function statusOf(tab: ConversationTab): 'running' | 'done' | 'loading' | 'error' | null {
  if (tab.key === tabsStore.requestedKey) return 'loading';
  if (tab.error) return 'error';
  if (tab.running) return 'running';
  return tab.key !== tabsStore.activeKey && donePending.value.has(tab.key) ? 'done' : null;
}
watch(
  () => tabsStore.tabs.map((tab) => `${tab.key}:${tab.running ? 1 : 0}`).join('|'),
  () => {
    const keys = new Set(tabsStore.tabs.map((tab) => tab.key));
    const next = new Set([...donePending.value].filter((key) => keys.has(key)));
    for (const key of prevRunning.keys()) if (!keys.has(key)) prevRunning.delete(key);
    for (const tab of tabsStore.tabs) {
      if (prevRunning.get(tab.key) === true && !tab.running && tab.key !== tabsStore.activeKey)
        next.add(tab.key);
      prevRunning.set(tab.key, tab.running);
    }
    donePending.value = next;
  }
);
watch(activeKey, (key) => {
  if (key) {
    donePending.value.delete(key);
    donePending.value = new Set(donePending.value);
    scrollTabIntoView(key);
  }
});
watch(
  () => tabsStore.requestedKey,
  (key) => {
    if (key) scrollTabIntoView(key);
  }
);
function titleOf(tab: ConversationTab): string {
  return tab.kind === 'new' ? t('appUi.tabStripNewTab') : tab.title || t('appUi.tabStripNewTab');
}
function retrySync() {
  tabsStore.commandError = '';
  void tabsStore.refreshSnapshot().catch(() => {});
}
function onOpenSettings() {
  void tabsStore.openPage('/settings').catch(() => {});
}
function onTabClick(tab: ConversationTab) {
  if (closingKeys.value.has(tab.key)) return;
  if (tab.key === tabsStore.activeKey && !tab.error && !tabsStore.requestedKey) return;
  void tabsStore.setActive(tab.key).catch(() => {});
  scrollTabIntoView(tab.key);
}
function onTabAuxClick(tab: ConversationTab, event: MouseEvent) {
  if (event.button === 1) {
    event.preventDefault();
    requestClose(tab);
  }
}
function onNewTab() {
  void tabsStore.addNewTab().catch(() => {});
}
function canClose(tab: ConversationTab): boolean {
  return !(tabsStore.tabs.length === 1 && tab.kind === 'new' && !tab.error);
}
function requestClose(tab: ConversationTab) {
  if (closingKeys.value.has(tab.key) || !canClose(tab)) return;
  if (tabsStore.tabs.length === 1) {
    void tabsStore.closeTab(tab.key).catch(() => {});
    return;
  }
  closingTabs.value = new Map(closingTabs.value).set(tab.key, {
    tab: { ...tab },
    index: tabsStore.tabs.findIndex((item) => item.key === tab.key)
  });
  const animation = new Promise<void>((resolve) => {
    const timer = window.setTimeout(() => {
      closeTimers.delete(timer);
      resolve();
    }, 230);
    closeTimers.add(timer);
  });
  // Start the request immediately, before waiting for the presentation animation.
  void Promise.all([tabsStore.closeTab(tab.key), animation])
    .then(() => {
      if (disposed) return;
      const siblings = Array.from(
        tabsEl.value?.querySelectorAll<HTMLElement>('.tab:not(.closing)') || []
      );
      const beforeX = new Map(
        siblings.map((node) => [node.dataset.key, node.getBoundingClientRect().left])
      );
      closingTabs.value.delete(tab.key);
      closingTabs.value = new Map(closingTabs.value);
      nextTick(() => {
        if (disposed) return;
        for (const node of siblings) {
          const dx = (beforeX.get(node.dataset.key) || 0) - node.getBoundingClientRect().left;
          if (dx)
            node.animate([{ transform: `translateX(${dx}px)` }, { transform: 'translateX(0)' }], {
              duration: 280,
              easing: 'cubic-bezier(.3,.9,.35,1)'
            });
        }
        forceHoverReeval();
      });
    })
    .catch(() => {
      if (disposed) return;
      closingTabs.value.delete(tab.key);
      closingTabs.value = new Map(closingTabs.value);
    });
}
function forceHoverReeval() {
  const el = tabsEl.value;
  if (!el) return;
  el.style.pointerEvents = 'none';
  void el.offsetHeight;
  el.style.pointerEvents = '';
}
function scrollTabIntoView(key: string) {
  nextTick(() => {
    if (disposed) return;
    const el = Array.from(tabsEl.value?.querySelectorAll<HTMLElement>('.tab') || []).find(
      (node) => node.dataset.key === key
    );
    el?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  });
}

// Keep native window controls independent of conversation navigation.
let dragArm: { x: number; y: number } | null = null;
let dragFired = false;
function startWindowDrag() {
  void fetch('/api/desktop/window/drag', { method: 'POST', credentials: 'same-origin' }).catch(
    () => {}
  );
}
function cleanupDragArm() {
  dragArm = null;
  dragFired = false;
  window.removeEventListener('mousemove', onDragMouseMove);
  window.removeEventListener('mouseup', onDragMouseUp);
}
function onDragMouseMove(event: MouseEvent) {
  if (!dragArm || dragFired) return;
  if (Math.abs(event.clientX - dragArm.x) + Math.abs(event.clientY - dragArm.y) < 4) return;
  dragFired = true;
  startWindowDrag();
  cleanupDragArm();
}
function onDragMouseUp() {
  cleanupDragArm();
}
function onBarMouseDown(event: MouseEvent) {
  if (event.button !== 0) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('.tab, .chrome-icon, .chrome-sync-error, .window-controls')) return;
  if (isWindows) {
    dragArm = { x: event.clientX, y: event.clientY };
    dragFired = false;
    window.addEventListener('mousemove', onDragMouseMove);
    window.addEventListener('mouseup', onDragMouseUp);
  } else startWindowDrag();
}
function onBarDoubleClick(event: MouseEvent) {
  if (!isWindows) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('.tab, .chrome-icon, .chrome-sync-error, .window-controls')) return;
  onWindowControl('maximize-toggle');
}
const maximized = ref(false);
let windowStateTimer = 0;
async function syncWindowState() {
  try {
    const resp = await fetch('/api/desktop/window/state', { credentials: 'same-origin' });
    const payload = await resp.json().catch(() => null);
    if (!disposed && payload?.success && payload.data)
      maximized.value = Boolean(payload.data.maximized);
  } catch {
    /* Keep the last known window state. */
  }
}
function onWindowControl(action: string) {
  if (action === 'maximize-toggle') maximized.value = !maximized.value;
  void fetch('/api/desktop/window/control', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action })
  }).catch(() => {});
}
function onKeydown(event: KeyboardEvent) {
  if (!(event.metaKey || event.ctrlKey)) return;
  const key = event.key.toLowerCase();
  if (key === 't') {
    event.preventDefault();
    onNewTab();
  } else if (key === 'w') {
    event.preventDefault();
    const target = tabsStore.requestedKey || tabsStore.activeKey;
    const tab = tabsStore.tabs.find((item) => item.key === target);
    if (tab && canClose(tab)) requestClose(tab);
  }
}
onMounted(() => {
  window.addEventListener('keydown', onKeydown);
  if (isWindows) {
    void syncWindowState();
    windowStateTimer = window.setInterval(syncWindowState, 1000);
  }
  syncSidebarState();
  sidebarPollTimer = window.setInterval(syncSidebarState, 150);
});
onBeforeUnmount(() => {
  disposed = true;
  closeTimers.forEach((timer) => window.clearTimeout(timer));
  closeTimers.clear();
  window.removeEventListener('keydown', onKeydown);
  if (windowStateTimer) window.clearInterval(windowStateTimer);
  if (sidebarPollTimer) window.clearInterval(sidebarPollTimer);
  cleanupDragArm();
});
</script>

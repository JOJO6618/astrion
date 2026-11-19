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
            closing: closingKeys.has(tab.key),
            dragging: drag.key === tab.key && drag.active,
            torn: drag.key === tab.key && drag.tear,
            shifting: drag.active && drag.key !== tab.key
          }"
          :style="tabStyle(tab.key)"
          :data-key="tab.key"
          role="tab"
          tabindex="0"
          :aria-selected="tab.key === tabsStore.activeKey"
          :aria-label="accessibleTabTitle(tab)"
          @keydown.enter.prevent="onTabClick(tab)"
          @keydown.space.prevent="onTabClick(tab)"
          @click="onTabClick(tab)"
          @pointerdown="onTabPointerDown(tab, $event)"
          @auxclick="onTabAuxClick(tab, $event)"
        >
          <i class="foot foot-l"></i><i class="foot foot-r"></i>
          <span
            v-if="tab.kind === 'page'"
            class="tab-icon"
            :style="pageIconStyle(tab)"
            aria-hidden="true"
          ></span>
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
import { iconSrcStyle, type IconKey } from '@/utils/icons';
import { isWindowsDesktopShell } from '@/utils/desktopPlatform';
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/stores/ui';
import { useConversationTabsStore, type ConversationTab } from '@/stores/conversationTabs';
import {
  dragStep,
  planReorder,
  startDrag,
  topLimitFor,
  verticalOutside,
  type DragInput,
  type DragState,
  type TabRect
} from './tabReorder';

const tabsStore = useConversationTabsStore();
const { activeKey } = storeToRefs(tabsStore);
const settingsIconStyle = iconSrcStyle('settings');
const PAGE_ICONS: Record<string, IconKey> = {
  settings: 'settings',
  workflows: 'workflow',
  git: 'gitBranch',
  schedules: 'timer'
};
function pageIconStyle(tab: ConversationTab) {
  return iconSrcStyle(PAGE_ICONS[tab.pageType as string] || 'settings');
}
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
  const rows = [...tabsStore.mainTabs];
  if (pendingOrder.value) {
    const order = pendingOrder.value;
    const rank = (key: string) => (order.includes(key) ? order.indexOf(key) : order.length);
    rows.sort((a, b) => rank(a.key) - rank(b.key));
  }
  for (const [key, value] of closingTabs.value) {
    if (!rows.some((tab) => tab.key === key))
      rows.splice(Math.min(value.index, rows.length), 0, value.tab);
  }
  return rows;
});
// 拖拽排序：松手后乐观保持新顺序，直到 reorder 命令的快照回来。
const pendingOrder = ref<string[] | null>(null);
const donePending = ref(new Set<string>());
const prevRunning = new Map<string, boolean>();
// 切换过程中不渲染任何临时标记：状态点参与 flex 排版，临时点会把标题推右再弹回
// （0.6.4 的 loading 环即如此）。切换进度由标签的 aria-busy 表达，见模板。
function statusOf(tab: ConversationTab): 'awaiting' | 'running' | 'done' | 'error' | null {
  if (tab.awaitingHumanApproval || tab.awaitingUserAnswer) return 'awaiting';
  if (tab.error) return 'error';
  if (tab.running) return 'running';
  return tab.key !== tabsStore.activeKey && donePending.value.has(tab.key) ? 'done' : null;
}
watch(
  () => tabsStore.mainTabs.map((tab) => `${tab.key}:${tab.running ? 1 : 0}`).join('|'),
  () => {
    const keys = new Set(tabsStore.mainTabs.map((tab) => tab.key));
    const next = new Set([...donePending.value].filter((key) => keys.has(key)));
    for (const key of prevRunning.keys()) if (!keys.has(key)) prevRunning.delete(key);
    for (const tab of tabsStore.mainTabs) {
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
function titleOf(tab: ConversationTab): string {
  if (tab.kind === 'page') {
    if (tab.pageType === 'workflows') return t('appUi.tabStripPageWorkflows');
    if (tab.pageType === 'schedules') return t('appUi.tabStripPageSchedules');
    if (tab.pageType === 'git')
      return tab.workspaceLabel
        ? t('appUi.tabStripPageGitWorkspace', { workspace: tab.workspaceLabel })
        : t('appUi.tabStripPageGit');
    return t('appUi.tabStripPageSettings');
  }
  return tab.kind === 'new' ? t('appUi.tabStripNewTab') : tab.title || t('appUi.tabStripNewTab');
}
function accessibleTabTitle(tab: ConversationTab): string {
  const states = [];
  if (tab.awaitingHumanApproval) states.push(t('appUi.tabStripAwaitingApproval'));
  if (tab.awaitingUserAnswer) states.push(t('appUi.tabStripAwaitingAnswer'));
  if (!states.length && tab.error) states.push(t('appUi.tabStripLoadFailed'));
  return [titleOf(tab), ...states].join(' · ');
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
  if (suppressClick) return;
  if (tab.key === tabsStore.activeKey && !tab.error) return;
  // 先让壳层乐观显示已加载的视图，再发 activate（以后端快照为准）。
  void tabsStore.showTab(tab.key).catch(() => {});
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
  return !(tabsStore.mainTabs.length === 1 && tab.kind === 'new' && !tab.error);
}
function requestClose(tab: ConversationTab) {
  if (closingKeys.value.has(tab.key) || !canClose(tab)) return;
  if (tabsStore.mainTabs.length === 1) {
    void tabsStore.closeTab(tab.key).catch(() => {});
    return;
  }
  closingTabs.value = new Map(closingTabs.value).set(tab.key, {
    tab: { ...tab },
    index: tabsStore.mainTabs.findIndex((item) => item.key === tab.key)
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

// ─── 拖拽排序与拖出（pointer 事件，状态机见 tabReorder.ts dragStep）：
// 超过阈值后进入拖拽并实时让位；指针离开标签条垂直方向超过 TEAR_OFF_PX 的瞬间（对话标签与
// 空白新对话标签）通知壳层立即建独立窗口并跟随指针（tear/move），松手 drop；指针回到标签条带内则
// cancel 回到排序态。页面标签只能排序 ───
const drag = ref<{
  key: string;
  active: boolean;
  tear: boolean;
  dx: number;
  shifts: Record<string, number>;
}>({ key: '', active: false, tear: false, dx: 0, shifts: {} });
const idleDrag = () => ({ key: '', active: false, tear: false, dx: 0, shifts: {} });
let dragState: DragState | null = null;
let dragRects: TabRect[] = [];
let dragStartX = 0;
let dragGrabX = 0;
let dragPointerId = -1;
let suppressClick = false;
let moveFrame = 0;
let pendingMove: PointerEvent | null = null;
function tabStyle(key: string) {
  if (!drag.value.active) return undefined;
  if (key === drag.value.key && !drag.value.tear)
    return { transform: `translateX(${drag.value.dx}px)`, zIndex: 3, transition: 'none' };
  const shift = drag.value.shifts[key];
  return shift ? { transform: `translateX(${shift}px)` } : undefined;
}
function sendShell(message: Record<string, unknown>) {
  try {
    (window as any).astrionShell?.dragTab?.({ key: dragState?.key, ...message });
  } catch {
    // 壳层不接受拖出时仍按排序处理。
  }
}
// 独立窗口顶部栏高度的一半：指针落在顶部栏中线；水平偏移让被抓标题落在窗口标题附近。
const TEAR_OFFSET_Y = 19;
const TEAR_OFFSET_X_BASE = 84;
function applyDragInput(input: DragInput) {
  if (!dragState) return;
  const wasActive = dragState.mode !== 'pressed';
  const result = dragStep(dragState, input);
  dragState = result.state;
  for (const message of result.shell) sendShell(message);
  if (input.type === 'move') {
    const torn = result.state.mode === 'torn';
    const plan = planReorder(dragRects, result.state.key, input.dx);
    // 拖出态：被拖标签从条中消失、其余标签合拢（不再让位预览）。
    drag.value = {
      key: result.state.key,
      active: true,
      tear: torn,
      dx: input.dx,
      shifts: torn ? {} : plan.shifts
    };
  }
  if (result.finished) {
    // 真正拖动过才吞掉随后的 click，普通点击不受影响。
    if (wasActive) {
      suppressClick = true;
      window.setTimeout(() => (suppressClick = false), 0);
    }
    const { key, dx } = { key: result.state.key, dx: dragState.dx };
    endDrag();
    if (result.commitReorder) commitReorder(key, dx);
  }
}
function onTabPointerDown(tab: ConversationTab, event: PointerEvent) {
  if (event.button !== 0 || closingKeys.value.has(tab.key) || dragState) return;
  if ((event.target as HTMLElement | null)?.closest('.tab-close')) return;
  dragRects = Array.from(
    tabsEl.value?.querySelectorAll<HTMLElement>('.tab:not(.closing)') || []
  ).map((node) => {
    const box = node.getBoundingClientRect();
    return { key: String(node.dataset.key), left: box.left, width: box.width };
  });
  const own = dragRects.find((rect) => rect.key === tab.key);
  dragGrabX = own ? Math.max(0, Math.min(own.width, event.clientX - own.left)) : 0;
  dragState = startDrag(tab.key, tab.kind);
  // 空白新对话标签同样需要显示器几何，否则窗口贴边时向上拖出不生效。
  if (tab.kind !== 'page') void loadDragGeometry(tab.key, event);
  dragStartX = event.clientX;
  dragPointerId = event.pointerId;
  // 捕获指针：拖出 chrome 视图（46px 高）后事件仍持续到达（壳层另有光标轮询兜底）。
  try {
    (event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId);
  } catch {
    // 捕获失败则只在条内排序。
  }
  drag.value = { ...idleDrag(), key: tab.key };
  window.addEventListener('pointermove', onDragMove);
  window.addEventListener('pointerup', onDragEnd);
  window.addEventListener('pointercancel', onDragLost);
  window.addEventListener('lostpointercapture', onDragLost, true);
  window.addEventListener('keydown', onDragKey, true);
}
// 向上拖出需要真实屏幕上沿（菜单栏、叠放显示器）：由壳层提供显示器 bounds，不靠 window.screen 猜。
// 信息到达前（或无壳层）退回纯 40px 规则。
async function loadDragGeometry(key: string, event: PointerEvent) {
  const box = tabsEl.value?.closest('header')?.getBoundingClientRect();
  const shell = (window as any).astrionShell;
  if (!box || !shell?.dragInfo) return;
  const stripTop = event.screenY - event.clientY + box.top;
  const startX = event.screenX;
  const startY = event.screenY;
  try {
    const info = await shell.dragInfo();
    if (!dragState || dragState.key !== key || !Array.isArray(info?.displays)) return;
    dragState = {
      ...dragState,
      geometry: { stripTop, startY, topLimit: topLimitFor({ x: startX, y: startY }, info.displays) }
    };
  } catch {
    // 取不到显示器信息：保持旧的 40px 规则。
  }
}
function moveInput(event: PointerEvent): DragInput {
  const box = tabsEl.value?.closest('header')?.getBoundingClientRect();
  const inX = !box || (event.clientX >= box.left && event.clientX <= box.right);
  const outside = box ? verticalOutside(event.clientY, box.top, box.bottom) : 0;
  return {
    type: 'move',
    dx: event.clientX - dragStartX,
    outside,
    inBand: inX && outside === 0,
    above: Boolean(box) && event.clientY < (box as DOMRect).top,
    screenX: event.screenX,
    screenY: event.screenY,
    offsetX: Math.min(300, TEAR_OFFSET_X_BASE + dragGrabX),
    offsetY: TEAR_OFFSET_Y
  };
}
// move 按 rAF 合并：拖出态每帧最多一条 move 消息，tear/cancel 等状态变化不丢（合并只保留最新位置）。
function onDragMove(event: PointerEvent) {
  if (event.pointerId !== dragPointerId || !dragState) return;
  pendingMove = event;
  if (moveFrame) return;
  moveFrame = window.requestAnimationFrame(flushMove);
}
function flushMove() {
  moveFrame = 0;
  const event = pendingMove;
  pendingMove = null;
  if (event && dragState) applyDragInput(moveInput(event));
}
function flushPendingMove() {
  if (moveFrame) window.cancelAnimationFrame(moveFrame);
  moveFrame = 0;
  flushMove();
}
function endDrag() {
  if (moveFrame) window.cancelAnimationFrame(moveFrame);
  moveFrame = 0;
  pendingMove = null;
  window.removeEventListener('pointermove', onDragMove);
  window.removeEventListener('pointerup', onDragEnd);
  window.removeEventListener('pointercancel', onDragLost);
  window.removeEventListener('lostpointercapture', onDragLost, true);
  window.removeEventListener('keydown', onDragKey, true);
  dragState = null;
  drag.value = idleDrag();
}
function onDragLost() {
  flushPendingMove();
  applyDragInput({ type: 'lost' });
}
function onDragKey(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !dragState) return;
  event.preventDefault();
  flushPendingMove();
  applyDragInput({ type: 'escape' });
}
function onDragEnd(event: PointerEvent) {
  if (event.pointerId !== dragPointerId || !dragState) return;
  flushPendingMove();
  applyDragInput({ type: 'end', screenX: event.screenX, screenY: event.screenY });
}
function commitReorder(key: string, dx: number) {
  const plan = planReorder(dragRects, key, dx);
  if (plan.beforeKey === null) return;
  pendingOrder.value = plan.order;
  void tabsStore
    .reorderTab(key, plan.beforeKey)
    .catch(() => {})
    .finally(() => (pendingOrder.value = null));
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
    const tab = tabsStore.mainTabs.find((item) => item.key === tabsStore.activeKey);
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
  endDrag();
});
</script>

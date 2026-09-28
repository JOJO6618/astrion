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
      :title="t('appUi.tabStripSettingsHint')"
      @click="onOpenSettings"
    >
      <span class="settings-icon" :style="settingsIconStyle" aria-hidden="true"></span>
    </button>
    <button
      class="chrome-icon new-tab-btn"
      :title="newTabHint"
      @click="onNewTab"
    >
      <svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10" /></svg>
    </button>
    <div class="tab-strip">
      <div ref="tabsEl" class="tabs">
        <div
          v-for="tab in tabsStore.tabs"
          :key="tab.key"
          class="tab"
          :class="{
            active: tab.key === tabsStore.activeKey,
            'has-status': Boolean(statusOf(tab)),
            closing: closingKeys.has(tab.key)
          }"
          :data-key="tab.key"
          @click="onTabClick(tab)"
          @auxclick="onTabAuxClick(tab, $event)"
        >
          <i class="foot foot-l"></i><i class="foot foot-r"></i>
          <span v-if="statusOf(tab)" class="tab-status" :class="statusOf(tab)"></span>
          <span class="tab-title">{{ titleOf(tab) }}</span>
          <button
            v-if="canClose(tab)"
            class="tab-close"
            :title="closeHint"
            @click.stop="requestClose(tab)"
          >
            <svg viewBox="0 0 10 10"><path d="M1.5 1.5l7 7M8.5 1.5l-7 7" /></svg>
          </button>
        </div>
      </div>
    </div>
    <div v-if="isWindows" class="window-controls">
      <button class="wc-btn" :title="t('appUi.tabStripWinMinimize')" @click="onWindowControl('minimize')">
        <svg viewBox="0 0 12 12"><path d="M2 6h8" /></svg>
      </button>
      <button
        class="wc-btn"
        :title="maximized ? t('appUi.tabStripWinRestore') : t('appUi.tabStripWinMaximize')"
        @click="onWindowControl('maximize-toggle')"
      >
        <svg v-if="!maximized" viewBox="0 0 12 12"><rect x="2.5" y="2.5" width="7" height="7" rx="1" /></svg>
        <svg v-else viewBox="0 0 12 12"><path d="M4 3.2V2.8A1.3 1.3 0 0 1 5.3 1.5h4.2a1.3 1.3 0 0 1 1.3 1.3v4.2a1.3 1.3 0 0 1-1.3 1.3h-.4" /><rect x="1.5" y="4" width="6.5" height="6.5" rx="1" /></svg>
      </button>
      <button class="wc-btn wc-close" :title="t('appUi.tabStripWinClose')" @click="onWindowControl('close')">
        <svg viewBox="0 0 12 12"><path d="M2.8 2.8l6.4 6.4M9.2 2.8L2.8 9.2" /></svg>
      </button>
    </div>
  </header>
</template>

<script setup lang="ts">
// 桌面壳 chrome 独立 webview 的顶部对话标签条（双 webview 架构，见
// desktop/src-tauri/src/backend.rs）。与主页面内的旧用法不同：
// - 本组件只做乐观镜像，标签数据单一写者是主页面；
// - 操作 = 本地乐观变更 + POST /api/desktop/chrome-dispatch 派发意图
//   （后端代理到壳控制桥，壳 eval 进主 webview 应用）；
// - 状态点 = 服务端快照的 tab.running + 本地 donePending（曾运行→停止的
//   未查看标记），当前激活标签不显示点；
// - 形态与交互对齐 cache/tab-strip-demo 定稿：
//   hover 圆角矩形 / 选中反向圆角融合脚（纯 CSS） / 切换瞬时 / 关闭沉入+FLIP 补位。
// 窗口拖拽：chrome 条空白区域 mousedown → 后端代理 → 壳控制桥 start_dragging。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { t } from '@/locales';
import { ICONS } from '@/utils/icons';
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/stores/ui';
import { useConversationTabsStore, type ConversationTab } from '@/stores/conversationTabs';

const tabsStore = useConversationTabsStore();
const { activeKey } = storeToRefs(tabsStore);

/* 设置齿轮用项目图标库（utils/icons.ts + static/icons/settings.svg），mask 方式
   继承文字颜色。chrome 页不加载 base/_global.scss 的 .icon 工具类，
   mask 样式在 _tab-strip.scss 的 .settings-icon 里自带。 */
const settingsIconStyle = { '--icon-src': `url(${ICONS.settings})` };

/* 对话记录侧边栏展开状态：主页面 ui store 变更时写 localStorage
   （stores/ui.ts SIDEBAR_COLLAPSED_STORAGE_KEY），本页轮询读取——跨 webview
   无 storage 事件，与 ChromeApp 的主题同步同模式。用途：侧边栏展开后首个
   对话标签位于侧边栏上方，其选中融合色需在侧边栏展开动画期间渐变为侧边栏
   底色（sidebar-fuse 样式与完整设计说明见 _tab-strip.scss 对应注释块）。
   轮询周期 150ms：足够跟上 320ms 的展开动画，localStorage 读取开销可忽略。
   注意：融合类只跟随侧边栏状态、不叠加「首标签激活」条件——否则侧边栏
   展开期间点击激活首标签时，融合色会额外播一次 320ms 渐变，破坏「切换
   标签瞬时无动画」的规则；是否作用于首标签由 CSS :first-child 选择器决定。 */
const sidebarOpen = ref(false);
let sidebarPollTimer = 0;

function syncSidebarState() {
  try {
    sidebarOpen.value = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '0';
  } catch {
    // localStorage 不可用时保持当前值
  }
}

/* 平台标记：壳 initialization_script 注入（desktop/src-tauri/src/backend.rs）。
   Windows 是原生标题栏（三大键在标题栏里），无红绿灯悬浮区——标签条左侧
   预留位改放「设置」入口按钮；快捷键提示修饰键用 Ctrl 而非 ⌘。 */
const isWindows = (window as unknown as { __ASTRION_PLATFORM__?: string }).__ASTRION_PLATFORM__ === 'windows';
const modKey = isWindows ? 'Ctrl' : '⌘';
const newTabHint = computed(() => t('appUi.tabStripNewTabHint', { mod: modKey }));
const closeHint = computed(() => t('appUi.tabStripCloseHint', { mod: modKey }));

const tabsEl = ref<HTMLElement | null>(null);
const closingKeys = ref(new Set<string>());

/* 状态点：运行中=蓝（服务端快照 tab.running）；曾运行→停止且未查看=绿（本地
   donePending，激活该标签后清除）。当前激活标签不显示任何点。 */
const donePending = ref(new Set<string>());
const prevRunning = new Map<string, boolean>();

function statusOf(tab: ConversationTab): 'running' | 'done' | null {
  if (tab.kind !== 'conv' || !tab.conversationId) return null;
  if (tab.key === tabsStore.activeKey) return null;
  if (tab.running) return 'running';
  return donePending.value.has(tab.key) ? 'done' : null;
}

watch(
  () => tabsStore.tabs.map((tab) => `${tab.key}:${tab.running ? 1 : 0}`).join('|'),
  () => {
    const next = new Set(donePending.value);
    for (const tab of tabsStore.tabs) {
      const was = prevRunning.get(tab.key);
      const running = Boolean(tab.running);
      if (was === true && !running && tab.key !== tabsStore.activeKey) next.add(tab.key);
      prevRunning.set(tab.key, running);
    }
    if (next.size !== donePending.value.size) donePending.value = next;
  }
);

// 激活后清除该标签的完成标记
watch(activeKey, (key) => {
  if (key && donePending.value.has(key)) {
    const next = new Set(donePending.value);
    next.delete(key);
    donePending.value = next;
  }
});

function titleOf(tab: ConversationTab): string {
  return tab.kind === 'new' ? t('appUi.tabStripNewTab') : tab.title || t('appUi.tabStripNewTab');
}

/** 意图派发：chrome → 后端代理 → 壳控制桥 → eval 进主 webview。 */
function dispatch(action: string, payload: Record<string, unknown> = {}) {
  fetch('/api/desktop/chrome-dispatch', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, payload })
  }).catch(() => {
    // 桥不可达（旧壳）静默忽略；快照轮询会收敛显示态
  });
}

/** Windows 左侧设置入口：意图派发给主页面，由主页面整跳 /settings
   （复用 openSettingsPage 既有链路：暂存激活标签 + 整页跳转）。 */
function onOpenSettings() {
  dispatch('open-settings');
}

function onTabClick(tab: ConversationTab) {
  if (closingKeys.value.has(tab.key)) return;
  if (tab.key === tabsStore.activeKey) return;
  tabsStore.setActive(tab.key);
  // 载荷带上完整标签信息：主页面在设置/工作流等独立路由下 store 可能未
  // 填充（hydrate 失败或被清空），handleChromeIntent 可用载荷兜底导航
  dispatch('activate', {
    key: tab.key,
    kind: tab.kind,
    conversationId: tab.conversationId,
    workspaceId: tab.workspaceId
  });
  scrollTabIntoView(tab.key);
}

function onTabAuxClick(tab: ConversationTab, event: MouseEvent) {
  if (event.button === 1) {
    event.preventDefault();
    requestClose(tab);
  }
}

function onNewTab() {
  const tab = tabsStore.addNewTab({ workspaceId: '', workspaceLabel: '' });
  dispatch('new');
  if (tab) scrollTabIntoView(tab.key);
}

/** 关闭按钮可见性：仅剩一个空对话标签时不显示（关了就没有任何出口）。 */
function canClose(tab: ConversationTab): boolean {
  return !(tabsStore.tabs.length === 1 && tab.kind === 'new');
}

/* 关闭：先沉入下方消失（230ms），再从 store 移除并让其余标签 FLIP 滑动补位。
   特例：仅剩的一个实际对话标签被关闭时，不做下沉动画——就地变为「新对话」
   标签（用户定稿：标题直接变，不要右侧冒出新标签再下沉）。 */
function requestClose(tab: ConversationTab) {
  if (closingKeys.value.has(tab.key)) return;
  if (!canClose(tab)) return;

  if (tabsStore.tabs.length === 1 && tab.kind === 'conv') {
    // 就地转换：移除旧标签 + 同位置插入新标签（同帧替换，无动画）
    tabsStore.closeTab(tab.key);
    tabsStore.addNewTabAt(0, {
      workspaceId: tab.workspaceId,
      workspaceLabel: tab.workspaceLabel
    });
    dispatch('close', { key: tab.key });
    forceHoverReeval();
    return;
  }

  closingKeys.value = new Set(closingKeys.value).add(tab.key);

  window.setTimeout(() => {
    const container = tabsEl.value;
    // FLIP 第一步：记录兄弟标签当前位置
    const siblings = container
      ? Array.from(container.querySelectorAll<HTMLElement>('.tab:not(.closing)'))
      : [];
    const beforeX = new Map(siblings.map((s) => [s.dataset.key, s.getBoundingClientRect().left]));

    tabsStore.closeTab(tab.key);
    dispatch('close', { key: tab.key });
    closingKeys.value.delete(tab.key);
    closingKeys.value = new Set(closingKeys.value);

    // WebKit 在元素移动（FLIP）到静止鼠标下方后不重算 :hover——按钮划到鼠标下
    // 却不显示 ×/hover 态，必须抖一下鼠标。短暂切掉 pointer-events 强制重算。
    forceHoverReeval();

    // FLIP 第二步：倒置位移，松手滑向新位置
    nextTick(() => {
      siblings.forEach((s) => {
        const dx = (beforeX.get(s.dataset.key) ?? 0) - s.getBoundingClientRect().left;
        if (!dx) return;
        s.style.transition = 'none';
        s.style.transform = `translateX(${dx}px)`;
        requestAnimationFrame(() => {
          s.style.transition = 'transform 0.28s cubic-bezier(.3,.9,.35,1)';
          s.style.transform = '';
          s.addEventListener(
            'transitionend',
            () => {
              s.style.transition = '';
              // 补位动画结束后再强制重算一次（元素最终位置落定后）
              forceHoverReeval();
            },
            { once: true }
          );
        });
      });
    });
  }, 230);
}

/* 强制 WebKit 重新计算静止指针下的 :hover（pointer-events 抖动脉冲） */
function forceHoverReeval() {
  const el = tabsEl.value;
  if (!el) return;
  el.style.pointerEvents = 'none';
  void el.offsetHeight; // 强制同步布局，让禁用生效
  el.style.pointerEvents = '';
}

function scrollTabIntoView(key: string) {
  nextTick(() => {
    const el = tabsEl.value?.querySelector<HTMLElement>(`.tab[data-key="${key}"]`);
    el?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  });
}

/* chrome 条空白区域按下 → 拖拽窗口（标签/按钮/三大键自身不触发）。
   Windows 无边框模式用位移阈值模式：按下即触发的 start_dragging（HTCAPTION
   接管）会吞掉后续双击序列导致双击最大化失效，故按下只记录起点，移动超过
   阈值才触发拖拽；静止双击不触发，dblclick 事件正常到达。mac 保持原链路
   （Electron 壳由 CSS app-region 接管拖拽，该请求是空操作兼容）。 */
let dragArm: { x: number; y: number } | null = null;
let dragFired = false;

function startWindowDrag() {
  fetch('/api/desktop/window/drag', { method: 'POST', credentials: 'same-origin' }).catch(() => {
    // 桥不可达（非桌面壳/旧壳）静默忽略
  });
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
  // HTCAPTION 接管后 webview 不再收到后续鼠标事件，主动解除监听防泄漏
  cleanupDragArm();
}

function onDragMouseUp() {
  cleanupDragArm();
}

function onBarMouseDown(event: MouseEvent) {
  if (event.button !== 0) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('.tab, .chrome-icon, .window-controls')) return;
  if (isWindows) {
    dragArm = { x: event.clientX, y: event.clientY };
    dragFired = false;
    window.addEventListener('mousemove', onDragMouseMove);
    window.addEventListener('mouseup', onDragMouseUp);
    return;
  }
  startWindowDrag();
}

/** Windows 无边框模式：双击空白区域 = 最大化/还原（浏览器惯例）。 */
function onBarDoubleClick(event: MouseEvent) {
  if (!isWindows) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('.tab, .chrome-icon, .window-controls')) return;
  onWindowControl('maximize-toggle');
}

/* Windows 自绘三大键：maximized 决定最大化/还原图标。1s 轮询收敛外部改变
   （拖边贴靠/Win+方向键等不经本组件的路径）；点击本身做乐观更新立即换图标。 */
const maximized = ref(false);
let windowStateTimer = 0;

async function syncWindowState() {
  try {
    const resp = await fetch('/api/desktop/window/state', { credentials: 'same-origin' });
    const payload = await resp.json().catch(() => null);
    if (payload?.success && payload.data) {
      maximized.value = Boolean(payload.data.maximized);
    }
  } catch {
    // 桥不可达保持当前值，下轮再试
  }
}

/** 三大键动作 → 后端代理 → 壳控制桥 /window/control。 */
function onWindowControl(action: string) {
  if (action === 'maximize-toggle') maximized.value = !maximized.value;
  fetch('/api/desktop/window/control', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action })
  }).catch(() => {
    // 桥不可达静默忽略
  });
}

// activeKey 变化时保证可见（覆盖快捷键/快照收敛路径的切换）
watch(activeKey, (key) => {
  if (key) scrollTabIntoView(key);
});

/* ⌘T 新建 / ⌘W 关闭当前标签（chrome webview 独立窗口级监听）。 */
function onKeydown(event: KeyboardEvent) {
  if (!(event.metaKey || event.ctrlKey)) return;
  const key = event.key.toLowerCase();
  if (key === 't') {
    event.preventDefault();
    onNewTab();
  } else if (key === 'w') {
    event.preventDefault();
    const active = tabsStore.activeTab;
    if (active && canClose(active)) requestClose(active);
  }
}
onMounted(() => {
  window.addEventListener('keydown', onKeydown);
  if (isWindows) {
    syncWindowState();
    windowStateTimer = window.setInterval(syncWindowState, 1000);
  }
  syncSidebarState();
  sidebarPollTimer = window.setInterval(syncSidebarState, 150);
});
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown);
  if (windowStateTimer) window.clearInterval(windowStateTimer);
  if (sidebarPollTimer) window.clearInterval(sidebarPollTimer);
  cleanupDragArm();
});
</script>

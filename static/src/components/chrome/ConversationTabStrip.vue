<template>
  <header class="chrome-tab-strip" @mousedown="onBarMouseDown">
    <button
      class="chrome-icon new-tab-btn"
      :title="t('appUi.tabStripNewTabHint')"
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
            :title="t('appUi.tabStripCloseHint')"
            @click.stop="requestClose(tab)"
          >
            <svg viewBox="0 0 10 10"><path d="M1.5 1.5l7 7M8.5 1.5l-7 7" /></svg>
          </button>
        </div>
      </div>
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
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { t } from '@/locales';
import { useConversationTabsStore, type ConversationTab } from '@/stores/conversationTabs';

const tabsStore = useConversationTabsStore();
const { activeKey } = storeToRefs(tabsStore);

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

/* chrome 条空白区域按下 → 拖拽窗口（标签/按钮自身不触发）。
   双击空白 = macOS 惯例的缩放行为暂不做（MVP 只拖拽）。 */
function onBarMouseDown(event: MouseEvent) {
  if (event.button !== 0) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('.tab, .chrome-icon')) return;
  fetch('/api/desktop/window/drag', { method: 'POST', credentials: 'same-origin' }).catch(() => {
    // 桥不可达（非桌面壳/旧壳）静默忽略
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
onMounted(() => window.addEventListener('keydown', onKeydown));
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown));
</script>

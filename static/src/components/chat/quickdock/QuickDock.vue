<template>
  <aside
    class="quick-dock"
    :class="{
      'quick-dock--empty': !settingsReady || !hasContent || userCollapsed,
      'quick-dock--no-anim': noAnim
    }"
  >
    <div ref="scrollRef" class="quick-dock__scroll" @scroll.passive="handleStackScroll">
      <WorkflowWindow />
      <TodoWindow />
      <RunnerWindow kind="agent" />
      <RunnerWindow kind="cmd" />
      <FileWindow />
    </div>

    <!-- 详情面板（fixed 浮在列左侧） -->
    <RunnerDetailPanel />

    <!-- 全局 ⋯ 菜单（fixed 单例） -->
    <div v-if="menu" class="qd-menu menu-enter" :style="menuStyle" @click.stop>
      <template v-if="menu.type === 'runner'">
        <button
          class="qd-menu__item qd-menu__item--danger"
          :disabled="!menuTargetRunning"
          @click="killRunner"
        >
          强制关闭
        </button>
      </template>
      <template v-else>
        <button class="qd-menu__item" @click="downloadFile">下载</button>
        <button v-if="hostMode" class="qd-menu__item" @click="revealInManager">
          在文件管理器中打开
        </button>
        <button class="qd-menu__item" @click="copyPath">复制路径</button>
      </template>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import {
  useQuickDockStore,
  persistQuickDockHadContent,
  persistQuickDockConvContent
} from '@/stores/quickDock';
import { useSubAgentStore } from '@/stores/subAgent';
import { useBackgroundCommandStore } from '@/stores/backgroundCommand';
import { useConversationStore } from '@/stores/conversation';
import { useUiStore } from '@/stores/ui';
import { usePersonalizationStore, hasCachedQuickDockAutoExpand } from '@/stores/personalization';
import { useFileStore } from '@/stores/file';
import { useWorkflowStore } from '@/stores/workflow';
import TodoWindow from './TodoWindow.vue';
import WorkflowWindow from './WorkflowWindow.vue';
import RunnerWindow from './RunnerWindow.vue';
import RunnerDetailPanel from './RunnerDetailPanel.vue';
import FileWindow from './FileWindow.vue';

/**
 * 快捷窗口（Quick Dock）容器
 * 对话区右侧占位列：工作流 / 待办 / 子智能体 / 后台指令 / 文件 五个窗口上下排布。
 * 同时负责：全局 ⋯ 菜单、Esc 分层关闭、列表轮询、对话切换重置。
 */

defineProps<{ hostMode: boolean }>();

const quickDock = useQuickDockStore();
const subAgentStore = useSubAgentStore();
const bgStore = useBackgroundCommandStore();
const conversationStore = useConversationStore();
const uiStore = useUiStore();
const fileStore = useFileStore();
const workflowStore = useWorkflowStore();
const personalizationStore = usePersonalizationStore();

// hasContent / userCollapsed 提升到 store：App.vue 的展开收起按钮共用同一判定
const { menu, hasContent, userCollapsed } = storeToRefs(quickDock);
const scrollRef = ref<HTMLElement | null>(null);

// 手动收起时关掉所有瞬态面板（详情/预览/菜单），避免固定定位的详情面板悬空
watch(userCollapsed, (collapsed) => {
  if (collapsed) {
    quickDock.resetTransient();
  }
});

/** 快捷窗口自动展开设置（个人空间·外观与显示，默认是；为否则只能手动点击按钮展开） */
const autoExpand = computed(() => personalizationStore.form.quick_dock_auto_expand !== false);

/**
 * 个性化设置是否就绪。有本地缓存时首帧即就绪（表单已从缓存同步初始化，
 * 与主题缓存同理）；无缓存（首次使用）或接口失败时退化为等待接口/按默认值呈现。
 * 就绪前强制空态（宽 0 不可见）：否则手动模式会先渲染展开态再纠正。
 */
const settingsReady = computed(
  () =>
    hasCachedQuickDockAutoExpand() || personalizationStore.loaded || !!personalizationStore.error
);

/**
 * 无过渡窗口（quick-dock--no-anim）：禁用容器展开/收起过渡，窗口内状态校正瞬间完成。
 * 两个开启时机：
 * 1. 初始加载：设置与首批内容（子智能体/后台指令/bootstrap 回填/待办）异步到达，
 *    若不禁用会播「先空再展开」/「先展开再收起」动画；
 * 2. 切换对话：旧内容刻意保留至新对话 bootstrap 回填（防「先收再放」），回填后
 *    内容有无翻转同样不能播动画，需瞬间切换成新对话的收起/展开状态。
 * 窗口关闭条件：新一轮回填到齐（同步序号推进）+ 短宽限；超时强制关闭防挂起。
 */
const noAnim = ref(true);
/** 回填到齐后的短宽限：合并同批次略晚到达的其余数据（如子智能体刷新） */
const SYNC_SETTLE_GRACE_MS = 300;
/** 兜底上限：回填异常慢/失败时也不能永久禁用过渡（到点强制关闭窗口） */
const NO_ANIM_CAP_MS = 3000;

/** 窗口代数：重开窗口会使上一代未完成的 rAF 回调失效 */
let windowGeneration = 0;
/** 开窗时捕获的同步进度：序号超过它才算「新一轮回填已到齐」 */
let gateTarget: { filesSeq: number; todoSeq: number } | null = null;
let gateWatchStop: (() => void) | null = null;
let releaseTimer: ReturnType<typeof setTimeout> | null = null;
let forceTimer: ReturnType<typeof setTimeout> | null = null;
/** 首批子智能体/后台指令拉取未完成前不关闭初始窗口（它们也参与 hasContent） */
let initialRefreshPending = true;

function gatesPassed(): boolean {
  if (initialRefreshPending) {
    return false;
  }
  if (!gateTarget) {
    return true;
  }
  return quickDock.filesSyncSeq > gateTarget.filesSeq && fileStore.todoSyncSeq > gateTarget.todoSeq;
}

function clearWindowTimers() {
  if (releaseTimer !== null) {
    clearTimeout(releaseTimer);
    releaseTimer = null;
  }
  if (forceTimer !== null) {
    clearTimeout(forceTimer);
    forceTimer = null;
  }
}

/** 开启/重开无过渡窗口：捕获当前同步进度，等新一轮回填到齐后关闭 */
function openNoAnimWindow() {
  windowGeneration += 1;
  noAnim.value = true;
  gateTarget = { filesSeq: quickDock.filesSyncSeq, todoSeq: fileStore.todoSyncSeq };
  clearWindowTimers();
  if (!gateWatchStop) {
    gateWatchStop = watch(
      () => [quickDock.filesSyncSeq, fileStore.todoSyncSeq],
      () => {
        scheduleReleaseIfSynced();
      }
    );
  }
  scheduleReleaseIfSynced();
  forceTimer = setTimeout(() => {
    releaseNoAnim(true);
  }, NO_ANIM_CAP_MS);
}

function scheduleReleaseIfSynced() {
  if (!noAnim.value || !gatesPassed()) {
    return;
  }
  if (releaseTimer !== null) {
    clearTimeout(releaseTimer);
  }
  releaseTimer = setTimeout(() => {
    releaseTimer = null;
    releaseNoAnim();
  }, SYNC_SETTLE_GRACE_MS);
}

function releaseNoAnim(force = false) {
  if (!noAnim.value || (!force && !gatesPassed())) {
    return;
  }
  clearWindowTimers();
  if (gateWatchStop) {
    gateWatchStop();
    gateWatchStop = null;
  }
  gateTarget = null;
  // 顺带结束首帧乐观内容标记（幂等）：此后 hasContent 只看真实状态
  quickDock.settleInitialContent();
  // 双 rAF：先把最终状态无过渡绘制一帧，再恢复过渡，避免恢复瞬间补播动画；
  // 期间若重开了窗口（代数变化），本次恢复作废
  const generation = windowGeneration;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (generation === windowGeneration) {
        noAnim.value = false;
      }
    });
  });
}

// 内容集合变化（出现/清空）时按模式校正展开状态：
// - 自动展开（默认）：重置手动标记，新内容到达自动展开
//   （按钮常显后，无内容时也可能被点击置为收起；不重置会导致新内容到达不展开）
// - 仅手动展开：保持/恢复收起态，等用户点击按钮；
//   用户手动展开后只要内容不完全清空（hasContent 不再变化），dock 保持展开
watch(
  hasContent,
  (content) => {
    quickDock.userCollapsed = !autoExpand.value;
  },
  // immediate：挂载时即校正（手动模式下刷新恢复对话，已有内容不应自动展开）
  { immediate: true }
);

// 设置切换立即生效：切到「否」收起等手动展开；切回「是」有内容即展开
watch(autoExpand, (enabled) => {
  quickDock.userCollapsed = !enabled;
});

// 持续维护「dock 是否有内容」缓存（全局 + 按对话两条）：
// 下次进入页面/切换对话时即可按最终状态静态渲染。
// 乐观掩码生效期间不写，避免把猜测值当真；掩码关闭时若状态翻转
// 本 watch 会触发并写入真实值，缓存自我修正。
watch(hasContent, (content) => {
  if (!quickDock.assumedActive) {
    persistQuickDockHadContent(content);
    const convId = conversationStore.currentConversationId;
    if (convId) {
      persistQuickDockConvContent(convId, content);
    }
  }
});

const AGENT_TERMINAL = new Set(['completed', 'failed', 'timeout', 'terminated']);
const CMD_TERMINAL = new Set(['completed', 'failed', 'timeout', 'cancelled']);

/* ---------------- 菜单定位与目标状态 ---------------- */

const MENU_ESTIMATED_HEIGHT = 128;

const menuStyle = computed(() => {
  const m = menu.value;
  if (!m) {
    return {};
  }
  const top = Math.min(m.top, window.innerHeight - MENU_ESTIMATED_HEIGHT - 8);
  if (m.alignRight) {
    return { right: `${window.innerWidth - m.left}px`, top: `${top}px` };
  }
  return { left: `${m.left}px`, top: `${top}px` };
});

const menuTargetRunning = computed(() => {
  const m = menu.value;
  if (!m || m.type !== 'runner') {
    return false;
  }
  if (m.kind === 'agent') {
    const agent = subAgentStore.subAgents.find((a) => a.task_id === m.key);
    const status = (agent?.status || '').toString().toLowerCase();
    return !!agent && !AGENT_TERMINAL.has(status);
  }
  const cmd = bgStore.commands.find((c) => c.command_id === m.key);
  const status = (cmd?.status || '').toString().toLowerCase();
  return !!cmd && !CMD_TERMINAL.has(status);
});

/* ---------------- 菜单动作 ---------------- */

async function killRunner() {
  const m = menu.value;
  if (!m || m.type !== 'runner') {
    return;
  }
  quickDock.closeMenu();
  const result =
    m.kind === 'agent'
      ? await subAgentStore.terminateSubAgent(m.key)
      : await bgStore.cancelCommand(m.key);
  if (!result?.success) {
    uiStore.pushToast({ message: result?.error || '强制关闭失败', type: 'error' });
  }
}

function basename(p: string): string {
  return p.split('/').pop() || p;
}

function downloadFile() {
  const m = menu.value;
  if (!m || m.type !== 'file') {
    return;
  }
  quickDock.closeMenu();
  const a = document.createElement('a');
  a.href = `/api/download/file?path=${encodeURIComponent(m.key)}`;
  a.download = basename(m.key);
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** 用系统默认应用打开（候选列表第一个即系统默认 handler） */
async function revealInManager() {
  const m = menu.value;
  if (!m || m.type !== 'file') {
    return;
  }
  const path = m.key;
  quickDock.closeMenu();
  try {
    const resp = await fetch(`/api/project/file-open-apps?path=${encodeURIComponent(path)}`);
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || '检测可用应用失败');
    }
    const apps = Array.isArray(payload?.data?.apps) ? payload.data.apps : [];
    if (!apps.length) {
      throw new Error('未找到可用应用');
    }
    const openResp = await fetch('/api/project/open-file-with-app', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, app_id: apps[0].id })
    });
    const openPayload = await openResp.json().catch(() => ({}));
    if (!openResp.ok || !openPayload?.success) {
      throw new Error(openPayload?.error || '打开文件失败');
    }
  } catch (err: any) {
    uiStore.pushToast({ message: err?.message || '打开文件失败', type: 'error' });
  }
}

async function copyPath() {
  const m = menu.value;
  if (!m || m.type !== 'file') {
    return;
  }
  const path = m.key;
  quickDock.closeMenu();
  const toast = (message: string, type = 'info') =>
    uiStore.pushToast({ message, type, duration: 2000 });
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(path);
      toast('已复制相对路径', 'success');
      return;
    } catch {
      // 走 fallback
    }
  }
  const ta = document.createElement('textarea');
  ta.value = path;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    toast('已复制相对路径', 'success');
  } catch {
    toast('复制失败', 'error');
  }
  ta.remove();
}

/* ---------------- 全局事件：Esc 分层关闭 / 点空白关菜单 / 栈滚动关菜单 ---------------- */

function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.defaultPrevented) {
    return;
  }
  if (menu.value) {
    quickDock.closeMenu();
    e.preventDefault();
    return;
  }
  if (quickDock.detail) {
    quickDock.closeDetail();
    e.preventDefault();
    return;
  }
  if (quickDock.previewPath) {
    quickDock.closePreview();
    e.preventDefault();
  }
}

function onDocumentClick(e: MouseEvent) {
  if (!menu.value) {
    return;
  }
  const target = e.target as HTMLElement | null;
  if (target?.closest('.qd-menu') || target?.closest('.qd-row-menu-btn')) {
    return;
  }
  quickDock.closeMenu();
}

function handleStackScroll() {
  // 菜单是 fixed 定位不随栈滚动，栈滚动时主动关闭
  if (menu.value) {
    quickDock.closeMenu();
  }
}

/* ---------------- 轮询（常驻，5s）与对话切换 ---------------- */

let pollTimer: ReturnType<typeof setInterval> | null = null;

function refreshAll() {
  void subAgentStore.fetchSubAgents();
  void bgStore.fetchCommands();
}

onMounted(() => {
  // 首批内容拉取（同时作为初始无过渡窗口的关闭条件之一）
  const initialRefresh = Promise.allSettled([
    subAgentStore.fetchSubAgents(),
    bgStore.fetchCommands()
  ]);
  pollTimer = setInterval(refreshAll, 5000);
  document.addEventListener('keydown', onKeydown);
  document.addEventListener('click', onDocumentClick);
  // 开启初始无过渡窗口：新一轮回填到齐 + 首批拉取完成后关闭；兜底定时器到点强制关闭
  openNoAnimWindow();
  void initialRefresh.then(() => {
    initialRefreshPending = false;
    scheduleReleaseIfSynced();
  });
});

onBeforeUnmount(() => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  clearWindowTimers();
  document.removeEventListener('keydown', onKeydown);
  document.removeEventListener('click', onDocumentClick);
  quickDock.resetTransient();
});

watch(
  () => conversationStore.currentConversationId,
  () => {
    // 只关瞬态面板；列表数据保留，由 bootstrap（edited_files）与 fetchTodoList 回填覆盖，
    // 避免切换对话时列「先收起再展开」闪烁（/new 场景由 app watcher 负责清空）。
    quickDock.resetTransient();
    // 工作流状态按对话隔离，但不清空——与待办同款「保留至回填」防闪烁：
    // 切到有工作流的对话（含 /new 激活后自动进入新对话）数据一致、无感切换；
    // 切到无工作流的对话由回填结果清空（live=false，不播退出动画）。
    const switchConvId = conversationStore.currentConversationId;
    if (switchConvId) {
      void workflowStore.fetchStatus(switchConvId);
    } else {
      workflowStore.reset();
    }
    refreshAll();
    // 切换期间重开无过渡窗口：新对话回填后的收起/展开状态瞬间切换，不播容器动画。
    // flush:'sync' 保证在 app watcher 清空回填（/new 场景）之前捕获同步序号，
    // 否则序号先推进、门控永远等不到而挂到超时
    openNoAnimWindow();
    // 按缓存乐观假定目标对话的 dock 状态：有内容则切过去即是展开（无动画、无延迟），
    // 无内容则旧内容保留至回填后瞬间收起；缓存在窗口关闭时自我纠正
    quickDock.assumeContentForConversation(conversationStore.currentConversationId);
  },
  { flush: 'sync' }
);
</script>

<style src="./quickdock.css"></style>

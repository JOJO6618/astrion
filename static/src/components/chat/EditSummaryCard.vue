<script setup lang="ts">
/**
 * 本次工作编辑摘要卡片（Edit Summary Card）
 *
 * 数据来自发起本轮工作的 user 消息 metadata.edit_summary（后端在
 * write_file/edit_file 成功后实时写入并广播），渲染在该工作段最后一条
 * assistant 消息末尾。同一文件多次编辑只展示合并后的最终结果（净变化）。
 *
 * 交互：
 * - 桌面端 hover 行（600ms 意图延迟）打开居中浮窗；离开行与浮窗后自动关闭；
 *   弹窗已打开时鼠标移到其他行，瞬间切换内容（不再等延迟）
 * - 点击行「钉住」弹窗（带遮罩 + 关闭按钮）；移动端无 hover，点击为唯一入口
 * - 弹窗位置/高度在展开瞬间一次性计算并钉死，之后不再重算
 * - 外部滚动 / 窗口缩放：直接关闭弹窗；钉住弹窗内部滚动放行（可滚动查看长 diff），
 *   hover 弹窗内部滚动同样关闭
 * - Esc 关闭
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useUiStore } from '@/stores/ui';

const props = defineProps<{
  summary: any;
  iconStyle: (key: string, size?: string) => Record<string, string>;
}>();

const uiStore = useUiStore();
const isMobile = computed(() => !!uiStore.isMobileViewport);

interface SummaryFile {
  path: string;
  status?: string;
  added?: number;
  removed?: number;
  lines?: any[];
  truncated?: boolean;
}

const files = computed<SummaryFile[]>(() => {
  const list = props.summary?.files;
  return Array.isArray(list) ? list.filter((f: any) => f && f.path) : [];
});

const totals = computed(() => {
  let added = 0;
  let removed = 0;
  for (const f of files.value) {
    added += Number(f.added) || 0;
    removed += Number(f.removed) || 0;
  }
  return { added, removed };
});

// ---------------- 弹窗状态 ----------------
const activePath = ref<string | null>(null);
const pinned = ref(false);
const cardRef = ref<HTMLElement | null>(null);
const anchorEl = ref<HTMLElement | null>(null);
// 桌面端弹窗的内联定位样式（水平相对卡片居中、垂直锚定条目上方）；
// 移动端保持空对象，回退到 CSS 的屏幕居中布局。
const panelStyle = ref<Record<string, string>>({});
let openTimer: ReturnType<typeof setTimeout> | null = null;
let closeTimer: ReturnType<typeof setTimeout> | null = null;

const activeFile = computed<SummaryFile | null>(() => {
  if (!activePath.value) return null;
  return files.value.find((f) => f.path === activePath.value) || null;
});

// ---------------- 弹窗定位（桌面端） ----------------
const PANEL_MAX_WIDTH = 720;
const VIEWPORT_MARGIN = 16;
const MIN_PANEL_HEIGHT = 140;
// 桌面端弹窗最大高度上限（2026-08-22 恢复）：与 CSS 基础值 min(70vh, 640px) 对齐，
// 在动态可用空间之上再取一次 min，避免高视口下弹窗占满半屏以上
const PANEL_MAX_HEIGHT_VH_RATIO = 0.7;
const PANEL_MAX_HEIGHT_PX = 640;
// 条目上方至少要有这么多可用空间才放上面，否则优先考虑下方
const PREFER_ABOVE_MIN_HEIGHT = 260;

function computePanelStyle() {
  if (isMobile.value || !cardRef.value || !anchorEl.value) {
    // 移动端 / 无锚点：回退到 CSS 的屏幕居中布局
    panelStyle.value = {};
    return;
  }
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.min(PANEL_MAX_WIDTH, vw - VIEWPORT_MARGIN * 2);

  // 水平：相对编辑卡片左右居中（而不是相对屏幕），并夹取在视口内
  const cardRect = cardRef.value.getBoundingClientRect();
  const centerX = cardRect.left + cardRect.width / 2;
  const left = Math.min(
    Math.max(centerX - width / 2, VIEWPORT_MARGIN),
    vw - width - VIEWPORT_MARGIN
  );

  // 垂直：优先放条目上方（底边 = 条目上边缘 - 半个条目高，向上生长）；
  // 上方空间不足且下方更宽裕时改放下方（顶边 = 条目下边缘 + 半个条目高，向下生长）。
  // 高度上限 = min(该方向可用空间, 70vh, 640px)，保底 MIN_PANEL_HEIGHT。
  const maxCapHeight = Math.min(vh * PANEL_MAX_HEIGHT_VH_RATIO, PANEL_MAX_HEIGHT_PX);
  const rowRect = anchorEl.value.getBoundingClientRect();
  const anchorAbove = rowRect.top - rowRect.height / 2;
  const anchorBelow = rowRect.bottom + rowRect.height / 2;
  const spaceAbove = anchorAbove - VIEWPORT_MARGIN;
  const spaceBelow = vh - VIEWPORT_MARGIN - anchorBelow;
  const placeAbove = spaceAbove >= PREFER_ABOVE_MIN_HEIGHT || spaceAbove >= spaceBelow;

  const base: Record<string, string> = {
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
    transform: 'none'
  };
  if (placeAbove) {
    const bottomEdge = Math.min(
      Math.max(anchorAbove, VIEWPORT_MARGIN + MIN_PANEL_HEIGHT),
      vh - VIEWPORT_MARGIN
    );
    panelStyle.value = {
      ...base,
      top: 'auto',
      bottom: `${Math.round(vh - bottomEdge)}px`,
      maxHeight: `${Math.round(Math.min(bottomEdge - VIEWPORT_MARGIN, maxCapHeight))}px`
    };
  } else {
    const topEdge = Math.min(
      Math.max(anchorBelow, VIEWPORT_MARGIN),
      vh - VIEWPORT_MARGIN - MIN_PANEL_HEIGHT
    );
    panelStyle.value = {
      ...base,
      top: `${Math.round(topEdge)}px`,
      bottom: 'auto',
      maxHeight: `${Math.round(Math.min(vh - VIEWPORT_MARGIN - topEdge, maxCapHeight))}px`
    };
  }
}

// 弹窗打开后位置/高度钉死（只在 open 时计算一次），滚动不再重算位置：
// - 外部滚动（对话区/页面）：一律关闭弹窗
// - 内部滚动（diff 内容）：钉住弹窗放行，hover 弹窗也关闭
// - 滚动/缩放关闭后短暂抑制重开，避免弹窗消失瞬间鼠标落在文件行上导致「关了又开」
let suppressOpenUntil = 0;

function onScrollCapture(e: Event) {
  if (!activePath.value) return;
  const target = e.target as Element | null;
  const isInsidePanel = target instanceof Element && !!target.closest('.es-modal');
  if (isInsidePanel && pinned.value) return;
  suppressOpenUntil = Date.now() + 250;
  close();
}

function onResizeClose() {
  if (!activePath.value) return;
  suppressOpenUntil = Date.now() + 250;
  close();
}

function clearOpenTimer() {
  if (openTimer) {
    clearTimeout(openTimer);
    openTimer = null;
  }
}

function clearCloseTimer() {
  if (closeTimer) {
    clearTimeout(closeTimer);
    closeTimer = null;
  }
}

function open(path: string, pin: boolean, rowEl: HTMLElement | null = null) {
  clearOpenTimer();
  clearCloseTimer();
  activePath.value = path;
  pinned.value = pin;
  anchorEl.value = rowEl;
  computePanelStyle();
}

// hover 打开意图延迟；弹窗已打开时切换到另一行则瞬间切换，不等待此延迟
const HOVER_OPEN_DELAY = 600;

function scheduleOpen(path: string, rowEl: HTMLElement | null) {
  clearOpenTimer();
  if (Date.now() < suppressOpenUntil) return;
  if (activePath.value) {
    // 弹窗已打开：鼠标移到另一行，瞬间切换（重新定位 + 换内容）
    open(path, false, rowEl);
    return;
  }
  openTimer = setTimeout(() => open(path, false, rowEl), HOVER_OPEN_DELAY);
}

function scheduleClose() {
  if (pinned.value) return;
  clearCloseTimer();
  closeTimer = setTimeout(() => {
    activePath.value = null;
  }, 200);
}

function onRowMouseEnter(path: string, e: MouseEvent) {
  if (isMobile.value) return;
  clearCloseTimer();
  scheduleOpen(path, (e.currentTarget as HTMLElement) || null);
}

function onRowMouseLeave() {
  if (isMobile.value) return;
  clearOpenTimer();
  scheduleClose();
}

function onPanelMouseEnter() {
  if (!isMobile.value) clearCloseTimer();
}

function onPanelMouseLeave() {
  if (!isMobile.value) scheduleClose();
}

function onRowClick(path: string, e: MouseEvent) {
  // 点击 = 钉住（桌面与移动端一致；移动端无 hover，这是唯一入口）
  open(path, true, (e.currentTarget as HTMLElement) || null);
}

function close() {
  clearOpenTimer();
  clearCloseTimer();
  pinned.value = false;
  activePath.value = null;
  anchorEl.value = null;
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && activePath.value) {
    close();
  }
}

watch(activePath, (v) => {
  if (v) {
    window.addEventListener('keydown', onKeydown);
    // 窗口缩放：直接关闭（钉死位置可能跑出视口），不做重算
    window.addEventListener('resize', onResizeClose);
    // 捕获阶段监听滚动：外部滚动一律关闭，钉住弹窗内部滚动放行
    window.addEventListener('scroll', onScrollCapture, true);
  } else {
    window.removeEventListener('keydown', onKeydown);
    window.removeEventListener('resize', onResizeClose);
    window.removeEventListener('scroll', onScrollCapture, true);
  }
});

onBeforeUnmount(() => {
  clearOpenTimer();
  clearCloseTimer();
  window.removeEventListener('keydown', onKeydown);
  window.removeEventListener('resize', onResizeClose);
  window.removeEventListener('scroll', onScrollCapture, true);
});

// ---------------- diff 行渲染辅助 ----------------
function lineClass(line: any): string {
  if (line.type === 'add') return 'es-diff-add';
  if (line.type === 'remove') return 'es-diff-remove';
  return 'es-diff-context';
}

function lineMarker(line: any): string {
  if (line.type === 'add') return '+';
  if (line.type === 'remove') return '-';
  return '';
}

function lineNumber(line: any): string {
  const n = line.type === 'remove' ? line.old_no : line.new_no;
  return typeof n === 'number' && Number.isFinite(n) ? String(n) : '';
}
</script>

<template>
  <div v-if="files.length" class="edit-summary-card" ref="cardRef">
    <div class="edit-summary-card__header">
      <span
        class="icon icon-sm edit-summary-card__icon"
        :style="props.iconStyle('filePen')"
        aria-hidden="true"
      ></span>
      <span class="edit-summary-card__title">{{ $t('chat.filesEdited', { n: files.length }) }}</span>
      <span class="edit-summary-card__totals">
        <span class="edit-summary-plus">+{{ totals.added }}</span>
        <span class="edit-summary-minus">-{{ totals.removed }}</span>
      </span>
    </div>
    <div class="edit-summary-card__list">
      <div
        v-for="file in files"
        :key="file.path"
        class="edit-summary-card__row"
        :class="{ 'is-active': pinned && activePath === file.path }"
        @mouseenter="onRowMouseEnter(file.path, $event)"
        @mouseleave="onRowMouseLeave"
        @click.stop="onRowClick(file.path, $event)"
      >
        <span class="edit-summary-card__path" :title="file.path">{{ file.path }}</span>
        <span class="edit-summary-card__delta">
          <span class="edit-summary-plus">+{{ file.added || 0 }}</span>
          <span class="edit-summary-minus">-{{ file.removed || 0 }}</span>
        </span>
      </div>
    </div>

    <!-- 居中 diff 弹窗：桌面 hover 非模态，钉住/移动端带透明点击层（点击外部关闭，不虚化周围） -->
    <teleport to="body">
      <div v-if="activeFile" class="es-modal">
        <div
          v-if="pinned || isMobile"
          class="es-modal__overlay"
          aria-hidden="true"
          @click="close"
        ></div>
        <div
          class="es-modal__panel"
          :style="panelStyle"
          role="dialog"
          :aria-label="$t('chat.fileChanges', { path: activeFile.path })"
          @mouseenter="onPanelMouseEnter"
          @mouseleave="onPanelMouseLeave"
        >
          <div class="es-modal__header">
            <span
              class="icon icon-sm es-modal__header-icon"
              :style="props.iconStyle('filePen')"
              aria-hidden="true"
            ></span>
            <span class="es-modal__path" :title="activeFile.path">{{ activeFile.path }}</span>
            <span class="es-modal__delta">
              <span class="edit-summary-plus">+{{ activeFile.added || 0 }}</span>
              <span class="edit-summary-minus">-{{ activeFile.removed || 0 }}</span>
            </span>
            <button
              v-if="pinned || isMobile"
              type="button"
              class="es-modal__close"
              :aria-label="$t('common.close')"
              @click.stop="close"
            >
              <span class="icon icon-sm" :style="props.iconStyle('x')" aria-hidden="true"></span>
            </button>
          </div>
          <div class="es-modal__body">
            <template v-if="(activeFile.lines || []).length">
              <template v-for="(line, i) in activeFile.lines" :key="i">
                <div v-if="line.type === 'sep'" class="es-diff-sep" aria-hidden="true">⋮</div>
                <div v-else class="es-diff-line" :class="lineClass(line)">
                  <span class="es-diff-line-number">{{ lineNumber(line) }}</span>
                  <span class="es-diff-marker">{{ lineMarker(line) }}</span>
                  <span class="es-diff-content">{{ line.content }}</span>
                </div>
              </template>
              <div v-if="activeFile.truncated" class="es-diff-note">{{ $t('chat.diffTruncated') }}</div>
            </template>
            <div v-else class="es-diff-empty">{{ $t('chat.diffEmpty') }}</div>
          </div>
        </div>
      </div>
    </teleport>
  </div>
</template>

<style>
/* ===== 卡片本体（消息流内嵌） ===== */
.edit-summary-card {
  margin-top: 10px;
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-raised);
  overflow: hidden;
}

.edit-summary-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  border-bottom: 1px solid var(--border-default);
}

.edit-summary-card__icon {
  color: var(--text-secondary);
  flex: none;
}

.edit-summary-card__title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.edit-summary-card__totals {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.edit-summary-plus {
  color: var(--state-success);
}

.edit-summary-minus {
  color: var(--state-danger);
}

/* 列表最多显示 5 行（32px/行），超出内部滚动；滚动条隐藏（同 QuickDock 惯例） */
.edit-summary-card__list {
  max-height: 160px;
  overflow-y: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.edit-summary-card__list::-webkit-scrollbar {
  display: none;
}

.edit-summary-card__row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 12px;
  cursor: pointer;
  font-size: 12.5px;
}

/* is-active 仅「点击钉住」时挂载，用于标识正在查看的文件；hover 预览不挂，
   否则鼠标离开后会被 200ms 关闭计时器拖住，看起来像 hover 消失有延迟 */
.edit-summary-card__row:hover,
.edit-summary-card__row.is-active {
  /* 用全局 hover token：深色下是白色微量 tint（提亮），避免 surface-soft 的近黑衬底 */
  background: var(--hover-bg);
}

.edit-summary-card__path {
  flex: 1;
  min-width: 0;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.edit-summary-card__delta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

/* ===== 居中 diff 弹窗 ===== */
.es-modal {
  position: fixed;
  inset: 0;
  z-index: 1200;
  pointer-events: none;
}

.es-modal__overlay {
  position: absolute;
  inset: 0;
  pointer-events: auto;
}

.es-modal__panel {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(720px, calc(100vw - 32px));
  max-height: min(70vh, 640px);
  display: flex;
  flex-direction: column;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  box-shadow: var(--shadow-strong);
  overflow: hidden;
  pointer-events: auto;
}

.es-modal__header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px;
  flex: none;
  /* 与版本管理弹窗一致：头部不加衬底，靠分隔线区分（深色下避免近黑色块） */
  border-bottom: 1px solid var(--border-default);
}

.es-modal__header-icon {
  color: var(--text-secondary);
  flex: none;
}

.es-modal__path {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.es-modal__delta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.es-modal__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: none;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0;
}

.es-modal__close:hover {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.es-modal__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px 0;
  font-size: 12.5px;
  line-height: 1.6;
}

/* ===== diff 行（与文件编辑工具结果 / 版本管理弹窗同一视觉体系） ===== */
.es-diff-line {
  display: flex;
  align-items: flex-start;
  min-height: 20px;
  padding-right: 12px;
}

.es-diff-line-number {
  width: 44px;
  flex: none;
  padding-right: 8px;
  text-align: right;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  user-select: none;
}

.es-diff-marker {
  width: 16px;
  flex: none;
  text-align: center;
  color: var(--text-secondary);
  user-select: none;
}

.es-diff-content {
  flex: 1;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--text-primary);
}

.es-diff-add {
  background: var(--diff-add-bg);
}

.es-diff-add .es-diff-marker {
  color: var(--state-success);
}

.es-diff-remove {
  background: var(--diff-del-bg);
}

.es-diff-remove .es-diff-marker {
  color: var(--state-danger);
}

.es-diff-sep {
  padding: 2px 0 2px 60px;
  color: var(--text-secondary);
  user-select: none;
}

.es-diff-empty,
.es-diff-note {
  padding: 12px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* 移动端：弹窗更宽、行号列收窄 */
@media (max-width: 768px) {
  .es-modal__panel {
    width: calc(100vw - 24px);
    max-height: 76vh;
  }

  .es-diff-line-number {
    width: 34px;
  }

  .es-diff-sep {
    padding-left: 50px;
  }
}
</style>

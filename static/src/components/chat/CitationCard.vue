<script lang="ts">
// 文件摘要懒加载缓存：模块级，卡片实例之间共享（重新打开同一引用不再请求）
import { ref as moduleRef } from 'vue';

const snippetCache = moduleRef<Record<string, string>>({});
const pendingSnippets = new Set<string>();

const IMAGE_EXTS = new Set([
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
  '.svg',
  '.bmp',
  '.ico',
  '.avif'
]);

const FILE_ICON_SVG =
  '<svg class="chip-file-icon" viewBox="0 0 16 16" fill="none">' +
  '<path d="M4 1.75h5.25L12.5 5v9.25H4z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>' +
  '<path d="M9.25 1.75V5h3.25" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';

/** 视口留边、距引用间距、默认最大高度（DESIGN.md §6.3） */
const EDGE = 12;
const GAP = 5;
const MAX_HEIGHT = 320;
/** 翻页滑入与高度过渡：120ms 进入缓动（§6.10） */
const PAGE_MOTION = '120ms cubic-bezier(0.2, 0.8, 0.2, 1)';
</script>

<script setup lang="ts">
/**
 * 行内引用悬停卡（单张）。CitationPopover 每次打开都渲染一个新实例，
 * 切换引用时旧实例沿原路收回、新实例在新引用处展开。
 *
 * - 来源行（图标 + 域名／路径）即打开入口：网页 → 新标签页；文件 → 右侧预览面板。
 * - 多来源时来源行右侧翻页；翻页时内容沿方向滑入，卡片高度同步过渡。
 * - 卡片在引用上方时（上翻）来源行移到底部、卡片以底边锚定：
 *   高度变化只移动远离引用的一边，翻页按钮始终留在指针下面。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { useQuickDockStore } from '@/stores/quickDock';
import { useUiStore } from '@/stores/ui';
import { withWorkspaceScope } from '@/app/methods/workspaceRequestScope';
import {
  keepCitationPopover,
  leaveCitationPopover,
  closeCitationPopover,
  type CitationAnnotation,
  type CitationCardState
} from './citationChips';
import { faviconFallbackHtml, upgradeCitationFavicons } from './citationFavicon';

defineOptions({ name: 'CitationCard' });

const props = defineProps<{ card: CitationCardState }>();

const quickDock = useQuickDockStore();
const uiStore = useUiStore();

const rootEl = ref<HTMLElement | null>(null);
/** 卡片在引用上方；首次定位时确定，翻页重排保持不变 */
const up = ref(false);
let directionDecided = false;

const currentIndex = ref(0);
/** 最近一次翻页方向（-1 / 1）；0 表示刚打开，不播放滑入 */
const pageDir = ref(0);
const resizing = ref(false);
let endResize: (() => void) | null = null;

const annotations = computed(() => props.card.annotations);
const multi = computed(() => annotations.value.length > 1);
const current = computed<CitationAnnotation | null>(() => {
  const list = annotations.value;
  return list[Math.min(currentIndex.value, list.length - 1)] ?? null;
});

// 右侧文件预览面板移动端不渲染（App.vue 门禁），此处同步门禁
const canPreviewFile = computed(() => !uiStore.isMobileViewport);

function isFile(ann: CitationAnnotation) {
  return ann.type === 'file_citation';
}

function isImageFile(ann: CitationAnnotation): boolean {
  if (!isFile(ann)) return false;
  const p = (ann.file_path || ann.file_name || '').toLowerCase();
  const dot = p.lastIndexOf('.');
  return dot >= 0 && IMAGE_EXTS.has(p.slice(dot));
}

/** /api/file/content 的 inline 白名单含 image/，可直接作为 <img> 源 */
function fileContentUrl(ann: CitationAnnotation): string {
  return withWorkspaceScope(`/api/file/content?path=${encodeURIComponent(ann.file_path || '')}`);
}

function iconHtml(ann: CitationAnnotation): string {
  // 先渲染首字母兜底，真实 favicon 由 upgradeCitationFavicons 竞速成功后原地替换
  return isFile(ann) ? FILE_ICON_SVG : faviconFallbackHtml(ann.domain || '');
}

function sourceText(ann: CitationAnnotation): string {
  return isFile(ann)
    ? ann.file_path || ann.file_name || ''
    : (ann.domain || '').replace(/^www\./, '');
}

function titleText(ann: CitationAnnotation): string {
  return ann.title || ann.file_name || ann.url || '';
}

function locatorText(ann: CitationAnnotation): string {
  const bits: string[] = [];
  if (ann.page != null) bits.push(t('chat.citationPage', { page: ann.page }));
  if (ann.line_start != null) {
    bits.push(
      ann.line_end != null && ann.line_end !== ann.line_start
        ? t('chat.citationLines', { start: ann.line_start, end: ann.line_end })
        : t('chat.citationLine', { line: ann.line_start })
    );
  }
  return bits.join(' · ');
}

/** 标题下的次文字：网页为完整网址，文件为页码／行号 */
function metaText(ann: CitationAnnotation): string {
  return isFile(ann) ? locatorText(ann) : ann.url || '';
}

function displaySnippet(ann: CitationAnnotation): string {
  return ann.snippet || snippetCache.value[ann.id] || '';
}

/** 按行号引用的文件片段是源码／文本行，用等宽字体保留换行 */
function isCodeSnippet(ann: CitationAnnotation): boolean {
  return isFile(ann) && ann.page == null;
}

const sourceKind = computed<'url' | 'file' | 'static'>(() => {
  const ann = current.value;
  if (!ann) return 'static';
  if (!isFile(ann)) return ann.url ? 'url' : 'static';
  return ann.file_path && canPreviewFile.value ? 'file' : 'static';
});

function openFile() {
  const path = current.value?.file_path;
  if (!path || !canPreviewFile.value) return;
  quickDock.openPreview(path);
  closeCitationPopover();
}

/** 点击引用图片缩略图：打开全局灯箱预览 */
function openImagePreview(ann: CitationAnnotation) {
  uiStore.openImagePreview({ url: fileContentUrl(ann), name: ann.file_name || '' });
}

/* ---------- 文件内容片段懒加载 ----------
 * 后端落库时已富化 snippet；但旧消息（富化前持久化）与流式期的临时 annotation
 * 没有 snippet，这里在卡片打开时按 file_path 拉一次内容补齐（多来源时并发预取全部条目）。 */
async function maybeFetchSnippet(ann: CitationAnnotation) {
  if (!isFile(ann) || isImageFile(ann) || ann.snippet || !ann.file_path) return;
  if (snippetCache.value[ann.id] || pendingSnippets.has(ann.id)) return;
  pendingSnippets.add(ann.id);
  try {
    const resp = await fetch(fileContentUrl(ann));
    if (!resp.ok) return;
    let text = await resp.text();
    // 二进制守卫：空字节或大量替换字符则放弃
    if (text.includes('\x00')) return;
    const bad = (text.match(/�/g) || []).length;
    if (bad > Math.max(4, text.length * 0.005)) return;
    if (ann.line_start != null) {
      const lines = text.split('\n');
      const s = Math.max(1, ann.line_start);
      const e = Math.min(lines.length, ann.line_end ?? s);
      text = lines.slice(s - 1, e).join('\n');
    }
    // 与后端富化一致：逐行去首尾空白、去空行，截断 300 字
    text = text
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .join('\n')
      .slice(0, 300);
    if (text) snippetCache.value = { ...snippetCache.value, [ann.id]: text };
  } catch {
    /* 读取失败静默：卡片退化为仅路径／定位信息 */
  } finally {
    pendingSnippets.delete(ann.id);
  }
}

/* ---------- 定位 ---------- */
function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** 同 UiPopover：距引用 5px，空间不足上翻，水平夹取在视口内；位移方向与变换原点同源。
 *  上翻时用 bottom 锚定，贴引用的一边固定。 */
function place() {
  const el = rootEl.value;
  const anchor = props.card.anchor;
  if (!el || !anchor.isConnected) return;
  const rect = anchor.getBoundingClientRect();
  const width = el.offsetWidth;
  const left = Math.min(Math.max(EDGE, rect.left), window.innerWidth - width - EDGE);
  el.style.left = `${left}px`;
  el.style.maxHeight = '';
  const below = window.innerHeight - rect.bottom - EDGE;
  const above = rect.top - EDGE;
  if (!directionDecided) {
    const height = el.offsetHeight;
    up.value = below < height && above > below;
    directionDecided = true;
  }
  const available = Math.max(64, (up.value ? above : below) - GAP);
  el.style.maxHeight = `${Math.min(MAX_HEIGHT, window.innerHeight * 0.65, available)}px`;
  if (up.value) {
    el.style.top = 'auto';
    el.style.bottom = `${window.innerHeight - rect.top + GAP}px`;
  } else {
    el.style.bottom = 'auto';
    el.style.top = `${rect.bottom + GAP}px`;
  }
  el.style.setProperty('--cp-offset', up.value ? '4px' : '-4px');
  const originX = Math.min(width, Math.max(0, rect.left + rect.width / 2 - left));
  el.style.transformOrigin = `${originX}px ${up.value ? 'bottom' : 'top'}`;
}

/* ---------- 翻页：内容滑入 + 高度过渡 ---------- */
async function stepPager(delta: number) {
  const list = annotations.value;
  const next = Math.min(Math.max(currentIndex.value + delta, 0), list.length - 1);
  const el = rootEl.value;
  if (next === currentIndex.value || !el) return;
  // offsetHeight 不受进场 scale 影响；过渡中途取到的是当前值，连续翻页接着走不跳回
  const from = el.offsetHeight;
  endResize?.();
  pageDir.value = delta > 0 ? 1 : -1;
  currentIndex.value = next;
  await nextTick();
  if (rootEl.value !== el) return;
  place();
  upgradeCitationFavicons(el);
  const to = el.offsetHeight;
  if (prefersReducedMotion() || Math.abs(from - to) < 1) return;
  // 过渡期间盒子与内容高度不一致，暂停纵向滚动，避免闪出滚动条
  resizing.value = true;
  el.style.height = `${from}px`;
  void el.offsetHeight; // 先落定起点再开过渡
  el.style.transition = `height ${PAGE_MOTION}`;
  el.style.height = `${to}px`;
  const onEnd = (e: TransitionEvent) => {
    if (e.target === el && e.propertyName === 'height') endResize?.();
  };
  endResize = () => {
    el.removeEventListener('transitionend', onEnd);
    el.style.transition = '';
    el.style.height = '';
    resizing.value = false;
    endResize = null;
  };
  el.addEventListener('transitionend', onEnd);
}

onMounted(() => {
  annotations.value.forEach((ann) => void maybeFetchSnippet(ann));
  place();
  if (rootEl.value) upgradeCitationFavicons(rootEl.value);
});

onBeforeUnmount(() => endResize?.());
</script>

<template>
  <div
    ref="rootEl"
    class="citation-popover"
    :class="{ 'is-up': up, 'is-resizing': resizing }"
    role="dialog"
    @mouseenter="keepCitationPopover"
    @mouseleave="leaveCitationPopover"
    @click.stop
  >
    <div v-if="current" class="cp-head">
      <a
        v-if="sourceKind === 'url'"
        class="cp-source is-link"
        :href="current.url"
        target="_blank"
        rel="noopener"
        @click="closeCitationPopover"
      >
        <span class="cp-icon" v-html="iconHtml(current)"></span>
        <span class="cp-source-text">{{ sourceText(current) }}</span>
        <svg class="cp-go" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M5.5 4.5h6v6M11.5 4.5l-7 7" />
        </svg>
      </a>
      <button
        v-else-if="sourceKind === 'file'"
        type="button"
        class="cp-source is-link"
        @click="openFile"
      >
        <span class="cp-icon" v-html="iconHtml(current)"></span>
        <span class="cp-source-text">{{ sourceText(current) }}</span>
        <svg class="cp-go" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M5.5 4.5h6v6M11.5 4.5l-7 7" />
        </svg>
      </button>
      <span v-else class="cp-source">
        <span class="cp-icon" v-html="iconHtml(current)"></span>
        <span class="cp-source-text">{{ sourceText(current) }}</span>
      </span>

      <span v-if="multi" class="cp-pager">
        <button
          type="button"
          class="cp-pager-btn"
          :disabled="currentIndex <= 0"
          :aria-label="t('chat.citationPrev')"
          @click="stepPager(-1)"
        >
          <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="m10 3.5-4.5 4.5 4.5 4.5" />
          </svg>
        </button>
        <span class="cp-pager-count">{{ currentIndex + 1 }}/{{ annotations.length }}</span>
        <button
          type="button"
          class="cp-pager-btn"
          :disabled="currentIndex >= annotations.length - 1"
          :aria-label="t('chat.citationNext')"
          @click="stepPager(1)"
        >
          <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="m6 3.5 4.5 4.5-4.5 4.5" />
          </svg>
        </button>
      </span>
    </div>

    <div class="cp-scroll">
      <div
        v-if="current"
        :key="currentIndex"
        class="cp-body"
        :class="{ 'is-paging': pageDir !== 0 }"
        :style="pageDir !== 0 ? { '--cp-page-dir': `${pageDir * 8}px` } : undefined"
      >
        <div class="cp-title">{{ titleText(current) }}</div>
        <div v-if="metaText(current)" class="cp-meta">{{ metaText(current) }}</div>
        <img
          v-if="isImageFile(current)"
          class="cp-image"
          :src="fileContentUrl(current)"
          :alt="current.file_name || ''"
          loading="lazy"
          @click.stop="openImagePreview(current)"
        />
        <div
          v-else-if="displaySnippet(current)"
          class="cp-snippet"
          :class="{ 'is-code': isCodeSnippet(current) }"
          v-text="displaySnippet(current)"
        ></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 浮层表面（DESIGN.md §6.3）：不透明、中性投影、R12、内边距 4；界面外框用系统无衬线 */
.citation-popover {
  position: fixed;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 320px;
  max-width: calc(100vw - 24px);
  padding: 4px;
  overflow: hidden;
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  background: var(--popover-surface);
  box-shadow: var(--popover-shadow);
  color: var(--text-primary);
  font-family:
    -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* 进出（§6.10）：140 / 100ms，位移朝向引用，变换原点对准引用中心；类名由 CitationPopover 的 Transition 挂上 */
.citation-popover.is-entering {
  animation: cp-in 140ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.citation-popover.is-leaving {
  pointer-events: none;
  animation: cp-out 100ms cubic-bezier(0.4, 0, 1, 1) both;
}

@keyframes cp-in {
  from {
    opacity: 0;
    transform: translateY(var(--cp-offset)) scale(0.98);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes cp-out {
  from {
    opacity: 1;
    transform: none;
  }

  to {
    opacity: 0;
    transform: translateY(var(--cp-offset)) scale(0.98);
  }
}

/* 来源行：32 高；来源链接 + 右侧翻页 */
.cp-head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
  height: 32px;
}

/* 上翻时来源行移到底部、贴着引用；高度过渡中盒子比内容高时也贴住底边 */
.citation-popover.is-up .cp-head {
  order: 2;
  margin-top: auto;
}

/* 来源即打开入口：32 高 R8（贴内边距 4，与外框 R12 同心）；hover 底色只贴自身宽度 */
.cp-source {
  display: inline-flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-tertiary);
  font: inherit;
  font-size: 12px;
  text-decoration: none;
}

.cp-source.is-link {
  cursor: pointer;
  transition:
    background-color 140ms ease,
    color 140ms ease;
}

.cp-source.is-link:hover,
.cp-source.is-link:focus-visible {
  outline: none;
  background: var(--hover-bg);
  color: var(--text-primary);
}

.cp-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
}

.cp-source-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cp-go {
  flex: none;
  width: 12px;
  height: 12px;
  margin-left: -2px;
  stroke: currentcolor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* 翻页按钮 32×32 R8：贴内边距 4，与外框同心 */
.cp-pager {
  display: inline-flex;
  flex: none;
  align-items: center;
  margin-left: auto;
}

.cp-pager-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background-color 140ms ease,
    color 140ms ease;
}

.cp-pager-btn:hover:not(:disabled) {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.cp-pager-btn:focus-visible {
  outline: 2px solid var(--text-secondary);
  outline-offset: -2px;
}

.cp-pager-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.cp-pager-btn svg {
  width: 16px;
  height: 16px;
  stroke: currentcolor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.cp-pager-count {
  min-width: 28px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-tertiary);
  text-align: center;
}

/* 正文：只有这里滚动，4px 细滚动条；横向必须 hidden（翻页滑入的 ±8px 不能触发横向滚动条） */
.cp-scroll {
  flex: 0 1 auto;
  min-height: 0;
  overflow: hidden auto;
  overscroll-behavior: contain;
}

.citation-popover.is-resizing .cp-scroll {
  overflow-y: hidden;
}

.cp-scroll::-webkit-scrollbar {
  width: 4px;
}

.cp-scroll::-webkit-scrollbar-thumb {
  border-radius: 4px;
  background: var(--border-strong);
}

.cp-body {
  padding: 2px 8px 8px;
}

.citation-popover.is-up .cp-body {
  padding: 8px 8px 2px;
}

.cp-body.is-paging {
  animation: cp-page 120ms cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

@keyframes cp-page {
  from {
    opacity: 0;
    transform: translateX(var(--cp-page-dir));
  }

  to {
    opacity: 1;
    transform: none;
  }
}

.cp-title {
  display: -webkit-box;
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
  color: var(--text-primary);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.cp-meta {
  margin-top: 2px;
  overflow: hidden;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-tertiary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cp-snippet {
  display: -webkit-box;
  margin-top: 8px;
  overflow: hidden;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
  white-space: pre-line;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}

.cp-snippet.is-code {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  white-space: pre;
  -webkit-line-clamp: 6;
}

.cp-image {
  display: block;
  max-width: 100%;
  max-height: 180px;
  margin-top: 8px;
  border-radius: 8px;
  cursor: zoom-in;
}

/* 来源图标：favicon 16 R4；字母兜底中性灰阶（不用 --accent 铺底） */
.cp-icon :deep(.chip-favicon) {
  width: 16px;
  height: 16px;
  border-radius: 4px;
}

.cp-icon :deep(.chip-file-icon) {
  width: 16px;
  height: 16px;
}

.cp-icon :deep(.chip-letter) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  background: var(--border-strong);
  color: var(--text-primary);
  font-size: 10px;
  font-weight: 700;
}

@media (prefers-reduced-motion: reduce) {
  .citation-popover.is-entering,
  .citation-popover.is-leaving,
  .cp-body.is-paging {
    animation: none;
  }
}
</style>

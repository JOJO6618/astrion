<template>
  <teleport to="body">
    <div
      v-if="citationPopover.visible"
      ref="popoverEl"
      class="citation-popover"
      :style="popoverStyle"
      @mouseenter="keepCitationPopover"
      @mouseleave="leaveCitationPopover"
      @click.stop
    >
      <!-- 单来源：完整卡片 -->
      <template v-if="single">
        <div
          class="pop-header"
          :class="{ clickable: isFile(single) && canPreviewFile }"
          @click="onHeaderClick(single)"
        >
          <span class="pop-icon" v-html="iconHtml(single)"></span>
          <span class="pop-domain">{{ headerText(single) }}</span>
        </div>
        <div class="pop-scroll">
          <div class="pop-body">
            <div class="pop-title-row">
              <div class="pop-title">{{ single.title || single.file_name || '' }}</div>
              <span v-if="locatorText(single)" class="pop-locator">{{ locatorText(single) }}</span>
            </div>
            <div v-if="single.url" class="pop-url">{{ single.url }}</div>
            <div v-if="isImageFile(single)" class="pop-image">
              <img
                :src="fileContentUrl(single)"
                :alt="single.file_name || ''"
                loading="lazy"
                @click.stop="openCitationPreview(single)"
              />
            </div>
            <div v-else-if="displaySnippet(single)" class="pop-snippet">“{{ displaySnippet(single) }}”</div>
          </div>
        </div>
        <div v-if="hasFooterAction" class="pop-footer">
          <a
            v-if="!isFile(single) && single.url"
            class="pop-open"
            :href="single.url"
            target="_blank"
            rel="noopener"
          >{{ t('chat.citationOpenSource') }} ↗</a>
          <span
            v-else-if="isFile(single) && hostMode"
            class="pop-open"
            @click="openOnComputer(single)"
          >{{ t('quickdock.menuRevealInManager') }}</span>
        </div>
      </template>

      <!-- 多来源：分页卡片（内容区与单来源一致，头部右侧左右箭头切换） -->
      <template v-else>
        <div
          class="pop-header"
          :class="{ clickable: current && isFile(current) && canPreviewFile }"
          @click="onHeaderClick(current)"
        >
          <span class="pop-icon" v-html="iconHtml(current)"></span>
          <span class="pop-domain">{{ headerText(current) }}</span>
          <span class="pop-pager">
            <button
              type="button"
              class="pop-pager-btn"
              :disabled="currentIndex <= 0"
              :aria-label="t('chat.citationPrev')"
              @click.stop="stepPager(-1)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 6-6 6 6 6" /></svg>
            </button>
            <span class="pop-pager-count">{{ currentIndex + 1 }}/{{ citationPopover.annotations.length }}</span>
            <button
              type="button"
              class="pop-pager-btn"
              :disabled="currentIndex >= citationPopover.annotations.length - 1"
              :aria-label="t('chat.citationNext')"
              @click.stop="stepPager(1)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
            </button>
          </span>
        </div>
        <div class="pop-scroll">
          <div class="pop-body">
            <div class="pop-title-row">
              <div class="pop-title">{{ current.title || current.file_name || '' }}</div>
              <span v-if="locatorText(current)" class="pop-locator">{{ locatorText(current) }}</span>
            </div>
            <div v-if="current.url" class="pop-url">{{ current.url }}</div>
            <div v-if="isImageFile(current)" class="pop-image">
              <img
                :src="fileContentUrl(current)"
                :alt="current.file_name || ''"
                loading="lazy"
                @click.stop="openCitationPreview(current)"
              />
            </div>
            <div v-else-if="displaySnippet(current)" class="pop-snippet">“{{ displaySnippet(current) }}”</div>
          </div>
        </div>
        <div v-if="hasFooterAction" class="pop-footer">
          <a
            v-if="!isFile(current) && current.url"
            class="pop-open"
            :href="current.url"
            target="_blank"
            rel="noopener"
          >{{ t('chat.citationOpenSource') }} ↗</a>
          <span
            v-else-if="isFile(current) && hostMode"
            class="pop-open"
            @click="openOnComputer(current)"
          >{{ t('quickdock.menuRevealInManager') }}</span>
        </div>
      </template>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue';
import { t } from '@/locales';
import { useQuickDockStore } from '@/stores/quickDock';
import { useUiStore } from '@/stores/ui';
import {
  citationPopover,
  keepCitationPopover,
  leaveCitationPopover,
  closeCitationPopover,
  type CitationAnnotation,
} from './citationChips';
import { faviconFallbackHtml, upgradeCitationFavicons } from './citationFavicon';

defineOptions({ name: 'CitationPopover' });

// hostMode：文件 footer 的「在文件管理器中打开」仅宿主机模式可用（docker 无此能力）
const props = defineProps<{ hostMode?: boolean }>();

const quickDock = useQuickDockStore();
const uiStore = useUiStore();

const single = computed<CitationAnnotation | null>(() =>
  citationPopover.annotations.length === 1 ? citationPopover.annotations[0] : null,
);

/** 多来源分页：当前展示的条目索引（弹层打开时重置为 0，箭头步进） */
const currentIndex = ref(0);

/** 始终指向当前展示的条目：单来源 = 唯一一条；多来源 = 分页当前页 */
const current = computed<CitationAnnotation | null>(() => {
  const list = citationPopover.annotations;
  if (!list.length) return null;
  return list[Math.min(currentIndex.value, list.length - 1)] ?? null;
});

function stepPager(delta: number) {
  const list = citationPopover.annotations;
  if (list.length < 2) return;
  const next = Math.min(Math.max(currentIndex.value + delta, 0), list.length - 1);
  if (next === currentIndex.value) return;
  currentIndex.value = next;
  // 内容高度变化后重新定位，并给新条目做 favicon 竞速升级
  void nextTick(() => {
    positionPopover();
    if (popoverEl.value) upgradeCitationFavicons(popoverEl.value);
  });
}

// 底部有动作才渲染 footer：网页=打开来源；文件=在电脑上直接打开（仅宿主机）
const hasFooterAction = computed(() => {
  const s = current.value;
  if (!s) return false;
  if (isFile(s)) return !!props.hostMode;
  return !!s.url;
});

const popoverEl = ref<HTMLElement | null>(null);
const popoverStyle = ref<Record<string, string>>({});

// 右侧文件预览面板移动端不渲染（App.vue 门禁），此处同步门禁
const canPreviewFile = computed(() => !uiStore.isMobileViewport);

function isFile(ann: CitationAnnotation) {
  return ann.type === 'file_citation';
}

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico', '.avif']);

function isImageFile(ann: CitationAnnotation): boolean {
  if (!isFile(ann)) return false;
  const p = (ann.file_path || ann.file_name || '').toLowerCase();
  const dot = p.lastIndexOf('.');
  return dot >= 0 && IMAGE_EXTS.has(p.slice(dot));
}

/** /api/file/content 的 inline 白名单含 image/，可直接作为 <img> 源 */
function fileContentUrl(ann: CitationAnnotation): string {
  return `/api/file/content?path=${encodeURIComponent(ann.file_path || '')}`;
}

/** 点击引用图片缩略图：打开全局灯箱预览 */
function openCitationPreview(ann: CitationAnnotation) {
  uiStore.openImagePreview({
    url: fileContentUrl(ann),
    name: ann.file_name || ''
  });
}

function shortDomain(domain?: string) {
  return (domain || '').replace(/^www\./, '');
}

const FILE_ICON_SVG =
  '<svg class="chip-file-icon" viewBox="0 0 16 16" fill="none">' +
  '<path d="M4 1.5h5.5L13 5v9.5H4z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>' +
  '<path d="M9.5 1.5V5H13" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';

function iconHtml(ann: CitationAnnotation): string {
  if (isFile(ann)) return FILE_ICON_SVG;
  // 先渲染首字母兜底，真实 favicon 由 upgradeCitationFavicons 竞速成功后原地替换
  return faviconFallbackHtml(ann.domain || '');
}

function headerText(ann: CitationAnnotation): string {
  return isFile(ann) ? (ann.file_path || ann.file_name || '') : shortDomain(ann.domain);
}

function locatorText(ann: CitationAnnotation): string {
  const bits: string[] = [];
  if (ann.page != null) bits.push(`p.${ann.page}`);
  if (ann.line_start != null) {
    bits.push(
      ann.line_end != null && ann.line_end !== ann.line_start
        ? `L${ann.line_start}–${ann.line_end}`
        : `L${ann.line_start}`,
    );
  }
  return bits.join(' · ');
}

function openFile(ann: CitationAnnotation) {
  if (!ann.file_path || !canPreviewFile.value) return;
  quickDock.openPreview(ann.file_path);
  closeCitationPopover();
}

/* ---------- 文件内容片段懒加载 ----------
 * 后端落库时已富化 snippet；但旧消息（富化前持久化）与流式期的临时 annotation
 * 没有 snippet，这里在弹层打开时按 file_path 拉一次内容补齐。
 * 多来源时打开瞬间即并发预取全部条目，避免切换箭头时内容闪烁。 */
const snippetCache = ref<Record<string, string>>({});

/** in-flight 去重：同一 id 并发（当前条目 watch + 全量预取）只发一次请求 */
const pendingSnippets = new Set<string>();

function displaySnippet(ann: CitationAnnotation): string {
  return ann.snippet || snippetCache.value[ann.id] || '';
}

async function maybeFetchSnippet(ann: CitationAnnotation) {
  if (!isFile(ann) || isImageFile(ann) || ann.snippet || !ann.file_path || snippetCache.value[ann.id]) return;
  if (pendingSnippets.has(ann.id)) return;
  pendingSnippets.add(ann.id);
  const path = ann.file_path;
  try {
    const resp = await fetch(`/api/file/content?path=${encodeURIComponent(path)}`);
    if (!resp.ok) return;
    let text = await resp.text();
    // 二进制守卫：空字节或大量替换字符则放弃
    if (text.includes('\x00')) return;
    const bad = (text.match(/\uFFFD/g) || []).length;
    if (bad > Math.max(4, text.length * 0.005)) return;
    if (ann.line_start != null) {
      const lines = text.split('\n');
      const s = Math.max(1, ann.line_start);
      const e = Math.min(lines.length, ann.line_end ?? s);
      text = lines.slice(s - 1, e).join('\n');
    }
    // 保留换行（弹层 pre-line 渲染），仅压缩行内空白、去空行
    text = text
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .join('\n')
      .slice(0, 300);
    if (text) {
      snippetCache.value = { ...snippetCache.value, [ann.id]: text };
    }
  } catch {
    /* 读取失败静默：弹层退化为仅路径/定位信息 */
  } finally {
    pendingSnippets.delete(ann.id);
  }
}

/** 打开弹层时并发预取全部条目的文件摘要（图片条目跳过，直接显示预览） */
function prefetchAllSnippets() {
  for (const ann of citationPopover.annotations) {
    void maybeFetchSnippet(ann);
  }
}

watch(
  () => [citationPopover.visible, current.value?.id] as const,
  ([visible]) => {
    if (visible && current.value) maybeFetchSnippet(current.value);
  },
);

/** 文件卡片头部点击 → 复用右侧文件预览面板 */
function onHeaderClick(ann: CitationAnnotation) {
  if (isFile(ann)) openFile(ann);
}

/** 在电脑上直接打开：复用 QuickDock 的链路（系统默认应用 = 候选列表第一个） */
async function openOnComputer(ann: CitationAnnotation) {
  const path = ann.file_path;
  if (!path) return;
  try {
    const resp = await fetch(`/api/project/file-open-apps?path=${encodeURIComponent(path)}`);
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || t('quickdock.revealDetectAppsFailed'));
    }
    const apps = Array.isArray(payload?.data?.apps) ? payload.data.apps : [];
    if (!apps.length) {
      throw new Error(t('quickdock.revealNoAppsFound'));
    }
    const openResp = await fetch('/api/project/open-file-with-app', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, app_id: apps[0].id }),
    });
    const openPayload = await openResp.json().catch(() => ({}));
    if (!openResp.ok || !openPayload?.success) {
      throw new Error(openPayload?.error || t('quickdock.revealOpenFileFailed'));
    }
  } catch (err: any) {
    uiStore.pushToast({ message: err?.message || t('quickdock.revealOpenFileFailed'), type: 'error' });
    return;
  }
  closeCitationPopover();
}

/** 定位：优先放胶囊下方，空间不足翻到上方；水平方向视口内夹取 */
function positionPopover() {
  const anchor = citationPopover.anchor;
  const el = popoverEl.value;
  if (!anchor || !el) return;
  const rect = anchor.getBoundingClientRect();
  const width = 320;
  const maxHeight = 300;
  const height = Math.min(el.offsetHeight || 200, maxHeight);
  let left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
  let top = rect.bottom + 6;
  if (top + height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - height - 6);
  }
  popoverStyle.value = { left: `${left}px`, top: `${top}px` };
}

watch(
  () => citationPopover.visible,
  async (visible) => {
    if (visible) {
      currentIndex.value = 0; // 多来源分页每次打开回到第 1 条
      prefetchAllSnippets(); // 并发预取全部条目摘要，切换时不再闪烁
      await nextTick();
      positionPopover();
      // v-html 渲染出的字母占位统一走多源竞速升级
      if (popoverEl.value) upgradeCitationFavicons(popoverEl.value);
    }
  },
);

const onGlobalClick = (e: MouseEvent) => {
  if (popoverEl.value && !popoverEl.value.contains(e.target as Node)) {
    closeCitationPopover();
  }
};
const onKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape') closeCitationPopover();
};
// 页面一旦滚动就收起（含固定态），符合全局交互约定
const onScroll = () => closeCitationPopover();
const onResize = () => closeCitationPopover();

onMounted(() => {
  document.addEventListener('click', onGlobalClick, true);
  document.addEventListener('keydown', onKeydown);
  window.addEventListener('scroll', onScroll, true);
  window.addEventListener('resize', onResize);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onGlobalClick, true);
  document.removeEventListener('keydown', onKeydown);
  window.removeEventListener('scroll', onScroll, true);
  window.removeEventListener('resize', onResize);
});
</script>

<style scoped>
/* 实体面板：不透明、无 backdrop-filter；中性阴影；颜色全走语义 token */
.citation-popover {
  position: fixed;
  z-index: 1000;
  width: 320px;
  max-height: 300px;
  display: flex;
  flex-direction: column;
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  box-shadow: var(--shadow-card);
  overflow: hidden;
}

.pop-scroll {
  overflow-y: auto;
  min-height: 0;
  scrollbar-width: none;
}
.pop-scroll::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.pop-header {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  flex: none;
  border-bottom: 1px solid var(--border-default);
  font-size: 11px;
  color: var(--text-tertiary);
}
.pop-header.clickable {
  cursor: pointer;
}
.pop-header.clickable:hover {
  color: var(--text-secondary);
}
.pop-icon {
  display: inline-flex;
  align-items: center;
  flex: none;
}
.pop-domain {
  min-width: 0; /* 标题过长时由 ellipsis 截断，给右侧分页器让位 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
/* 多来源分页：左侧 图标+域名，右侧 左右箭头+页码 */
.pop-pager {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}
.pop-pager-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background 0.15s,
    color 0.15s;
}
.pop-pager-btn:hover:not(:disabled) {
  background: var(--hover-bg);
  color: var(--text-primary);
}
.pop-pager-btn:disabled {
  color: var(--text-tertiary);
  opacity: 0.45;
  cursor: default;
}
.pop-pager-btn svg {
  width: 14px;
  height: 14px;
}
.pop-pager-count {
  min-width: 26px;
  text-align: center;
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.pop-body {
  padding: 10px 12px 12px;
}
.pop-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.pop-title {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text-primary);
  min-width: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pop-url {
  font-size: 11.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 8px;
}
.pop-locator {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  background: var(--surface-muted);
  font-size: 11px;
  color: var(--text-secondary);
}
.pop-snippet {
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-secondary);
  white-space: pre-line; /* 保留文件内容换行 */
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pop-image {
  margin-top: 6px;
}
.pop-image img {
  display: block;
  max-width: 100%;
  max-height: 180px;
  border-radius: 6px;
}
.pop-footer {
  flex: none;
  height: 38px;
  padding: 0 12px;
  border-top: 1px solid var(--border-default);
  display: flex;
  align-items: center;
}
.pop-open {
  font-size: 12.5px;
  color: var(--accent);
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  text-decoration: none;
  cursor: pointer;
}
.pop-open:hover {
  color: var(--accent-hover);
  text-decoration: underline;
}

.pop-header :deep(.chip-favicon) {
  width: 12px;
  height: 12px;
  border-radius: 3px;
}
.pop-header :deep(.chip-file-icon) {
  width: 12px;
  height: 12px;
  color: var(--text-tertiary);
}
.pop-header :deep(.chip-letter) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  color: var(--on-accent);
  background: var(--accent);
}
</style>

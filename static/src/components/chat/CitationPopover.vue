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
              <img :src="fileContentUrl(single)" :alt="single.file_name || ''" loading="lazy" />
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

      <!-- 多来源：列表 -->
      <template v-else>
        <div class="pop-header">
          <span class="pop-domain">{{ t('chat.citationSources', { n: citationPopover.annotations.length }) }}</span>
        </div>
        <div class="pop-scroll">
          <div
            v-for="ann in citationPopover.annotations"
            :key="ann.id"
            class="pop-list-row"
            @click="onRowClick(ann)"
          >
            <span class="pop-list-icon" v-html="iconHtml(ann)"></span>
            <span class="pop-list-text">
              <span class="pop-list-title">
                <span class="pop-list-title-text">{{ ann.title || ann.file_name || '' }}</span>
                <span v-if="isFile(ann) && locatorText(ann)" class="pop-locator pop-locator--sm">{{ locatorText(ann) }}</span>
              </span>
              <span class="pop-list-domain">{{ rowSubText(ann) }}</span>
            </span>
          </div>
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

defineOptions({ name: 'CitationPopover' });

// hostMode：文件 footer 的「在文件管理器中打开」仅宿主机模式可用（docker 无此能力）
const props = defineProps<{ hostMode?: boolean }>();

const quickDock = useQuickDockStore();
const uiStore = useUiStore();

const single = computed<CitationAnnotation | null>(() =>
  citationPopover.annotations.length === 1 ? citationPopover.annotations[0] : null,
);

// 底部有动作才渲染 footer：网页=打开来源；文件=在电脑上直接打开（仅宿主机）
const hasFooterAction = computed(() => {
  const s = single.value;
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

function shortDomain(domain?: string) {
  return (domain || '').replace(/^www\./, '');
}

function escapeText(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const FILE_ICON_SVG =
  '<svg class="chip-file-icon" viewBox="0 0 16 16" fill="none">' +
  '<path d="M4 1.5h5.5L13 5v9.5H4z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>' +
  '<path d="M9.5 1.5V5H13" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';

function iconHtml(ann: CitationAnnotation): string {
  if (isFile(ann)) return FILE_ICON_SVG;
  const d = shortDomain(ann.domain);
  const letter = (d[0] || '?').toUpperCase();
  return (
    `<img class="chip-favicon" loading="lazy" alt="" ` +
    `src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&sz=64" ` +
    `onerror="this.outerHTML='<span class=&quot;chip-letter&quot;>${letter}</span>'">`
  );
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

function rowSubText(ann: CitationAnnotation): string {
  // 行号徽章已提到标题行，副行固定显示路径（网页显示域名）
  if (isFile(ann)) return ann.file_path || '';
  return shortDomain(ann.domain);
}

function openFile(ann: CitationAnnotation) {
  if (!ann.file_path || !canPreviewFile.value) return;
  quickDock.openPreview(ann.file_path);
  closeCitationPopover();
}

/* ---------- 文件内容片段懒加载 ----------
 * 后端落库时已富化 snippet；但旧消息（富化前持久化）与流式期的临时 annotation
 * 没有 snippet，这里在弹层打开时按 file_path 拉一次内容补齐。 */
const snippetCache = ref<Record<string, string>>({});

function displaySnippet(ann: CitationAnnotation): string {
  return ann.snippet || snippetCache.value[ann.id] || '';
}

async function maybeFetchSnippet(ann: CitationAnnotation) {
  if (!isFile(ann) || isImageFile(ann) || ann.snippet || !ann.file_path || snippetCache.value[ann.id]) return;
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
  }
}

watch(
  () => [citationPopover.visible, single.value?.id] as const,
  ([visible]) => {
    if (visible && single.value) maybeFetchSnippet(single.value);
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

function onRowClick(ann: CitationAnnotation) {
  if (isFile(ann)) {
    openFile(ann);
  } else if (ann.url) {
    window.open(ann.url, '_blank', 'noopener');
    closeCitationPopover();
  }
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
      await nextTick();
      positionPopover();
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
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

.pop-list-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px;
  cursor: pointer;
}
.pop-list-row:hover {
  background: var(--hover-bg);
}
.pop-list-row + .pop-list-row {
  border-top: 1px solid var(--border-default);
}
.pop-list-icon {
  flex: none;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pop-list-icon :deep(.chip-favicon) {
  width: 14px;
  height: 14px;
}
.pop-list-icon :deep(.chip-file-icon) {
  width: 14px;
  height: 14px;
  color: var(--text-tertiary); /* 显式颜色绑定，stroke=currentColor 才能生效 */
}
.pop-list-text {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}
.pop-list-title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12.5px;
  color: var(--text-primary);
  line-height: 1.3;
}
.pop-list-title-text {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 列表行内的小型行号徽章（44px 行高内容纳得下） */
.pop-locator--sm {
  height: 16px;
  padding: 0 6px;
  font-size: 10px;
}
.pop-list-domain {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.3;
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
.pop-header :deep(.chip-letter),
.pop-list-icon :deep(.chip-letter) {
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

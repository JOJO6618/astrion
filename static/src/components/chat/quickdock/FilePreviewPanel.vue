<template>
  <transition name="qd-preview-slide">
    <aside v-if="previewPath" class="qd-preview" :style="{ width: previewWidth + 'px' }">
      <!-- 左缘拖拽手柄：拖动调整面板宽度，localStorage 持久化 -->
      <div
        class="qd-preview__resize"
        :title="$t('quickdock.resizeWidthHint')"
        @mousedown="startPreviewResize"
      ></div>
      <section class="qd-preview__panel">
        <header class="qd-preview__header">
          <svg viewBox="0 0 16 16" fill="none">
            <path
              d="M4 2.5h5.5L12 5v8.5H4V2.5z"
              stroke="currentColor"
              stroke-width="1.3"
              stroke-linejoin="round"
            />
            <path
              d="M9.5 2.5V5H12"
              stroke="currentColor"
              stroke-width="1.3"
              stroke-linejoin="round"
            />
          </svg>
          <span class="qd-preview__name" :title="previewPath">{{ fileName }}</span>
          <span class="qd-preview__path" :title="previewPath">{{ dirName }}</span>
          <button class="qd-preview__close" :title="$t('common.close')" @click="close">
            <svg viewBox="0 0 16 16" fill="none">
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
              />
            </svg>
          </button>
        </header>
        <div class="qd-preview__body">
          <div v-if="loading" class="qd-preview__loading">{{ $t('common.loading') }}</div>
          <div v-else-if="error" class="qd-preview__error">{{ error }}</div>
          <template v-else>
            <!-- 自动换行模式（设置项开启）：逐行渲染，行号内嵌不会错位，行高亮走 CSS :hover -->
            <div v-if="autoWrap" class="qd-code qd-code--wrap">
              <div v-for="(lineHtml, idx) in wrappedLines" :key="idx" class="qd-code-line">
                <span class="qd-code-line-no" aria-hidden="true">{{ idx + 1 }}</span>
                <span class="qd-code-line-text" v-html="lineHtml"></span>
              </div>
            </div>
            <!-- 不换行（默认）：行号列 + 整段高亮 pre：Prism 的 token 可能跨行（多行注释/字符串）， -->
            <!-- 不能按行拆分 v-html，行 hover 用绝对定位高亮条实现 -->
            <div v-else class="qd-code" @mousemove="onCodeMouseMove" @mouseleave="hoverLine = -1">
              <div
                v-if="hoverLine >= 0"
                class="qd-code-hoverline"
                :style="{ top: `${hoverLine * LINE_HEIGHT}px` }"
              ></div>
              <div class="qd-code-gutter" aria-hidden="true">
                <span v-for="n in lineCount" :key="n">{{ n }}</span>
              </div>
              <pre class="qd-code-pre"><code v-html="highlightedHtml"></code></pre>
            </div>
          </template>
        </div>
      </section>
    </aside>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { t } from '@/locales';
import { useQuickDockStore } from '@/stores/quickDock';
import { usePersonalizationStore } from '@/stores/personalization';
import { highlightCode, prismLangForPath } from '@/utils/prismHighlight';

/** 与 quickdock.css 中 .qd-code 的 font-size(12px) × line-height(1.75) 保持同步 */
const LINE_HEIGHT = 21;
/** 超过该字符数的文件跳过语法高亮（仍显示纯文本与行号），避免大文件解析卡顿 */
const HIGHLIGHT_MAX_CHARS = 100 * 1024;

/**
 * 文件预览侧边栏（占位列，位于快捷窗口列右侧、git/终端面板左侧）
 * 内容走现有 /api/file/content；仅支持白名单内的 UTF-8 文本类文件。
 */

const PREVIEWABLE_EXTS = new Set([
  '.txt',
  '.md',
  '.markdown',
  '.csv',
  '.json',
  '.json5',
  '.yaml',
  '.yml',
  '.js',
  '.ts',
  '.jsx',
  '.tsx',
  '.vue',
  '.py',
  '.rb',
  '.go',
  '.rs',
  '.java',
  '.c',
  '.h',
  '.cpp',
  '.hpp',
  '.cs',
  '.php',
  '.swift',
  '.kt',
  '.sh',
  '.bash',
  '.zsh',
  '.ps1',
  '.bat',
  '.cmd',
  '.html',
  '.htm',
  '.css',
  '.scss',
  '.sass',
  '.less',
  '.xml',
  '.svg',
  '.toml',
  '.ini',
  '.cfg',
  '.conf',
  '.log',
  '.sql'
]);

const quickDock = useQuickDockStore();
const { previewPath } = storeToRefs(quickDock);

/** 预览面板宽度（含右侧 12px padding），localStorage 持久化 */
const PREVIEW_WIDTH_STORAGE_KEY = 'agents_qd_preview_width';
const PREVIEW_WIDTH_MIN = 320;
const PREVIEW_WIDTH_MAX = 860;
const PREVIEW_WIDTH_DEFAULT = 452;

const loadPreviewWidth = (): number => {
  if (typeof window === 'undefined' || !window.localStorage) return PREVIEW_WIDTH_DEFAULT;
  try {
    const raw = Number(window.localStorage.getItem(PREVIEW_WIDTH_STORAGE_KEY));
    if (!Number.isFinite(raw) || raw <= 0) return PREVIEW_WIDTH_DEFAULT;
    return Math.max(PREVIEW_WIDTH_MIN, Math.min(PREVIEW_WIDTH_MAX, raw));
  } catch {
    return PREVIEW_WIDTH_DEFAULT;
  }
};

const previewWidth = ref(loadPreviewWidth());

function startPreviewResize(event: MouseEvent) {
  event.preventDefault();
  const startX = event.clientX;
  const startWidth = previewWidth.value;
  const onMove = (e: MouseEvent) => {
    // 手柄在面板左缘：向左拖变宽、向右拖变窄
    const next = startWidth + (startX - e.clientX);
    previewWidth.value = Math.max(PREVIEW_WIDTH_MIN, Math.min(PREVIEW_WIDTH_MAX, next));
  };
  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    try {
      window.localStorage.setItem(PREVIEW_WIDTH_STORAGE_KEY, String(previewWidth.value));
    } catch {
      /* 持久化失败不影响本次调整 */
    }
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
}

const rawText = ref('');
const loading = ref(false);
const error = ref('');
const hoverLine = ref(-1);

const previewLang = computed(() => {
  if (rawText.value.length > HIGHLIGHT_MAX_CHARS) return null;
  return prismLangForPath(previewPath.value || '');
});

const highlightedHtml = computed(() => {
  const html = highlightCode(rawText.value, previewLang.value);
  // pre 中末尾的单个 \n 不产生视觉空行；文本以换行结尾时补一个，
  // 保证行号列与代码视觉行一一对齐
  return rawText.value.endsWith('\n') ? html + '\n' : html;
});

const lineCount = computed(() => (rawText.value ? rawText.value.split('\n').length : 0));

const personalization = usePersonalizationStore();
/** 设置项「预览窗口自动换行显示」，默认关闭（长行横向滚动） */
const autoWrap = computed(() => personalization.form.file_preview_auto_wrap === true);

// 自动换行模式：逐行拆分 + 逐行高亮。
// 取舍：多行注释/字符串等跨行 token 在该模式下着色可能不准（逐行独立解析），换来行号与换行行的天然对齐。
const wrappedLines = computed<string[]>(() => {
  if (!rawText.value) return [];
  return rawText.value.split('\n').map((line) => highlightCode(line, previewLang.value));
});

const fileName = computed(() => {
  const p = previewPath.value || '';
  return p.split('/').pop() || p;
});

const dirName = computed(() => {
  const p = previewPath.value || '';
  const parts = p.split('/');
  return parts.length > 1 ? `${parts.slice(0, -1).join('/')}/` : '';
});

function extOf(path: string): string {
  const name = path.split('/').pop() || '';
  const idx = name.lastIndexOf('.');
  return idx >= 0 ? name.slice(idx).toLowerCase() : '';
}

let loadSeq = 0;

watch(
  previewPath,
  async (path) => {
    const mySeq = ++loadSeq;
    rawText.value = '';
    error.value = '';
    hoverLine.value = -1;
    if (!path) {
      return;
    }
    if (!PREVIEWABLE_EXTS.has(extOf(path))) {
      error.value = t('quickdock.previewTypeUnsupported');
      return;
    }
    loading.value = true;
    try {
      const resp = await fetch(`/api/file/content?path=${encodeURIComponent(path)}`);
      if (!resp.ok) {
        const payload = await resp.json().catch(() => ({}));
        throw new Error(payload?.error || t('quickdock.loadFailedHttp', { status: resp.status }));
      }
      const text = await resp.text();
      if (mySeq !== loadSeq) {
        return;
      }
      rawText.value = text;
    } catch (err: any) {
      if (mySeq !== loadSeq) {
        return;
      }
      error.value = err?.message || t('common.loadFailed');
    } finally {
      if (mySeq === loadSeq) {
        loading.value = false;
      }
    }
  },
  { immediate: true }
);

function onCodeMouseMove(event: MouseEvent) {
  const el = event.currentTarget as HTMLElement;
  const rect = el.getBoundingClientRect();
  const idx = Math.floor((event.clientY - rect.top) / LINE_HEIGHT);
  hoverLine.value = idx >= 0 && idx < lineCount.value ? idx : -1;
}

function close() {
  quickDock.closePreview();
}
</script>

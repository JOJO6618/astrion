<template>
  <div class="show-file-card">
    <div class="sfc-header">
      <span class="sfc-file-icon" aria-hidden="true"></span>
      <span class="sfc-name" :title="displayName">{{ displayName }}</span>
      <div class="sfc-actions">
        <button
          v-if="isHtmlFile"
          type="button"
          class="sfc-btn"
          :disabled="previewLoading"
          @click="openHtmlPreview"
        >
          {{ previewLoading ? $t('common.loading') : $t('chat.preview') }}
        </button>
        <button v-if="canCopy" type="button" class="sfc-btn" @click="copyContent">
          {{ copied ? $t('common.copied') : $t('common.copy') }}
        </button>
        <button type="button" class="sfc-btn sfc-btn-download" @click="handleDownload">{{ $t('common.download') }}</button>
      </div>
    </div>

    <div class="sfc-preview" :class="{ 'sfc-preview--csv': fileType === 'csv' }" v-if="canPreview">
      <div class="sfc-loading" v-if="loading">
        <span class="sfc-spinner"></span>
        <span>{{ $t('common.loading') }}</span>
      </div>

      <div class="sfc-error" v-else-if="error">{{ error }}</div>

      <template v-else-if="fileType === 'text' || fileType === 'code' || fileType === 'json'">
        <pre class="sfc-code" v-html="highlightedCode"></pre>
      </template>

      <template v-else-if="fileType === 'csv'">
        <div class="sfc-csv-scroll" :class="{ 'sfc-csv-scroll--loading': loading }">
          <table class="sfc-csv-table">
            <thead>
              <tr>
                <th v-for="(col, i) in csvHeader" :key="i">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, ri) in csvRows" :key="ri">
                <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="sfc-csv-meta" v-if="csvTruncated">
          {{ $t('chat.csvTruncated', { n: csvRows.length }) }}
        </div>
      </template>

      <template v-else-if="fileType === 'markdown'">
        <div class="sfc-md" v-html="renderedMarkdown"></div>
      </template>

      <template v-else-if="fileType === 'image'">
        <img
          class="sfc-image"
          :class="{ 'sfc-image--android': isAndroidApp }"
          :src="contentUrl"
          :alt="displayName"
          @click="openFullImage()"
          @error="onImageError"
        />
      </template>

      <template v-else-if="fileType === 'pdf'">
        <PdfPreview :source="contentUrl" />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { highlightCode, prismLangForPath } from '@/utils/prismHighlight';
import { renderMarkdown } from '../../composables/useMarkdownRenderer';
import { buildShowHtmlIframeSrcdoc } from '@/utils/showHtmlSandbox';
import { openShowHtmlFullscreen } from '@/utils/showHtmlFullscreen';
import PdfPreview from './PdfPreview.vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';

// 文本类文件内容缓存，避免父组件反复重建卡片时重复 fetch 导致闪烁
const SHOW_FILE_CONTENT_CACHE = new Map<string, { content: string; ts: number }>();
const SHOW_FILE_CONTENT_CACHE_TTL_MS = 5 * 60 * 1000;

function getCachedShowFileContent(path: string): string | null {
  const cached = SHOW_FILE_CONTENT_CACHE.get(path);
  if (!cached) return null;
  if (Date.now() - cached.ts > SHOW_FILE_CONTENT_CACHE_TTL_MS) {
    SHOW_FILE_CONTENT_CACHE.delete(path);
    return null;
  }
  return cached.content;
}

function setCachedShowFileContent(path: string, content: string) {
  SHOW_FILE_CONTENT_CACHE.set(path, { content, ts: Date.now() });
}

defineOptions({ name: 'ShowFileCard' });

const props = defineProps<{
  path: string;
  name?: string;
  type?: string;
  description?: string;
  preview?: string;
}>();

// ---- 状态 ----
const loading = ref(false);
const error = ref('');
const rawContent = ref('');
const copied = ref(false);
const contentUrl = ref('');
const previewLoading = ref(false);

// ---- 计算属性 ----
const fileType = computed(() => props.type || inferShowFileType(props.path));
const displayName = computed(() => props.name || props.path.split('/').pop() || 'file');

const canPreview = computed(() => {
  if (props.preview === 'off') return false;
  return ['text', 'code', 'json', 'csv', 'markdown', 'image', 'pdf'].includes(fileType.value);
});

const canCopy = computed(() =>
  ['text', 'code', 'json', 'csv', 'markdown'].includes(fileType.value)
);

// HTML 文件额外提供全屏预览入口（卡片内仍按代码高亮展示）
const isHtmlFile = computed(() => /\.html?$/i.test(props.path));

const isAndroidApp = computed(() => {
  return (
    typeof (window as any).AndroidDownloadBridge !== 'undefined' ||
    typeof (window as any).AndroidThemeBridge !== 'undefined'
  );
});

// 根据路径推断 Prism 语言（修复历史遗留：原代码引用了未定义的 displayLang，
// 高亮从未真正生效，静默 fallback 为纯文本）
const displayLang = computed(() => prismLangForPath(props.path));

const highlightedCode = computed(() => {
  if (!rawContent.value) return '';
  let content = rawContent.value;
  if (fileType.value === 'json') {
    try {
      content = JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      // JSON 解析失败时保留原内容
    }
  }
  return highlightCode(content, displayLang.value);
});

const renderedMarkdown = computed(() => {
  if (!rawContent.value) return '';
  return renderMarkdown(rawContent.value, false);
});

const csvParsed = computed(() => {
  if (fileType.value !== 'csv' || !rawContent.value) return null;
  const lines = rawContent.value.split(/\r?\n/).filter((l) => l.trim());
  const parseLine = (line: string): string[] => {
    const result: string[] = [];
    let current = '';
    let inQuote = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQuote) {
        if (ch === '"' && line[i + 1] === '"') {
          current += '"';
          i++;
        } else if (ch === '"') inQuote = false;
        else current += ch;
      } else {
        if (ch === '"') inQuote = true;
        else if (ch === ',') {
          result.push(current);
          current = '';
        } else current += ch;
      }
    }
    result.push(current);
    return result;
  };
  const maxRows = 100;
  const header = parseLine(lines[0] || '');
  const rows = lines.slice(1, maxRows + 1).map(parseLine);
  return { header, rows, truncated: lines.length > maxRows + 1 };
});
const csvHeader = computed(() => csvParsed.value?.header || []);
const csvRows = computed(() => csvParsed.value?.rows || []);
const csvTruncated = computed(() => csvParsed.value?.truncated || false);

// ---- 方法 ----
function inferShowFileType(path: string): string {
  const extMap: Record<string, string> = {
    '.txt': 'text',
    '.md': 'markdown',
    '.markdown': 'markdown',
    '.json': 'json',
    '.json5': 'json',
    '.csv': 'csv',
    '.tsv': 'csv',
    '.yaml': 'text',
    '.yml': 'text',
    '.toml': 'text',
    '.ini': 'text',
    '.cfg': 'text',
    '.conf': 'text',
    '.log': 'text',
    '.js': 'code',
    '.ts': 'code',
    '.jsx': 'code',
    '.tsx': 'code',
    '.vue': 'code',
    '.py': 'code',
    '.rb': 'code',
    '.go': 'code',
    '.rs': 'code',
    '.java': 'code',
    '.c': 'code',
    '.h': 'code',
    '.cpp': 'code',
    '.hpp': 'code',
    '.cs': 'code',
    '.php': 'code',
    '.swift': 'code',
    '.kt': 'code',
    '.sh': 'code',
    '.bash': 'code',
    '.html': 'code',
    '.htm': 'code',
    '.css': 'code',
    '.scss': 'code',
    '.less': 'code',
    '.xml': 'code',
    '.svg': 'image',
    '.sql': 'code',
    '.png': 'image',
    '.jpg': 'image',
    '.jpeg': 'image',
    '.gif': 'image',
    '.webp': 'image',
    '.bmp': 'image',
    '.ico': 'image',
    '.avif': 'image',
    '.pdf': 'pdf'
  };
  const lowerPath = path.toLowerCase();
  const dotIdx = lowerPath.lastIndexOf('.');
  if (dotIdx >= 0) {
    const ext = lowerPath.slice(dotIdx);
    if (extMap[ext]) return extMap[ext];
  }
  return 'binary';
}

function buildContentUrl() {
  return `/api/file/content?path=${encodeURIComponent(props.path)}`;
}

/**
 * 检测 HTML 中的外部资源引用（src/href）。
 * srcdoc iframe 无基础 URL，相对路径与根路径引用都会解析失败；
 * 绝对 URL（含协议相对）与 data/blob/锚点不受影响。
 */
function detectExternalResourceRefs(html: string): boolean {
  const attrPattern = /\b(?:src|href)\s*=\s*["']([^"']*)["']/gi;
  let match: RegExpExecArray | null;
  while ((match = attrPattern.exec(html))) {
    const url = (match[1] || '').trim();
    if (!url || url.startsWith('#')) continue;
    if (/^(https?:)?\/\//i.test(url)) continue;
    if (/^(data|blob|mailto|tel|javascript):/i.test(url)) continue;
    return true;
  }
  return false;
}

/**
 * 全屏预览 HTML 文件（V1：面向自包含单文件）。
 * 与 show_html js=on 卡片同策略：注入 CSP/守卫脚本，沙箱 allow-scripts。
 * 检测到外部资源引用时在顶栏提示「可能显示不完整」。
 */
async function openHtmlPreview() {
  if (previewLoading.value) return;
  previewLoading.value = true;
  error.value = '';
  try {
    let content = rawContent.value || getCachedShowFileContent(props.path) || '';
    if (!content) {
      const resp = await fetch(buildContentUrl());
      if (!resp.ok) {
        let msg = resp.statusText;
        try {
          const j = await resp.json();
          msg = j.error || msg;
        } catch {
          // 响应体非 JSON 时沿用 statusText
        }
        error.value = msg || t('common.loadFailed');
        return;
      }
      content = await resp.text();
      setCachedShowFileContent(props.path, content);
      // 顺手填充卡片代码区内容，保持两处一致
      rawContent.value = content;
    }
    if (!content.trim()) {
      error.value = t('chat.fileEmpty');
      return;
    }
    openShowHtmlFullscreen({
      srcdoc: buildShowHtmlIframeSrcdoc(content),
      allowScripts: true,
      title: displayName.value,
      notice: detectExternalResourceRefs(content)
        ? t('chat.htmlPreviewNotice')
        : undefined
    });
  } catch (e) {
    error.value = (e as Error).message || t('chat.networkError');
  } finally {
    previewLoading.value = false;
  }
}

async function loadContent() {
  if (!canPreview.value) return;
  error.value = '';

  // 文本类文件优先读缓存，避免重复 fetch 导致闪烁
  if (!['image', 'pdf'].includes(fileType.value)) {
    const cached = getCachedShowFileContent(props.path);
    if (cached !== null) {
      rawContent.value = cached;
      return;
    }
  }

  loading.value = true;
  try {
    if (['image', 'pdf'].includes(fileType.value)) {
      contentUrl.value = buildContentUrl();
    } else {
      const resp = await fetch(buildContentUrl());
      if (!resp.ok) {
        let msg = resp.statusText;
        try {
          const j = await resp.json();
          msg = j.error || msg;
        } catch {
          // 响应体非 JSON 时沿用 statusText
        }
        error.value = msg || t('common.loadFailed');
        return;
      }
      const content = await resp.text();
      rawContent.value = content;
      setCachedShowFileContent(props.path, content);
    }
  } catch (e) {
    error.value = (e as Error).message || t('chat.networkError');
  } finally {
    loading.value = false;
  }
}

async function handleDownload() {
  const url = `/api/download/file?path=${encodeURIComponent(props.path)}`;
  const name = props.path.split('/').pop() || 'file';

  // Android App 内通过原生桥接下载，WebView 的 a.download 通常无法触发系统下载器
  const androidBridge = (window as any).AndroidDownloadBridge;
  if (androidBridge && typeof androidBridge.downloadFile === 'function') {
    try {
      androidBridge.downloadFile(url, name);
      return;
    } catch (e) {
      console.warn('[ShowFileCard] Android 桥接下载失败，回退:', e);
    }
  }

  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      let msg = resp.statusText;
      try {
        const j = await resp.json();
        msg = j.error || msg;
      } catch {
        // 响应体非 JSON 时沿用 statusText
      }
      throw new Error(msg || t('common.downloadFailed'));
    }
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
  } catch (e) {
    console.warn('[ShowFileCard] 下载失败:', e);
    window.open(url, '_blank');
  }
}

async function copyContent() {
  if (!rawContent.value) return;
  try {
    await navigator.clipboard.writeText(rawContent.value);
    copied.value = true;
    setTimeout(() => {
      copied.value = false;
    }, 2000);
  } catch {
    // 剪贴板不可用时静默忽略
  }
}

function openFullImage() {
  if (!contentUrl.value) return;
  // 全站统一走全局灯箱预览（Android WebView 同样适用，不再新开标签页）
  useUiStore().openImagePreview({ url: contentUrl.value, name: displayName.value || '' });
}

function onImageError() {
  error.value = t('chat.imageLoadFailed');
}

onMounted(() => {
  if (canPreview.value) {
    loadContent();
  }
});
</script>

<style lang="scss" scoped>
.show-file-card {
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-base);
  overflow: hidden;
  margin: 8px 0;
  font-size: 13px;
}

.sfc-header {
  min-height: 44px;
  background: var(--surface-raised);
  padding: 0 12px 0 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-default);
  gap: 12px;
}

.sfc-file-icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  background-color: var(--text-secondary);
  -webkit-mask: url('/static/icons/file-down.svg') no-repeat center / contain;
  mask: url('/static/icons/file-down.svg') no-repeat center / contain;
}

.sfc-name {
  flex: 1;
  min-width: 0;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sfc-actions {
  flex-shrink: 0;
  display: flex;
  gap: 6px;
}

.sfc-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s;

  &:hover {
    background: var(--surface-muted);
    border-color: var(--border-strong);
  }

  &:disabled {
    opacity: 0.6;
    cursor: default;

    &:hover {
      background: var(--surface-base);
      border-color: var(--border-default);
    }
  }
}

.sfc-btn-download {
  color: var(--accent-primary);
}

.sfc-preview {
  border-top: 1px solid var(--border-default);
  max-height: 360px;
  min-height: 160px;
  overflow: auto;
  position: relative;
  background: var(--surface-base);
  scrollbar-width: thin;
  scrollbar-color: var(--text-muted) transparent;
}

.sfc-preview--csv {
  overflow: auto;
  display: block;
  padding: 0;
}

.sfc-preview::-webkit-scrollbar {
  width: 8px;
}

.sfc-preview::-webkit-scrollbar-track {
  background: transparent;
}

.sfc-preview::-webkit-scrollbar-thumb {
  background: var(--text-muted);
  border-radius: 8px;
}

.sfc-loading,
.sfc-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: var(--text-tertiary);
  justify-content: center;
  font-size: 12px;
}

.sfc-error {
  color: var(--state-error);
}

.sfc-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border-default);
  border-top-color: var(--accent-primary);
  border-radius: 50%;
  animation: sfc-spin 0.8s linear infinite;
}

@keyframes sfc-spin {
  to {
    transform: rotate(360deg);
  }
}

.sfc-code {
  margin: 0;
  padding: 12px;
  font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
  background: var(--surface-base);
}

.sfc-csv-scroll {
  overflow-x: auto;
  overflow-y: hidden;
  margin: 0;
  padding: 0;
}
.sfc-csv-scroll--loading {
  min-height: 160px;
}

.sfc-csv-table {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  border-spacing: 0;
  font-size: 12px;
  margin: 0;
  padding: 0;
  background: var(--surface-soft);
}

.sfc-csv-table th,
.sfc-csv-table td {
  padding: 6px 10px;
  border: 1px solid var(--border-soft);
  text-align: left;
  white-space: nowrap;
}

.sfc-csv-table th {
  background: var(--surface-soft);
  font-weight: 600;
}

.sfc-csv-table td {
  background: var(--surface-base);
}

.sfc-csv-meta {
  padding: 6px 12px;
  font-size: 11px;
  color: var(--text-tertiary);
  text-align: center;
  border-top: 1px solid var(--border-soft);
}

.sfc-md {
  padding: 12px;
  max-height: 400px;
  overflow: auto;
}

.sfc-image {
  display: block;
  max-width: 100%;
  max-height: 400px;
  margin: 0 auto;
  cursor: zoom-in;
  padding: 12px;
}

.sfc-image--android {
  cursor: default;
  pointer-events: none;
  -webkit-user-select: none;
  user-select: none;
}
</style>

<template>
  <div ref="containerRef" class="pdf-preview">
    <div v-if="loading" class="pdf-preview-loading">
      <span class="pdf-preview-spinner"></span>
      <span>PDF 加载中...</span>
    </div>

    <div v-else-if="error" class="pdf-preview-error">{{ error }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist/legacy/build/pdf.mjs';
import pdfWorkerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

defineOptions({ name: 'PdfPreview' });

const props = defineProps<{
  source: string;
}>();

const containerRef = ref<HTMLDivElement | null>(null);
const loading = ref(true);
const error = ref('');

let pdfDoc: PDFDocumentProxy | null = null;
let renderTasks: RenderTask[] = [];

function clearPages() {
  const container = containerRef.value;
  if (!container) return;
  container.querySelectorAll('canvas').forEach((c) => c.remove());
}

function cancelPendingRenders() {
  renderTasks.forEach((task) => {
    try { task.cancel(); } catch {}
  });
  renderTasks = [];
}

async function destroyDocument() {
  cancelPendingRenders();
  clearPages();
  if (pdfDoc) {
    try { await pdfDoc.destroy(); } catch {}
    pdfDoc = null;
  }
}

async function renderPdf() {
  const container = containerRef.value;
  if (!container || !props.source) return;

  await destroyDocument();
  loading.value = true;
  error.value = '';

  try {
    const resp = await fetch(props.source, { credentials: 'same-origin' });
    if (!resp.ok) {
      throw new Error(resp.statusText || 'PDF 加载失败');
    }
    const data = await resp.arrayBuffer();
    if (!data.byteLength) {
      throw new Error('PDF 内容为空');
    }

    const loadingTask = getDocument({ data });
    pdfDoc = await loadingTask.promise;
    const numPages = pdfDoc.numPages;
    const containerWidth = container.clientWidth;

    for (let pageNum = 1; pageNum <= numPages; pageNum++) {
      const page = await pdfDoc.getPage(pageNum);
      const baseViewport = page.getViewport({ scale: 1 });
      const scale = containerWidth > 0 ? containerWidth / baseViewport.width : 1;
      const viewport = page.getViewport({ scale });

      const canvas = document.createElement('canvas');
      canvas.className = 'pdf-preview-page';
      canvas.style.display = 'block';
      canvas.style.width = '100%';
      canvas.style.height = 'auto';
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      container.appendChild(canvas);

      const ctx = canvas.getContext('2d');
      if (!ctx) continue;
      ctx.scale(dpr, dpr);

      const renderTask = page.render({ canvasContext: ctx, viewport });
      renderTasks.push(renderTask);
      await renderTask.promise;
      renderTasks = renderTasks.filter((t) => t !== renderTask);
    }

    loading.value = false;
  } catch (e) {
    const message = (e as Error)?.message || 'PDF 渲染失败';
    if (message.toLowerCase().includes('cancel')) {
      return;
    }
    error.value = message;
    loading.value = false;
  }
}

onMounted(() => {
  renderPdf();
});

onBeforeUnmount(() => {
  destroyDocument();
});

watch(() => props.source, () => {
  renderPdf();
});
</script>

<style scoped lang="scss">
.pdf-preview {
  width: 100%;
  min-height: 120px;
}

.pdf-preview-loading,
.pdf-preview-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.pdf-preview-error {
  color: var(--state-error);
}

.pdf-preview-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--border-default);
  border-top-color: var(--accent-primary);
  border-radius: 50%;
  animation: pdf-preview-spin 0.8s linear infinite;
}

@keyframes pdf-preview-spin {
  to {
    transform: rotate(360deg);
  }
}

.pdf-preview-page {
  display: block;
  width: 100%;
  height: auto;
  margin: 0;
}
</style>

<template>
  <transition name="qd-preview-slide">
    <aside v-if="activeTarget" class="qd-preview" :style="previewInlineStyle">
      <div
        class="qd-preview__resize"
        :title="$t('quickdock.resizeWidthHint')"
        @mousedown="startPreviewResize"
      ></div>
      <section class="qd-preview__panel">
        <header class="qd-preview__header">
          <span class="qd-preview__name" :title="addressText">{{ activeTarget.label }}</span>
          <span class="qd-preview__path" :title="addressText">{{ addressText }}</span>
          <button
            class="qd-preview__tool"
            :title="$t('quickdock.previewRefresh')"
            @click="refresh"
          >
            <svg viewBox="0 0 16 16" fill="none">
              <path
                d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3"
                stroke="currentColor"
                stroke-width="1.3"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <button
            class="qd-preview__tool"
            :title="$t('quickdock.previewOpenExternal')"
            @click="openExternal"
          >
            <svg viewBox="0 0 16 16" fill="none">
              <path
                d="M6.5 3.5h-3v9h9v-3M9.5 3.5h3v3M12.5 3.5l-5 5"
                stroke="currentColor"
                stroke-width="1.3"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <CloseButton :label="$t('common.close')" :title="$t('common.close')" @click="close" />
        </header>
        <div class="qd-preview__body qd-preview__body--iframe">
          <iframe
            v-if="iframeSrc"
            :key="`${activeKey}:${refreshNonce}`"
            class="qd-preview__iframe"
            :src="iframeSrc"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          ></iframe>
        </div>
      </section>
    </aside>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import CloseButton from '@/components/common/CloseButton.vue';
import { usePreviewStore } from '@/stores/preview';
import { useUiStore } from '@/stores/ui';

/**
 * 预览面板（预览窗口的展开视图，与文件预览侧边栏同位）
 * - iframe 地址推导收口在 stores/preview.previewUrlFor：本机访问走独立预览服务器
 *   （127.0.0.1 随机端口 + 对话 token，与主应用跨站隔离），远端访问回退旧同源端点
 * - 服务器目标在 host 模式下直连 localhost（保真 + HMR 可用）
 * - MVP 已知限制：代理不转发 WebSocket，dev server 热更新在代理预览中不生效，用刷新按钮
 */

const props = defineProps<{ hostMode: boolean }>();

const preview = usePreviewStore();
const { activeTarget, activeKey } = storeToRefs(preview);

const uiStore = useUiStore();
const { isMobileViewport } = storeToRefs(uiStore);

const refreshNonce = ref(0);

/** 预览面板宽度（与文件预览同一存储键，两处宽度偏好一致） */
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

const previewInlineStyle = computed(() =>
  isMobileViewport.value ? {} : { width: `${previewWidth.value}px` }
);

function startPreviewResize(event: MouseEvent) {
  event.preventDefault();
  const startX = event.clientX;
  const startWidth = previewWidth.value;
  const onMove = (e: MouseEvent) => {
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

const iframeSrc = computed(() => {
  const target = activeTarget.value;
  if (!target) return '';
  // 地址推导收口在 store：有独立预览服务器（隔离源）优先，否则回退旧同源端点；
  // host 模式的服务器目标直连 localhost（保真 + HMR）
  return preview.previewUrlFor(target, props.hostMode);
});

const addressText = computed(() => {
  const target = activeTarget.value;
  if (!target) return '';
  return target.type === 'server' ? target.url || '' : target.path || '';
});

function refresh() {
  refreshNonce.value += 1;
}

function openExternal() {
  const target = activeTarget.value;
  if (!target) return;
  const url = target.type === 'server' ? target.url : iframeSrc.value;
  if (url) {
    window.open(url, '_blank', 'noopener');
  }
}

function close() {
  preview.closePanel();
}
</script>

<style scoped>
.qd-preview__tool {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: 6px;
  flex: none;
}
.qd-preview__tool:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}
.qd-preview__tool svg {
  width: 14px;
  height: 14px;
}
.qd-preview__body--iframe {
  padding: 0;
  overflow: hidden;
}
.qd-preview__iframe {
  width: 100%;
  height: 100%;
  border: none;
  /* 网页内容背景未知，默认透明会透出面板底色；用语义 token 而非写死白色 */
  background: var(--surface-base);
}
</style>

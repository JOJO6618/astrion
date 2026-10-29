<template>
  <transition name="qd-preview-slide">
    <aside v-if="activeTarget" class="qd-preview" :style="previewInlineStyle">
      <div
        class="qd-preview__resize"
        :title="$t('quickdock.resizeWidthHint')"
        @pointerdown="startPreviewResize"
      ></div>
      <section class="qd-preview__panel">
        <header class="qd-preview__header">
          <span class="qd-preview__name" :title="addressText">{{ activeTarget.label }}</span>
          <span class="qd-preview__path" :title="addressText">{{ addressText }}</span>
          <button class="qd-preview__tool" :title="$t('quickdock.previewRefresh')" @click="refresh">
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
        <div class="qd-preview__body qd-preview__body--iframe" :class="{ 'is-resizing': resizing }">
          <iframe
            v-if="iframeSrc"
            :key="`${activeKey}:${refreshNonce}:${preview.previewBase}`"
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
import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import CloseButton from '@/components/common/CloseButton.vue';
import { usePreviewStore } from '@/stores/preview';
import { useUiStore } from '@/stores/ui';
import { useConversationStore } from '@/stores/conversation';
import { openExternal as openExternalUrl, openWorkspaceFile } from '@/utils/openExternal';

/**
 * 预览面板（预览窗口的展开视图，与文件预览侧边栏同位）
 * - iframe 地址推导收口在 stores/preview.previewUrlFor：本机访问走独立预览服务器
 *   （127.0.0.1 随机端口 + 对话 token，与主应用跨站隔离），远端访问回退旧同源端点
 * - 服务器目标在 host 模式下直连 localhost（保真 + HMR 可用）
 * - MVP 已知限制：代理不转发 WebSocket，dev server 热更新在代理预览中不生效，用刷新按钮
 */

const props = defineProps<{ hostMode: boolean; workspaceId?: string }>();

const preview = usePreviewStore();
const { activeTarget, activeKey } = storeToRefs(preview);
const conversationStore = useConversationStore();
const { currentConversationId } = storeToRefs(conversationStore);

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
/** 拖拽中：iframe 在自己的文档里吞掉指针事件，必须让它临时让出命中测试 */
const resizing = ref(false);

const previewInlineStyle = computed(() =>
  isMobileViewport.value ? {} : { width: `${previewWidth.value}px` }
);

function startPreviewResize(event: PointerEvent) {
  event.preventDefault();
  const handle = event.currentTarget as HTMLElement | null;
  const pointerId = event.pointerId;
  try {
    handle?.setPointerCapture(pointerId);
  } catch {
    /* 捕获失败不影响：下面的 document 监听 + iframe 让出指针事件已足够 */
  }
  const startX = event.clientX;
  const startWidth = previewWidth.value;
  // 手柄贴在面板左缘，向右拖会立刻压到 iframe 上。iframe 是独立文档，
  // mousemove 会被它自己吃掉、不再冒泡到父文档 —— 以前表现为「向左拉大很容易，
  // 向右拉小完全拉不动」。拖拽期间关掉 iframe 的 pointer-events 即可。
  resizing.value = true;
  const onMove = (e: PointerEvent) => {
    const next = startWidth + (startX - e.clientX);
    previewWidth.value = Math.max(PREVIEW_WIDTH_MIN, Math.min(PREVIEW_WIDTH_MAX, next));
  };
  const onEnd = () => {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onEnd);
    document.removeEventListener('pointercancel', onEnd);
    try {
      handle?.releasePointerCapture(pointerId);
    } catch {
      /* 指针已释放 */
    }
    resizing.value = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    try {
      window.localStorage.setItem(PREVIEW_WIDTH_STORAGE_KEY, String(previewWidth.value));
    } catch {
      /* 持久化失败不影响本次调整 */
    }
  };
  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onEnd);
  document.addEventListener('pointercancel', onEnd);
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
}

const iframeSrc = computed(() => {
  const target = activeTarget.value;
  if (!target) return '';
  // 地址推导收口在 store：有独立预览服务器（隔离源）优先，否则回退旧同源端点；
  // host 模式的服务器目标直连 localhost（保真 + HMR）；
  // 回退端点必须带工作区/对话身份，不能再依赖 session 选中工作区。
  return preview.previewUrlFor(
    target,
    props.hostMode,
    props.workspaceId,
    currentConversationId.value || undefined
  );
});

// 面板打开目标时补齐预览运行时：首条消息创建的对话不跑 bootstrap，没有 token 只能走回退。
watch(
  activeTarget,
  (target) => {
    if (!target) return;
    void preview.ensureRuntime(props.workspaceId, currentConversationId.value || undefined);
  },
  { immediate: true }
);

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
  if (target.type === 'file') {
    // 文件目标：打开的是真实文件本身（后端用系统默认程序打开，file:// 真身），
    // 而不是预览服务器的代理地址（随机端口 + 对话 token，复制出去无意义）。
    // 宿主通道不可用（非 host 模式等）时才退回代理地址。
    void openWorkspaceFile(target.path || '').then((opened) => {
      if (!opened && iframeSrc.value) void openExternalUrl(iframeSrc.value);
    });
    return;
  }
  const url = target.url;
  // 统一走 openExternal：桌面壳 window.open 静默失败，由后端转系统浏览器
  if (url) void openExternalUrl(url);
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
/* 拖拽调宽期间：iframe 是独立文档，会吞掉指针事件导致向右拖（缩小）卡死 */
.qd-preview__body--iframe.is-resizing .qd-preview__iframe {
  pointer-events: none;
}
</style>

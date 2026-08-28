<template>
  <div class="review-overlay" @click.self="$emit('close')">
    <div class="review-window">
      <div class="review-header">
        <div class="header-titles">
          <div class="title">{{ $t('overlay.reviewTitle') }}</div>
          <div class="subtitle">{{ $t('overlay.reviewSubtitle') }}</div>
        </div>
        <div class="header-actions">
          <div v-if="generatedPath" class="hint" :title="generatedPath">
            {{ $t('overlay.generatedHint', { path: generatedPath }) }}
          </div>
          <button
            type="button"
            class="icon-close-btn"
            :aria-label="$t('common.close')"
            :title="$t('common.close')"
            @click="$emit('close')"
            :disabled="submitting"
          >
            <span class="icon icon-sm" :style="iconStyle('x')" aria-hidden="true"></span>
          </button>
        </div>
      </div>

      <div class="review-body">
        <div class="review-left">
          <div class="pane-head">
            <span class="pane-head-label">{{ $t('overlay.conversationList') }}</span>
            <span class="pane-head-meta" v-if="conversations.length">
              {{ $t('overlay.conversationCount', { n: conversations.length }) }}
            </span>
          </div>
          <div class="scroll-area conversation-list" :class="{ loading }">
            <div v-if="loading" class="empty">{{ $t('common.loading') }}</div>
            <div v-else-if="!conversations.length" class="empty">{{ $t('overlay.noConversations') }}</div>
            <template v-else>
              <button
                v-for="conv in conversations"
                :key="conv.id"
                type="button"
                class="conversation-item"
                :class="{
                  active: conv.id === selectedId,
                  current: conv.id === currentConversationId
                }"
                @click="$emit('select', conv.id)"
                :disabled="submitting || conv.id === currentConversationId"
              >
                <div class="row">
                  <span class="item-title">{{ conv.title || $t('overlay.unnamedConversation') }}</span>
                  <span v-if="conv.id === currentConversationId" class="tag current-tag">{{ $t('overlay.currentTag') }}</span>
                </div>
                <div class="meta">
                  <span>{{ formatUpdatedAt(conv.updated_at) }}</span>
                  <span>
                    {{ $t('overlay.messageCount', { n: conv.total_messages || 0 }) }}
                    <span v-if="(conv.total_tools || 0) > 0">{{ $t('overlay.toolCount', { n: conv.total_tools }) }}</span>
                  </span>
                </div>
              </button>
            </template>
            <div class="list-footer" v-if="conversations.length || hasMore">
              <button
                type="button"
                class="load-more-btn"
                @click="$emit('load-more')"
                :disabled="loadingMore || !hasMore || submitting"
              >
                {{ loadingMore ? $t('overlay.loadingMore') : hasMore ? $t('overlay.loadMore') : $t('overlay.noMore') }}
              </button>
            </div>
          </div>
        </div>

        <div class="review-right">
          <div class="pane-head">
            <span class="pane-head-label">{{ $t('overlay.previewTitle', { n: previewLimit }) }}</span>
            <span v-if="preview && preview.length" class="pane-head-meta">
              {{ $t('overlay.previewCount', { n: preview.length }) }}
            </span>
          </div>
          <div class="scroll-area preview-box" :class="{ loading: previewLoading }">
            <div v-if="previewLoading" class="placeholder">
              <span class="icon icon-xl placeholder-icon" :style="iconStyle('clock')" aria-hidden="true"></span>
              <div class="text-main">{{ $t('overlay.previewGenerating') }}</div>
            </div>
            <div v-else-if="previewError" class="placeholder error">
              <span class="icon icon-xl placeholder-icon" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
              <div class="text-main">{{ previewError }}</div>
            </div>
            <div v-else-if="!preview || !preview.length" class="placeholder">
              <span class="icon icon-xl placeholder-icon" :style="iconStyle('file')" aria-hidden="true"></span>
              <div class="text-main">{{ $t('overlay.previewEmptyHint') }}</div>
              <div class="text-sub">{{ $t('overlay.previewLimitHint', { n: previewLimit }) }}</div>
            </div>
            <div v-else class="preview-list">
              <div v-for="(line, idx) in preview" :key="idx" class="preview-line">
                {{ line }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="review-footer">
        <label class="toggle-send">
          <input
            type="checkbox"
            :checked="sendToModel"
            @change="$emit('toggle-send', ($event.target as HTMLInputElement).checked)"
          />
          <span class="switch"></span>
          <span class="label">{{ $t('overlay.sendToModel') }}</span>
        </label>
        <button
          type="button"
          class="primary-btn"
          @click="$emit('confirm')"
          :disabled="!selectedId || submitting"
        >
          {{ submitting ? $t('overlay.generating') : $t('common.confirm') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ConversationReviewDialog' });

const props = defineProps<{
  open: boolean;
  conversations: Array<{
    id: string;
    title: string;
    updated_at: string | number;
    total_messages?: number;
    total_tools?: number;
  }>;
  selectedId: string | null;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  submitting: boolean;
  currentConversationId: string | null;
  preview: string[];
  previewLoading: boolean;
  previewError?: string | null;
  previewLimit?: number;
  sendToModel: boolean;
  generatedPath?: string | null;
  iconStyle?: (key: string) => Record<string, string>;
}>();

const iconStyle = (key: string) => (props.iconStyle ? props.iconStyle(key) : {});

defineEmits<{
  (event: 'close'): void;
  (event: 'select', id: string): void;
  (event: 'load-more'): void;
  (event: 'confirm'): void;
  (event: 'toggle-send', value: boolean): void;
}>();

const formatUpdatedAt = (value: string | number) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
</script>

<style scoped>
/* ===== 遮罩层 ===== */
.review-overlay {
  position: fixed;
  inset: 0;
  background: var(--theme-overlay-scrim);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 420;
  padding: 24px;
}

/* ===== 窗口：单层圆角，最大尺寸 + 内部滚动 ===== */
.review-window {
  width: min(1040px, 96vw);
  max-width: 96vw;
  height: min(700px, 88vh);
  max-height: 88vh;
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  border-radius: 16px;
  box-shadow: var(--theme-shadow-strong);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ===== 头/脚：靠分隔线区分，不再套盒子 ===== */
.review-header {
  flex: 0 0 auto;
  height: 64px;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--border-default);
}

.header-titles {
  min-width: 0;
}

.review-header .title {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.review-header .subtitle {
  font-size: 12px;
  line-height: 1.2;
  color: var(--text-secondary);
  margin-top: 4px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.hint {
  flex: 0 1 auto;
  height: 28px;
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  color: var(--text-secondary);
  padding: 0 10px;
  border-radius: 8px;
  background: var(--theme-tab-active);
  max-width: 280px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

/* 自定义关闭按钮，固定尺寸、视觉居中 */
.icon-close-btn {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.icon-close-btn:hover:not(:disabled) {
  background: var(--theme-tab-active);
  color: var(--text-primary);
}

.icon-close-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ===== 主体：左右两栏，靠竖线分隔 ===== */
.review-body {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: 360px 1fr;
  min-height: 0;
}

.review-left {
  border-right: 1px solid var(--border-default);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.review-right {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* 栏头：固定高度 */
.pane-head {
  flex: 0 0 auto;
  height: 40px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pane-head-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.pane-head-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ===== 通用滚动容器：隐藏滚动条，无边框盒子 ===== */
.scroll-area {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.scroll-area::-webkit-scrollbar {
  width: 0;
  height: 0;
  display: none;
}

/* ===== 对话列表项：扁平，固定高度，无套娃边框 ===== */
.conversation-list {
  padding: 4px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.conversation-item {
  width: 100%;
  height: 60px;
  flex: 0 0 60px;
  box-sizing: border-box;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 10px;
  padding: 0 12px;
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.conversation-item:hover:not(:disabled),
.conversation-item.active {
  background: var(--theme-tab-active);
}

/* 选中态：左侧强调条提示，不靠彩色光晕 */
.conversation-item.active {
  position: relative;
}

.conversation-item.active::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 14px;
  bottom: 14px;
  width: 3px;
  border-radius: 999px;
  background: var(--text-secondary);
}

.conversation-item .row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--text-primary);
}

.conversation-item .item-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 14px;
  line-height: 1.25;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-item .tag {
  flex: 0 0 auto;
  height: 20px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1;
  background: var(--theme-chip-bg);
  color: var(--text-secondary);
}

.conversation-item .meta {
  font-size: 12px;
  line-height: 1;
  color: var(--text-secondary);
  display: flex;
  gap: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.empty {
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  padding: 20px 0;
}

.list-footer {
  flex: 0 0 auto;
  padding: 2px 0;
}

/* 加载更多按钮：对齐侧边栏对话记录的样式（全宽、文字靠左、固定高度） */
.load-more-btn {
  width: 100%;
  height: 34px;
  border: 0;
  border-radius: 10px;
  padding: 0 12px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.load-more-btn:hover:not(:disabled) {
  background: var(--theme-tab-active);
  color: var(--text-primary);
}

.load-more-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== 预览区 ===== */
.preview-box {
  padding: 4px 12px 12px;
}

.preview-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-family: Menlo, Consolas, 'SFMono-Regular', monospace;
  font-size: 13px;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* 预览行：扁平，靠交替/留白分隔，不套边框圆角盒 */
.preview-line {
  padding: 7px 10px;
  border-radius: 8px;
  line-height: 1.5;
}

.preview-line:nth-child(odd) {
  background: var(--theme-tab-active);
}

.placeholder {
  height: 100%;
  min-height: 220px;
  text-align: center;
  color: var(--text-secondary);
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px;
  align-items: center;
  justify-content: center;
}

.placeholder-icon {
  color: var(--text-tertiary);
  opacity: 0.9;
}

.text-main {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-secondary);
}

.text-sub {
  font-size: 12px;
  color: var(--text-tertiary);
}

.placeholder.error .text-main {
  color: var(--state-warning);
}

/* ===== 底部操作栏 ===== */
.review-footer {
  flex: 0 0 auto;
  height: 64px;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-top: 1px solid var(--border-default);
}

/* 自定义开关（非原生 checkbox 外观），固定高度 */
.toggle-send {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  height: 38px;
  font-size: 14px;
  color: var(--text-primary);
  cursor: pointer;
  user-select: none;
}

.toggle-send input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-send .switch {
  flex: 0 0 auto;
  width: 38px;
  height: 22px;
  border-radius: 999px;
  background: var(--theme-switch-track);
  position: relative;
  transition: background 0.2s ease;
}

.toggle-send .switch::after {
  content: '';
  position: absolute;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--theme-surface-strong);
  top: 2px;
  left: 2px;
  transition: transform 0.2s ease;
}

.toggle-send input:checked + .switch {
  background: var(--accent-strong);
}

.toggle-send input:checked + .switch::after {
  transform: translateX(16px);
}

.toggle-send .label {
  white-space: nowrap;
}

/* ===== 主操作按钮：固定高度，无彩色光晕 ===== */
.primary-btn {
  flex: 0 0 auto;
  height: 38px;
  border: 0;
  border-radius: 10px;
  padding: 0 20px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  background: var(--accent);
  color: var(--on-accent);
  transition:
    background 140ms ease,
    color 140ms ease;
}

.primary-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.primary-btn:disabled,
.conversation-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.conversation-item.current {
  cursor: default;
}

/* ===== 窄屏：上下堆叠 ===== */
@media (max-width: 860px) {
  .review-body {
    grid-template-columns: 1fr;
    grid-template-rows: 1fr 1fr;
  }
  .review-left {
    border-right: none;
    border-bottom: 1px solid var(--border-default);
  }
}
</style>

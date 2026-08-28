<template>
  <div class="versioning-dialog" @click.self="$emit('close')">
    <div class="versioning-card" @click="closeScopeMenu">
      <header class="versioning-header">
        <div class="versioning-header-left">
          <h3>{{ $t('common.versioning') }}</h3>
          <label class="switch-line">
            <input
              type="checkbox"
              :checked="enabled"
              :disabled="loading"
              @change="$emit('toggle-enabled', ($event.target as HTMLInputElement).checked)"
            />
            <span class="switch"></span>
            <span class="switch-text">{{ enabled ? $t('overlay.switchOn') : $t('overlay.switchOff') }}</span>
          </label>
        </div>
        <div class="versioning-header-actions">
          <button type="button" class="refresh-btn" :disabled="loading" @click="$emit('refresh')">
            <span class="icon icon-sm" :style="iconStyle('refreshCw')" aria-hidden="true"></span>
            <span>{{ $t('overlay.refresh') }}</span>
          </button>
          <button type="button" class="icon-close-btn" :aria-label="$t('common.close')" @click="$emit('close')">
            <span class="icon icon-sm" :style="iconStyle('x')" aria-hidden="true"></span>
          </button>
        </div>
      </header>

      <div class="versioning-body">
        <aside class="checkpoint-list scroll-area">
          <button
            v-for="item in orderedItems"
            :key="item.seq"
            type="button"
            class="checkpoint-item"
            :class="{ active: Number(selectedSeq) === Number(item.seq) }"
            @click="$emit('select', item.seq)"
          >
            <div class="row top">
              <span class="seq">#{{ item.seq }}</span>
              <span class="time">{{ formatTime(item.timestamp) }}</span>
            </div>
            <div class="preview">{{ item.message_preview || $t('overlay.emptyMessage') }}</div>
            <div class="row stats">
              <span class="plus">+{{ item.insertions || 0 }}</span>
              <span class="minus">-{{ item.deletions || 0 }}</span>
              <span class="files">{{ $t('overlay.filesCount', { n: item.files_changed || 0 }) }}</span>
            </div>
          </button>
          <div v-if="!items.length" class="empty">{{ $t('overlay.noCheckpoints') }}</div>
        </aside>

        <section class="checkpoint-detail scroll-area">
          <div v-if="detailLoading" class="loading">{{ $t('overlay.loadingDetail') }}</div>
          <template v-else-if="detail">
            <h4>{{ $t('overlay.detailTitle', { seq: detail.seq }) }}</h4>
            <div class="summary">
              <span class="plus">+{{ detail.insertions || 0 }}</span>
              <span class="minus">-{{ detail.deletions || 0 }}</span>
              <span class="files">{{ $t('overlay.filesCount', { n: detail.files_changed || 0 }) }}</span>
            </div>
            <div class="file-list">
              <template v-for="(file, idx) in detail.files || []" :key="`${file.status}:${file.path}:${idx}`">
                <div class="file-row" :class="{ expanded: isFileExpanded(file, idx) }" @click="toggleFileExpand(file, idx)">
                  <span class="status">{{ statusLabel(file.status) }}</span>
                  <span class="path">{{ file.path }}</span>
                  <span class="delta">
                    <span class="plus">+{{ file.insertions || 0 }}</span>
                    <span class="minus">-{{ file.deletions || 0 }}</span>
                  </span>
                </div>
                <div v-if="isFileExpanded(file, idx)" class="tool-result-diff scroll-area versioning-diff">
                  <div v-if="!(file.patch_lines || []).length" class="empty">{{ $t('overlay.noDiffLines') }}</div>
                  <template v-for="(line, lineIdx) in file.patch_lines || []" :key="`line:${idx}:${lineIdx}`">
                    <div
                      v-for="(subLine, subIdx) in splitDiffContent(line.content)"
                      :key="`line:${idx}:${lineIdx}:${subIdx}`"
                      class="diff-line"
                      :class="line.type === 'add' ? 'diff-add' : (line.type === 'remove' ? 'diff-remove' : 'diff-context')"
                    >
                      <span class="diff-marker">{{ line.type === 'add' ? '+' : (line.type === 'remove' ? '-' : ' ') }}</span>
                      <span class="diff-content">{{ subLine }}</span>
                    </div>
                  </template>
                  <div v-if="file.patch_truncated" class="approval-note">{{ $t('overlay.diffTruncated') }}</div>
                </div>
              </template>
            </div>
          </template>
          <div v-else class="empty">{{ $t('overlay.selectCheckpoint') }}</div>
        </section>
      </div>

      <footer class="versioning-footer">
        <div class="restore-options">
          <div class="restore-option-group">
            <span class="restore-option-label">{{ $t('overlay.restoreScope') }}</span>
            <div class="restore-option-buttons">
              <button
                type="button"
                class="restore-option-btn"
                :class="{ active: trackingMode === 'conversation_only' }"
                :disabled="restoring"
                @click="$emit('update:tracking-mode', 'conversation_only')"
              >
                {{ $t('overlay.scopeConversationOnly') }}
              </button>
              <button
                type="button"
                class="restore-option-btn"
                :class="{ active: trackingMode === 'workspace_and_conversation', disabled: !hostMode }"
                :disabled="restoring || !hostMode"
                @click="$emit('update:tracking-mode', 'workspace_and_conversation')"
              >
                {{ $t('overlay.scopeWorkspaceAndConversation') }}
              </button>
            </div>
          </div>
          <div class="restore-option-group">
            <span class="restore-option-label">{{ $t('overlay.restoreModeLabel') }}</span>
            <div class="restore-option-buttons">
              <button
                type="button"
                class="restore-option-btn"
                :class="{ active: restoreMode === 'overwrite' }"
                :disabled="restoring"
                @click="$emit('update:restore-mode', 'overwrite')"
              >
                {{ $t('overlay.modeOverwrite') }}
              </button>
              <button
                type="button"
                class="restore-option-btn"
                :class="{ active: restoreMode === 'copy' }"
                :disabled="restoring"
                @click="$emit('update:restore-mode', 'copy')"
              >
                {{ $t('overlay.modeCopy') }}
              </button>
            </div>
          </div>
        </div>
        <button
          type="button"
          class="confirm-btn"
          :disabled="!enabled || selectedSeq === null || selectedSeq === undefined || restoring"
          @click="$emit('confirm')"
        >
          {{ restoring ? $t('overlay.restoring') : $t('overlay.confirmRestore') }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';

const props = defineProps<{
  hostMode: boolean;
  enabled: boolean;
  loading: boolean;
  items: any[];
  selectedSeq: number | null;
  detail: any;
  detailLoading: boolean;
  restoring: boolean;
  restoreMode: string;
  trackingMode: string;
  iconStyle?: (key: string) => Record<string, string>;
}>();

const emit = defineEmits([
  'close',
  'refresh',
  'toggle-enabled',
  'select',
  'confirm',
  'update:restore-mode',
  'update:tracking-mode'
]);

const iconStyle = (key: string) => (props.iconStyle ? props.iconStyle(key) : {});

const orderedItems = computed(() => {
  const src = Array.isArray(props.items) ? props.items : [];
  return [...src].sort((a: any, b: any) => Number(a?.seq || 0) - Number(b?.seq || 0));
});

const expandedFileKey = ref<string | null>(null);
const fileKey = (file: any, idx: number) => `${file?.status || ''}:${file?.old_path || ''}:${file?.path || ''}:${idx}`;
const toggleFileExpand = (file: any, idx: number) => {
  const key = fileKey(file, idx);
  expandedFileKey.value = expandedFileKey.value === key ? null : key;
};
const isFileExpanded = (file: any, idx: number) => expandedFileKey.value === fileKey(file, idx);

watch(
  () => props.detail?.seq,
  () => {
    expandedFileKey.value = null;
  }
);

const formatTime = (value: string) => {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  } catch {
    return value;
  }
};

const statusLabel = (status: string): string => {
  const map: Record<string, string> = {
    added: 'A',
    modified: 'M',
    deleted: 'D',
    renamed: 'R'
  };
  return map[String(status || '').toLowerCase()] || String(status || '').toUpperCase().slice(0, 1);
};

const splitDiffContent = (content: any): string[] => {
  if (content === null || content === undefined) return [''];
  return String(content).split('\n');
};
</script>

<style scoped>
/* ===== 遮罩 + 窗口（单层圆角，最大尺寸） ===== */
.versioning-dialog {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: var(--theme-overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  backdrop-filter: blur(8px);
}

.versioning-card {
  width: min(1100px, 96vw);
  max-width: 96vw;
  height: min(620px, 88vh);
  max-height: 88vh;
  overflow: hidden;
  border-radius: 16px;
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  box-shadow: var(--theme-shadow-strong);
  display: flex;
  flex-direction: column;
  color: var(--text-primary);
}

/* ===== 头部：固定高度，靠分隔线 ===== */
.versioning-header {
  flex: 0 0 auto;
  height: 60px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border-default);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.versioning-header-left {
  display: inline-flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.versioning-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.versioning-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* 自定义开关 */
.switch-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  font-size: 14px;
  cursor: pointer;
  user-select: none;
}

.switch-line input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.switch-line .switch {
  flex: 0 0 auto;
  width: 38px;
  height: 22px;
  border-radius: 999px;
  background: var(--theme-switch-track);
  position: relative;
  transition: background 0.2s ease;
}

.switch-line .switch::after {
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

.switch-line input:checked + .switch {
  background: var(--accent-strong);
}

.switch-line input:checked + .switch::after {
  transform: translateX(16px);
}

.switch-line input:disabled + .switch {
  opacity: 0.5;
}

.switch-text {
  white-space: nowrap;
}

/* ===== 自定义下拉（管理范围） ===== */
.scope-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}

.scope-label {
  white-space: nowrap;
}

.scope-select {
  position: relative;
}

.scope-select-button {
  height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 9px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition:
    background 140ms ease,
    border-color 140ms ease;
}

.scope-select-button:hover:not(:disabled) {
  background: var(--theme-tab-active);
}

.scope-select.disabled .scope-select-button,
.scope-select-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.scope-select-value {
  white-space: nowrap;
}

/* chevron：纯 CSS 箭头，视觉居中 */
.scope-chevron {
  width: 10px;
  height: 10px;
  flex: 0 0 auto;
  position: relative;
}

.scope-chevron::before {
  content: '';
  position: absolute;
  top: 1px;
  left: 1px;
  width: 6px;
  height: 6px;
  border-right: 1.5px solid var(--text-secondary);
  border-bottom: 1.5px solid var(--text-secondary);
  transform: rotate(45deg);
  transition: transform 160ms ease;
}

.scope-select.open .scope-chevron::before {
  transform: rotate(-135deg);
  top: 4px;
}

.scope-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  min-width: 100%;
  width: max-content;
  z-index: 20;
  padding: 6px;
  border: 1px solid var(--border-default);
  border-radius: 12px;
  background: var(--theme-surface-strong);
  box-shadow: var(--theme-shadow-soft);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.scope-menu-option {
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 10px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  transition: background 140ms ease;
}

.scope-menu-option:hover:not(:disabled) {
  background: var(--theme-tab-active);
}

.scope-menu-option.disabled,
.scope-menu-option:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.scope-option-check {
  flex: 0 0 auto;
  color: var(--text-secondary);
}

/* ===== 头部按钮 ===== */
.refresh-btn {
  height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 9px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: background 140ms ease;
}

.refresh-btn:hover:not(:disabled) {
  background: var(--theme-tab-active);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.icon-close-btn {
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

.icon-close-btn:hover {
  background: var(--theme-tab-active);
  color: var(--text-primary);
}

/* ===== 主体：左右分栏靠竖线 ===== */
.versioning-body {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  min-height: 0;
  flex: 1 1 auto;
}

/* 通用滚动容器：隐藏滚动条 */
.scroll-area {
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.scroll-area::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.checkpoint-list {
  border-right: 1px solid var(--border-default);
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 版本点：扁平无边框，固定高度，靠 hover/active 底色区分 */
.checkpoint-item {
  width: 100%;
  height: 78px;
  flex: 0 0 78px;
  box-sizing: border-box;
  text-align: left;
  border: 0;
  border-radius: 10px;
  padding: 0 12px;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 5px;
  transition: background 140ms ease;
}

.checkpoint-item:hover,
.checkpoint-item.active {
  background: var(--theme-tab-active);
}

.checkpoint-item.active {
  position: relative;
}

.checkpoint-item.active::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 16px;
  bottom: 16px;
  width: 3px;
  border-radius: 999px;
  background: var(--text-secondary);
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.row.top {
  font-size: 12px;
  color: var(--text-secondary);
}

.seq {
  font-weight: 700;
  color: var(--text-primary);
}

.preview {
  font-size: 13px;
  line-height: 1.3;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row.stats {
  font-size: 12px;
}

/* diff 红绿增删色：通用语义色，保留固定值，三模式下含义一致 */
.plus {
  color: var(--state-success);
}

.minus {
  color: var(--state-danger);
}

.files {
  color: var(--text-secondary);
}

.checkpoint-detail {
  padding: 14px 16px;
  min-width: 0;
  overflow-x: hidden;
}

.checkpoint-detail h4 {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 700;
}

.summary {
  display: flex;
  gap: 12px;
  font-size: 13px;
  margin-bottom: 12px;
}

.file-list {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
}

/* 文件行：固定高度，靠虚线分隔 */
.file-row {
  display: grid;
  grid-template-columns: 52px 1fr auto;
  gap: 10px;
  align-items: center;
  min-height: 40px;
  border-bottom: 1px solid var(--border-default);
  padding: 0 4px;
  font-size: 13px;
  cursor: pointer;
  border-radius: 8px;
  transition: background 140ms ease;
}

.file-row:hover {
  background: var(--theme-tab-active);
}

.file-row.expanded {
  background: var(--theme-tab-active);
}

.status {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--text-secondary);
}

.path {
  word-break: break-all;
  color: var(--text-primary);
}

.delta {
  display: inline-flex;
  gap: 8px;
}

.loading,
.empty {
  color: var(--text-secondary);
  font-size: 13px;
  padding: 12px 4px;
}

/* ===== 底部：固定高度，靠分隔线 ===== */
.versioning-footer {
  flex: 0 0 auto;
  min-height: 60px;
  padding: 12px 18px;
  border-top: 1px solid var(--border-default);
  display: flex;
  align-items: center;
  gap: 16px;
  justify-content: flex-end;
}

.restore-options {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-right: auto;
}

.restore-option-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.restore-option-label {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.restore-option-buttons {
  display: flex;
  align-items: center;
  gap: 6px;
}

.restore-option-btn {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 9px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 140ms ease,
    border-color 140ms ease,
    color 140ms ease;
}

.restore-option-btn:hover:not(:disabled) {
  background: var(--theme-tab-active);
}

.restore-option-btn.active {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.restore-option-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.restore-option-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.confirm-btn {
  height: 38px;
  border: 0;
  border-radius: 10px;
  padding: 0 18px;
  font-size: 14px;
  font-weight: 600;
  background: var(--accent);
  color: var(--on-accent);
  cursor: pointer;
  transition: background 140ms ease;
}

.confirm-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== diff 展示：与文件编辑差异保持一致 ===== */
.tool-result-diff {
  margin-top: 10px;
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

.tool-result-diff.scroll-area {
  max-height: calc(8 * 1.5em + 16px);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 8px;
  background: var(--theme-surface-soft);
  overflow-x: auto;
}

.diff-line {
  display: grid;
  grid-template-columns: 1.5ch minmax(0, 1fr);
  column-gap: 6px;
  padding: 2px 4px;
  margin: 0;
  align-items: start;
  white-space: pre;
  min-width: 0;
}

.diff-marker {
  color: var(--text-secondary);
  text-align: center;
  user-select: none;
}

.diff-content {
  min-width: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* diff 增删色：通用语义色，保留固定值 */
.diff-remove {
  background: var(--diff-del-bg);
  color: var(--state-danger);
}

.diff-add {
  background: var(--diff-add-bg);
  color: var(--state-success);
}

.diff-context {
  background: transparent;
  color: var(--text-secondary);
}

.approval-note {
  padding: 6px 8px;
  font-size: 11px;
  color: var(--text-secondary);
}

@media (max-width: 860px) {
  .versioning-body {
    grid-template-columns: 1fr;
    grid-template-rows: 1fr 1fr;
  }
  .checkpoint-list {
    border-right: none;
    border-bottom: 1px solid var(--border-default);
  }
}
</style>

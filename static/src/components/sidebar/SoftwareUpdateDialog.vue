<template>
  <Teleport to="body">
    <transition name="update-dialog-fade">
      <div
        v-if="store.dialogOpen"
        class="update-dialog-overlay"
        @click.self="store.closeDialog()"
      >
        <div class="update-dialog" role="dialog" :aria-label="$t('update.dialogTitle')">
          <div class="update-dialog-header">
            <span class="update-dialog-title">{{ $t('update.dialogTitle') }}</span>
            <button
              v-if="!store.installBusy"
              type="button"
              class="update-dialog-close"
              :title="$t('common.close')"
              @click="store.closeDialog()"
            >
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <path d="M3 3l10 10M13 3L3 13" />
              </svg>
            </button>
          </div>

          <!-- 检查中 -->
          <div v-if="store.checking && !store.progress" class="update-dialog-body update-dialog-body--center">
            <span class="update-spinner" aria-hidden="true"></span>
            <span class="update-status-text">{{ $t('update.checking') }}</span>
          </div>

          <!-- 安装流程（优先于检查结果展示） -->
          <div v-else-if="store.progress && store.progress.state !== 'idle'" class="update-dialog-body">
            <template v-if="store.progress.state === 'error'">
              <div class="update-result-row update-result-row--error">
                <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
                  <circle cx="10" cy="10" r="8" />
                  <path d="M10 6v5M10 13.8v.2" />
                </svg>
                <span class="update-status-text">{{ $t('update.updateFailed') }}</span>
              </div>
              <div class="update-error-detail">{{ store.progress.error }}</div>
              <div class="update-dialog-actions">
                <button type="button" class="update-btn update-btn--ghost" @click="store.closeDialog()">
                  {{ $t('common.close') }}
                </button>
                <button type="button" class="update-btn update-btn--primary" @click="store.startInstall()">
                  {{ $t('common.retry') }}
                </button>
              </div>
            </template>
            <template v-else>
              <div class="update-status-line">
                <span class="update-status-text">{{ progressText }}</span>
                <span v-if="store.progress.state === 'downloading' && store.progress.total" class="update-progress-num">
                  {{ downloadText }}
                </span>
              </div>
              <div class="update-progress-track">
                <div
                  class="update-progress-fill"
                  :class="{ 'update-progress-fill--indeterminate': !store.progress.total }"
                  :style="{ width: progressPercent + '%' }"
                ></div>
              </div>
              <div class="update-hint">{{ $t('update.installing') }}</div>
            </template>
          </div>

          <!-- 检查失败 -->
          <div v-else-if="store.checkError" class="update-dialog-body">
            <div class="update-result-row update-result-row--error">
              <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
                <circle cx="10" cy="10" r="8" />
                <path d="M10 6v5M10 13.8v.2" />
              </svg>
              <span class="update-status-text">{{ $t('update.checkFailed') }}</span>
            </div>
            <div class="update-error-detail">{{ store.checkError }}</div>
            <div class="update-dialog-actions">
              <button type="button" class="update-btn update-btn--ghost" @click="store.closeDialog()">
                {{ $t('common.close') }}
              </button>
              <button type="button" class="update-btn update-btn--primary" @click="store.checkUpdate({ force: true })">
                {{ $t('common.retry') }}
              </button>
            </div>
          </div>

          <!-- 检查结果 -->
          <div v-else-if="store.result" class="update-dialog-body">
            <template v-if="store.result.update_available">
              <div class="update-result-row update-result-row--accent">
                <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M10 3v9M6.5 8.5L10 12l3.5-3.5" />
                  <path d="M4 15.5h12" />
                </svg>
                <span class="update-status-text">{{ $t('update.newVersionAvailable') }}</span>
              </div>
              <div class="update-version-line">
                <span class="update-version-item">
                  <span class="update-version-label">{{ $t('update.currentVersion') }}</span>
                  <span class="update-version-value">v{{ store.result.current_version }}</span>
                </span>
                <span class="update-version-arrow" aria-hidden="true">→</span>
                <span class="update-version-item">
                  <span class="update-version-label">{{ $t('update.latestVersion') }}</span>
                  <span class="update-version-value update-version-value--accent">v{{ store.result.latest_version }}</span>
                </span>
              </div>
              <div class="update-notes-block">
                <div class="update-notes-title">
                  {{ $t('update.releaseNotes') }}
                  <span v-if="store.result.pub_date" class="update-notes-date">{{ $t('update.publishedAt', { date: pubDateText }) }}</span>
                </div>
                <div class="update-notes-content">{{ store.result.notes || $t('update.noReleaseNotes') }}</div>
              </div>
              <div class="update-dialog-actions">
                <button type="button" class="update-btn update-btn--ghost" @click="store.closeDialog()">
                  {{ $t('update.later') }}
                </button>
                <button type="button" class="update-btn update-btn--primary" @click="store.startInstall()">
                  {{ $t('update.updateNow') }}
                </button>
              </div>
            </template>
            <template v-else>
              <div class="update-result-row update-result-row--success">
                <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="10" cy="10" r="8" />
                  <path d="M6.5 10.2l2.4 2.4 4.6-4.8" />
                </svg>
                <span class="update-status-text">{{ $t('update.upToDate') }}</span>
              </div>
              <div class="update-version-line update-version-line--single">
                <span class="update-version-item">
                  <span class="update-version-label">{{ $t('update.currentVersion') }}</span>
                  <span class="update-version-value">v{{ store.result.current_version }}</span>
                </span>
              </div>
              <div class="update-dialog-actions">
                <button type="button" class="update-btn update-btn--ghost" @click="store.closeDialog()">
                  {{ $t('common.close') }}
                </button>
                <button type="button" class="update-btn update-btn--primary" @click="store.checkUpdate({ force: true })">
                  {{ $t('update.recheck') }}
                </button>
              </div>
            </template>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useDesktopUpdateStore } from '@/stores/desktopUpdate';

const store = useDesktopUpdateStore();
const { t, locale } = useI18n();

/** 下载/安装阶段文案（与桥 state 对齐；restarting 与 installing 共用一句） */
const progressText = computed(() => {
  const s = store.progress?.state;
  if (s === 'downloading') return t('update.downloading');
  if (s === 'checking') return t('update.checking');
  return t('update.installing');
});

/** 下载百分比（total 未知时为 0，配合 indeterminate 动画） */
const progressPercent = computed(() => {
  const p = store.progress;
  if (!p) return 0;
  if (p.state === 'installing' || p.state === 'restarting') return 100;
  if (!p.total) return 0;
  return Math.min(100, Math.round((p.downloaded / p.total) * 100));
});

function formatMB(bytes: number): string {
  return (bytes / 1024 / 1024).toFixed(1);
}

/** 「82.4 MB / 145.0 MB」 */
const downloadText = computed(() => {
  const p = store.progress;
  if (!p) return '';
  if (!p.total) return `${formatMB(p.downloaded)} MB`;
  return `${formatMB(p.downloaded)} / ${formatMB(p.total)} MB`;
});

/** 发布日期本地化（原值为 ISO 串，解析失败原样展示） */
const pubDateText = computed(() => {
  const raw = store.result?.pub_date || '';
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return raw;
  return at.toLocaleDateString(locale.value === 'zh-CN' ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
});
</script>

<style scoped>
/* 遮罩（scrim 是全站唯一允许半透明/叠层的场景） */
.update-dialog-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: grid;
  place-items: center;
  z-index: var(--z-modal, 1000);
}

/* 实体面板：不透明、无磨砂、无 glow（§5.5） */
.update-dialog {
  width: min(440px, calc(100vw - 48px));
  max-height: min(560px, calc(100vh - 96px));
  display: flex;
  flex-direction: column;
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  box-shadow: var(--shadow-strong);
  overflow: hidden;
}

/* 头部：分隔线区分而非深色衬底（对齐 VersioningDialog 约定） */
.update-dialog-header {
  flex: 0 0 auto;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-default);
}

.update-dialog-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.update-dialog-close {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease;
}

.update-dialog-close:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.update-dialog-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.update-dialog-body--center {
  align-items: center;
  justify-content: center;
  min-height: 140px;
}

.update-spinner {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid var(--border-default);
  border-top-color: var(--accent);
  animation: update-spin 0.8s linear infinite;
}

@keyframes update-spin {
  to {
    transform: rotate(360deg);
  }
}

.update-result-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}

.update-result-row--success {
  color: var(--state-success);
}

.update-result-row--accent {
  color: var(--accent);
}

.update-result-row--error {
  color: var(--state-danger);
}

.update-status-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.update-error-detail {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-tertiary);
  word-break: break-all;
  max-height: 72px;
  overflow-y: auto;
}

.update-version-line {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 40px;
}

.update-version-line--single {
  gap: 0;
}

.update-version-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.update-version-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.update-version-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.update-version-value--accent {
  color: var(--accent);
}

.update-version-arrow {
  color: var(--text-tertiary);
  font-size: 14px;
}

.update-notes-block {
  border-top: 1px solid var(--border-default);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.update-notes-title {
  height: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.update-notes-date {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
}

.update-notes-content {
  max-height: 160px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.update-status-line {
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.update-progress-num {
  font-size: 12px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.update-progress-track {
  height: 6px;
  border-radius: 3px;
  background: var(--progress-track);
  overflow: hidden;
}

.update-progress-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 300ms ease;
}

.update-progress-fill--indeterminate {
  width: 40% !important;
  animation: update-indeterminate 1.2s ease-in-out infinite;
}

@keyframes update-indeterminate {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(260%);
  }
}

.update-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  min-height: 18px;
}

.update-dialog-actions {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}

.update-btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease, border-color 140ms ease;
  white-space: nowrap;
}

.update-btn--primary {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.update-btn--primary:hover {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}

.update-btn--ghost {
  border: 1px solid var(--border-default);
  background: transparent;
  color: var(--text-primary);
}

.update-btn--ghost:hover {
  background: var(--hover-bg);
}

/* 过渡 */
.update-dialog-fade-enter-active,
.update-dialog-fade-leave-active {
  transition: opacity 160ms ease;
}

.update-dialog-fade-enter-from,
.update-dialog-fade-leave-to {
  opacity: 0;
}
</style>

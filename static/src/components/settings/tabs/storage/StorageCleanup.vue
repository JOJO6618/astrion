<script setup lang="ts">
import { computed, inject, ref } from 'vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import { formatBytes, parseTimestamp } from './format';
import type { CleanBackupsResult, CleanLogsResult, StorageData } from './types';

const props = defineProps<{ data: StorageData }>();
const emit = defineEmits<{ (e: 'cleaned'): void }>();

const uiStore = useUiStore();
/** 下拉沿用设置页共享上下文（与其它分区同一套浮层定位与外部点击关闭） */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { activeDropdown, activeTheme, closeDropdown, floatingMenuStyle, toggleDropdown } = ctx;

const KEEP_PRESETS = [7, 30, 90];
const DEFAULT_KEEP_DAYS = 30;

const keepDays = ref(DEFAULT_KEEP_DAYS);
const customMode = ref(false);
const customInput = ref(String(DEFAULT_KEEP_DAYS));
const customError = ref('');
const backupRunning = ref(false);
const logsRunning = ref(false);

const keepText = computed(() => t('storageStats.keepDays', { n: keepDays.value }));

const keepOptions = computed(() => [
  ...KEEP_PRESETS.map((days) => ({
    id: String(days),
    label: t('storageStats.keepDays', { n: days })
  })),
  { id: 'custom', label: t('storageStats.keepCustom') }
]);

const logsBytes = computed(
  () => props.data.categories.find((item) => item.id === 'logs')?.bytes || 0
);

/** 按当前保留天数预估将被删除的备份（后端执行时会再次校验） */
const pending = computed(() => {
  const cutoff = Date.now() - keepDays.value * 86400000;
  let count = 0;
  let bytes = 0;
  for (const item of props.data.backups) {
    const ts = parseTimestamp(item.updated_at);
    if (ts !== null && ts < cutoff) {
      count += 1;
      bytes += item.bytes;
    }
  }
  return { count, bytes };
});

function pickKeep(id: string) {
  if (id === 'custom') {
    customInput.value = String(keepDays.value);
    customError.value = '';
    customMode.value = true;
  } else {
    keepDays.value = Number(id);
    customMode.value = false;
    customError.value = '';
  }
  closeDropdown();
}

function applyCustom(strict = true) {
  const value = Number(String(customInput.value).trim());
  const valid = Number.isInteger(value) && value >= 1 && value <= 3650;
  if (!valid) {
    if (strict) {
      customError.value = t('storageStats.customHint');
      return;
    }
    // 失焦/Esc 时静默退出编辑态，不把校验错误留在页面上
    customMode.value = false;
    customError.value = '';
    return;
  }
  keepDays.value = value;
  customMode.value = false;
  customError.value = '';
}

async function runCleanBackups() {
  if (backupRunning.value || pending.value.count === 0) return;
  const ok = await uiStore.requestConfirm({
    title: t('storageStats.confirmBackupTitle'),
    message: t('storageStats.confirmBackupMessage', {
      count: pending.value.count,
      days: keepDays.value,
      size: formatBytes(pending.value.bytes)
    }),
    warningText: t('storageStats.confirmBackupWarning'),
    confirmText: t('storageStats.confirmButton'),
    cancelText: t('storageStats.cancelButton'),
    confirmVariant: 'danger'
  });
  if (!ok) return;

  backupRunning.value = true;
  try {
    const resp = await fetch('/api/storage-stats/clean/backups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_days: keepDays.value })
    });
    const payload = await resp.json().catch(() => null);
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || `HTTP ${resp.status}`);
    }
    const result = payload.data as CleanBackupsResult;
    uiStore.pushToast({
      message: t('storageStats.cleanBackupDone', {
        count: result.removed_count,
        size: formatBytes(result.freed_bytes)
      }),
      type: 'success'
    });
    if (result.failed?.length) {
      uiStore.pushToast({
        message: t('storageStats.cleanPartial', { count: result.failed.length }),
        type: 'error'
      });
    }
    emit('cleaned');
  } catch (err: any) {
    uiStore.pushToast({
      message: t('storageStats.cleanFailed', { error: err?.message || String(err) }),
      type: 'error'
    });
  } finally {
    backupRunning.value = false;
  }
}

async function runCleanLogs() {
  if (logsRunning.value || logsBytes.value === 0) return;
  const ok = await uiStore.requestConfirm({
    title: t('storageStats.confirmLogsTitle'),
    message: t('storageStats.confirmLogsMessage', { size: formatBytes(logsBytes.value) }),
    warningText: t('storageStats.confirmLogsWarning'),
    confirmText: t('storageStats.confirmButton'),
    cancelText: t('storageStats.cancelButton'),
    confirmVariant: 'danger'
  });
  if (!ok) return;

  logsRunning.value = true;
  try {
    const resp = await fetch('/api/storage-stats/clean/logs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
    const payload = await resp.json().catch(() => null);
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || `HTTP ${resp.status}`);
    }
    const result = payload.data as CleanLogsResult;
    uiStore.pushToast({
      message: t('storageStats.cleanLogsDone', { size: formatBytes(result.freed_bytes) }),
      type: 'success'
    });
    if (result.failed?.length) {
      uiStore.pushToast({
        message: t('storageStats.cleanPartial', { count: result.failed.length }),
        type: 'error'
      });
    }
    emit('cleaned');
  } catch (err: any) {
    uiStore.pushToast({
      message: t('storageStats.cleanFailed', { error: err?.message || String(err) }),
      type: 'error'
    });
  } finally {
    logsRunning.value = false;
  }
}
</script>

<template>
  <section class="storage-clean">
    <div class="storage-clean-head">
      <h3>{{ $t('storageStats.cleanupTitle') }}</h3>
      <p>{{ $t('storageStats.cleanupDesc') }}</p>
    </div>

    <!-- 对话备份 -->
    <div class="storage-clean-row">
      <div class="storage-clean-info">
        <div class="storage-clean-title">{{ $t('storageStats.backupRowTitle') }}</div>
        <div class="storage-clean-desc">{{ $t('storageStats.backupRowDesc') }}</div>
        <div class="storage-clean-hint" :class="{ 'is-empty': pending.count === 0 }">
          {{
            pending.count > 0
              ? $t('storageStats.willRemove', {
                  count: pending.count,
                  size: formatBytes(pending.bytes)
                })
              : $t('storageStats.nothingToRemove')
          }}
        </div>
      </div>
      <div class="storage-clean-actions" @click.stop>
        <div v-if="customMode" class="storage-custom">
          <input
            v-model="customInput"
            class="storage-custom-input"
            type="text"
            inputmode="numeric"
            :placeholder="$t('storageStats.customPlaceholder')"
            @keydown.enter.prevent="applyCustom(true)"
            @keydown.esc="applyCustom(false)"
            @blur="applyCustom(false)"
          />
          <span class="storage-custom-unit">{{ $t('storageStats.keepDaysUnit') }}</span>
        </div>
        <div
          v-else
          class="settings-select-wrap"
          :class="{ open: activeDropdown === 'storage-keep' }"
        >
          <button
            type="button"
            class="settings-select-button storage-select-button"
            @click="toggleDropdown('storage-keep')"
          >
            <span class="storage-ellipsis">
              {{ $t('storageStats.keepLabel') }} {{ keepText }}
            </span>
            <span class="select-chevron" aria-hidden="true"></span>
          </button>
          <div
            class="settings-floating-menu"
            :class="{ dark: activeTheme === 'dark' }"
            :style="activeDropdown === 'storage-keep' ? floatingMenuStyle : undefined"
          >
            <button
              v-for="option in keepOptions"
              :key="option.id"
              type="button"
              class="settings-menu-option"
              :class="{ selected: option.id === String(keepDays) }"
              @click="pickKeep(option.id)"
            >
              <strong>{{ option.label }}</strong>
              <svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
            </button>
          </div>
        </div>
        <button
          type="button"
          class="storage-danger-button"
          :disabled="backupRunning || pending.count === 0"
          @click="runCleanBackups"
        >
          {{ backupRunning ? $t('storageStats.cleaning') : $t('storageStats.cleanButton') }}
        </button>
      </div>
    </div>

    <p v-if="customError" class="storage-custom-error">{{ customError }}</p>

    <!-- 日志 -->
    <div class="storage-clean-row">
      <div class="storage-clean-info">
        <div class="storage-clean-title">{{ $t('storageStats.logRowTitle') }}</div>
        <div class="storage-clean-desc">{{ $t('storageStats.logRowDesc') }}</div>
        <div class="storage-clean-hint">{{ formatBytes(logsBytes) }}</div>
      </div>
      <div class="storage-clean-actions">
        <button
          type="button"
          class="storage-danger-button"
          :disabled="logsRunning || logsBytes === 0"
          @click="runCleanLogs"
        >
          {{ logsRunning ? $t('storageStats.cleaning') : $t('storageStats.cleanLogsButton') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.storage-clean {
  margin-top: 10px;
  padding-top: 22px;
  border-top: 1px solid var(--border-default);
}

.storage-clean-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.storage-clean-head p {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-tertiary);
}

.storage-clean-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
  padding: 16px 0;
  border-bottom: 1px solid var(--border-default);
}

.storage-clean-info {
  flex: 1;
  min-width: 0;
}

.storage-clean-title {
  font-size: 13px;
}

.storage-clean-desc {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-tertiary);
}

.storage-clean-hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.storage-clean-hint.is-empty {
  color: var(--text-tertiary);
}

.storage-clean-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.storage-select-button {
  min-width: 136px;
  border: 1px solid var(--border-default);
}

.storage-ellipsis {
  max-width: 170px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-custom {
  display: flex;
  align-items: center;
  gap: 6px;
}

.storage-custom-input {
  width: 76px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.storage-custom-input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.storage-custom-unit {
  font-size: 12px;
  color: var(--text-tertiary);
}

.storage-custom-error {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--state-danger);
}

.storage-danger-button {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  color: var(--state-danger);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.storage-danger-button:hover:not(:disabled) {
  background: var(--hover-bg);
}

.storage-danger-button:disabled {
  color: var(--text-muted);
  cursor: default;
}

.storage-danger-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
</style>

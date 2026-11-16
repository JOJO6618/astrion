<script setup lang="ts">
import { computed, ref } from 'vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import { formatBytes, parseTimestamp } from './format';
import type { CleanBackupsResult, CleanLogsResult, StorageData } from './types';

const props = defineProps<{ data: StorageData }>();
const emit = defineEmits<{ (e: 'cleaned'): void }>();

const uiStore = useUiStore();
const KEEP_PRESETS = [7, 30, 90];
const DEFAULT_KEEP_DAYS = 30;

const keepDays = ref(DEFAULT_KEEP_DAYS);
const customMode = ref(false);
const customInput = ref(String(DEFAULT_KEEP_DAYS));
const customError = ref('');
const backupRunning = ref(false);
const logsRunning = ref(false);

const keepOptions = computed(() => [
  ...KEEP_PRESETS.map((days) => ({
    value: String(days),
    label: t('storageStats.keepDays', { n: days })
  })),
  { value: 'custom', label: t('storageStats.keepCustom') }
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
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('storageStats.cleanupTitle') }}</span>
    </div>

    <!-- 对话备份：保留时长（下拉 / 自定义天数）与清理拆成两行，每行右侧只有一个控件 -->
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('storageStats.keepRowTitle') }}</span>
          <span class="stg-row-desc">{{ $t('storageStats.keepRowDesc') }}</span>
        </span>
        <input
          v-if="customMode"
          v-model="customInput"
          class="stg-number-input"
          type="text"
          inputmode="numeric"
          :placeholder="$t('storageStats.customPlaceholder')"
          :aria-label="$t('storageStats.keepRowTitle')"
          @keydown.enter.prevent="applyCustom(true)"
          @keydown.esc="applyCustom(false)"
          @blur="applyCustom(false)"
        />
        <UiSelect
          v-else
          :model-value="String(keepDays)"
          :options="keepOptions"
          align-end
          @update:model-value="pickKeep"
        />
      </div>
      <p v-if="customError" class="storage-custom-error">{{ customError }}</p>

      <div class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('storageStats.backupRowTitle') }}</span>
          <span class="stg-row-desc">{{
            pending.count > 0
              ? $t('storageStats.willRemove', {
                  count: pending.count,
                  size: formatBytes(pending.bytes)
                })
              : $t('storageStats.nothingToRemove')
          }}</span>
        </span>
        <button
          type="button"
          class="stg-secondary-button danger"
          :disabled="backupRunning || pending.count === 0"
          @click="runCleanBackups"
        >
          {{ backupRunning ? $t('storageStats.cleaning') : $t('storageStats.cleanButton') }}
        </button>
      </div>

      <div class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('storageStats.logRowTitle') }}</span>
          <span class="stg-row-desc">{{ formatBytes(logsBytes) }}</span>
        </span>
        <button
          type="button"
          class="stg-secondary-button danger"
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
}

.storage-custom-error {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--state-danger);
}
</style>

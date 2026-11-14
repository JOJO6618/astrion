<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { t } from '@/locales';
import StorageBar from './storage/StorageBar.vue';
import StorageCleanup from './storage/StorageCleanup.vue';
import StorageLegend from './storage/StorageLegend.vue';
import { formatBytes } from './storage/format';
import { useStorageStats } from './storage/useStorageStats';

defineOptions({ name: 'StorageTab' });

const { data, loading, busy, error, load, reload } = useStorageStats();

const totalText = computed(() => (data.value ? formatBytes(data.value.total_bytes) : '—'));

const scannedText = computed(() => {
  const raw = data.value?.scanned_at;
  if (!raw) return t('storageStats.neverScanned');
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return '';
  return t('storageStats.lastScan', {
    time: date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  });
});

const unsupported = computed(() => data.value?.available === false);

onMounted(load);
</script>

<template>
  <section class="settings-page storage-page">
    <div class="storage-toolbar">
      <span class="storage-toolbar-meta">{{ scannedText }}</span>
      <button
        type="button"
        class="settings-secondary-button"
        :disabled="busy"
        @click="load"
      >
        {{ busy ? $t('storageStats.scanning') : $t('storageStats.refresh') }}
      </button>
    </div>

    <p v-if="error" class="storage-error">
      {{ $t('storageStats.loadFailed', { error }) }}
    </p>
    <p v-else-if="unsupported" class="storage-placeholder">
      {{ $t('storageStats.unsupported') }}
    </p>
    <p v-else-if="!data" class="storage-placeholder">{{ $t('storageStats.loading') }}</p>

    <template v-else-if="data.available">
      <div class="storage-total">
        <div class="storage-total-line">
          <span class="storage-total-label">{{ $t('storageStats.totalUsed') }}</span>
          <span class="storage-total-value">{{ totalText }}</span>
        </div>
        <div class="storage-root" v-if="data.root">{{ data.root }}</div>
      </div>

      <StorageBar :categories="data.categories" :total="data.total_bytes" />
      <StorageLegend :categories="data.categories" :total="data.total_bytes" />

      <p v-if="data.total_bytes === 0 && !loading" class="storage-placeholder">
        {{ $t('storageStats.empty') }}
      </p>

      <StorageCleanup :data="data" @cleaned="reload" />
    </template>
  </section>
</template>

<style scoped>
.storage-page {
  display: flex;
  flex-direction: column;
}

.storage-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
}

.storage-toolbar-meta {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-tertiary);
}

.storage-error {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--state-danger);
}

.storage-placeholder {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--text-tertiary);
}

.storage-total {
  padding: 2px 0 18px;
}

.storage-total-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.storage-total-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.storage-total-value {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.storage-root {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}
</style>

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
  <section class="stg-page storage-page">
    <!-- 总占用：状态写进说明，右侧只放「刷新」 -->
    <div class="stg-card">
      <div class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title"
            >{{ $t('storageStats.totalUsed')
            }}<template v-if="data?.available"> {{ totalText }}</template></span
          >
          <span class="stg-row-desc">{{
            data?.root ? `${data.root} · ${scannedText}` : scannedText
          }}</span>
        </span>
        <button type="button" class="stg-secondary-button" :disabled="busy" @click="load">
          {{ busy ? $t('storageStats.scanning') : $t('storageStats.refresh') }}
        </button>
      </div>
    </div>

    <p v-if="error" class="storage-error">
      {{ $t('storageStats.loadFailed', { error }) }}
    </p>
    <p v-else-if="unsupported" class="storage-placeholder">
      {{ $t('storageStats.unsupported') }}
    </p>
    <p v-else-if="!data" class="storage-placeholder">{{ $t('storageStats.loading') }}</p>

    <template v-else-if="data.available">
      <div class="stg-card storage-overview">
        <StorageBar :categories="data.categories" :total="data.total_bytes" />
        <StorageLegend :categories="data.categories" :total="data.total_bytes" />

        <p v-if="data.total_bytes === 0 && !loading" class="storage-placeholder">
          {{ $t('storageStats.empty') }}
        </p>
      </div>

      <StorageCleanup :data="data" @cleaned="reload" />
    </template>
  </section>
</template>

<style scoped>
.storage-page {
  display: flex;
  flex-direction: column;
}

/* 占用条与图例：图形内容四周统一 16 */
.storage-overview {
  padding: 16px;
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
</style>

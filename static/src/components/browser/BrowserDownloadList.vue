<template>
  <div v-if="!rows.length" class="nb-download-empty">{{ $t('browser.emptyDownloads') }}</div>
  <div v-for="row in rows" :key="row.id" class="nb-download-row" :class="{ compact }">
    <BrowserIcon name="file" />
    <div class="nb-download-main">
      <span>{{ row.name }}</span>
      <small v-if="row.state === 'completed'">{{ bytes(row.total || row.received) }}</small>
      <small v-else
        >{{ bytes(row.received) }}<template v-if="row.total"> / {{ bytes(row.total) }}</template> ·
        {{ stateLabel(row.state)
        }}<template v-if="row.state === 'progressing' && row.total">
          {{ Math.min(100, Math.round((row.received / row.total) * 100)) }}%</template
        ></small
      >
    </div>
    <BrowserButton
      v-if="row.state === 'progressing'"
      name="x"
      :label="$t('browser.cancelDownload')"
      @click="store.command('download-cancel', undefined, row.id)"
    />
    <template v-else>
      <BrowserButton
        v-if="row.state === 'completed'"
        name="folderOpen"
        :label="$t('browser.reveal')"
        @click="store.command('download-reveal', undefined, row.id)"
      />
      <BrowserButton
        v-if="!compact"
        name="x"
        :label="$t('browser.remove')"
        @click="store.command('download-remove', undefined, row.id)"
      />
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import { t } from '@/locales';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import BrowserButton from './BrowserButton.vue';
import BrowserIcon from './BrowserIcon.vue';
const props = defineProps<{ compact?: boolean; limit?: number }>();
const store = useNativeBrowserStore();
const rows = computed(() => (store.snapshot?.downloads || []).slice(0, props.limit));
const stateLabel = (value: string) =>
  ['completed', 'progressing', 'interrupted', 'cancelled'].includes(value)
    ? t(`browser.${value}`)
    : value;
function bytes(value: number) {
  return value < 1024
    ? `${value} B`
    : value < 1048576
      ? `${(value / 1024).toFixed(1)} KB`
      : `${(value / 1048576).toFixed(1)} MB`;
}
</script>
<style scoped>
.nb-download-empty {
  height: 72px;
  display: grid;
  place-items: center;
  color: var(--text-tertiary);
  font-size: 12px;
}
.nb-download-row {
  height: 70px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--border-default);
}
.nb-download-row.compact {
  height: 58px;
  padding: 0 10px;
}
.nb-download-row:last-child {
  border-bottom: 0;
}
.nb-download-row > .nb-icon {
  color: var(--text-secondary);
}
.nb-download-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nb-download-main span,
.nb-download-main small {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nb-download-main span {
  font-size: 12px;
}
.nb-download-main small {
  font-size: 10px;
  color: var(--text-tertiary);
}
</style>

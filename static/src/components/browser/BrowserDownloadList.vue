<template>
  <div v-if="!rows.length" class="nb-download-empty">{{ $t('browser.emptyDownloads') }}</div>
  <div
    v-for="row in rows"
    :key="row.id"
    class="nb-download-row"
    :class="{ compact, transferring: row.state === 'progressing' }"
  >
    <BrowserIcon name="file" />
    <div class="nb-download-main">
      <span>{{ row.name }}</span>
      <small v-if="row.state === 'completed'">{{ formatBytes(row.received) }}</small>
      <small v-else>{{ downloadProgress(row) }}</small>
      <small v-if="row.bytesPerSecond || row.estimatedCompletionAt">{{
        downloadTransfer(row)
      }}</small>
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
        :label="$t(isWindowsDesktopShell() ? 'browser.revealWindows' : 'browser.reveal')"
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
import { isWindowsDesktopShell } from '@/utils/desktopPlatform';
import { formatBytes } from '@/utils/formatters';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import BrowserButton from './BrowserButton.vue';
import BrowserIcon from './BrowserIcon.vue';
import { downloadProgress, downloadTransfer } from './downloadPresentation';

const props = defineProps<{ compact?: boolean; limit?: number }>();
const store = useNativeBrowserStore();
const rows = computed(() => (store.snapshot?.downloads || []).slice(0, props.limit));
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
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px 0 8px;
  border-radius: 8px;
}
.nb-download-row + .nb-download-row {
  margin-top: 2px;
}
.nb-download-row.compact {
  height: 52px;
}
.nb-download-row.transferring {
  height: 68px;
}
.nb-download-row > .nb-icon {
  color: var(--text-secondary);
}
.nb-download-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nb-download-main span,
.nb-download-main small {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nb-download-main span {
  font-size: 13px;
  line-height: 20px;
}
.nb-download-main small {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-tertiary);
}
</style>

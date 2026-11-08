<template>
  <section
    class="nb-popover nb-recent-downloads"
    role="dialog"
    :aria-label="$t('browser.downloads')"
  >
    <header>
      <strong>{{ $t('browser.downloads') }}</strong>
      <button class="nb-text-button" @click="menuAction('downloads')">
        {{ $t('browser.allDownloads') }}
      </button>
    </header>
    <BrowserDownloadList compact :limit="6" />
  </section>
</template>
<script setup lang="ts">
import { inject } from 'vue';
import { browserControlsKey } from './useBrowserControls';
import BrowserDownloadList from './BrowserDownloadList.vue';
const { menuAction } = inject(browserControlsKey)!;
</script>
<style scoped>
.nb-recent-downloads {
  top: 88px;
  right: 43px;
  width: 350px;
  max-width: calc(100% - 52px);
  padding: 0;
}
.nb-recent-downloads header {
  height: 42px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid var(--border-default);
}
.nb-recent-downloads strong {
  font-size: 12px;
  font-weight: 600;
}
.nb-recent-downloads header button {
  font-size: 11px;
}
</style>

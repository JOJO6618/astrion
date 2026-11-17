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
}
/* 标题行与下方列表靠留白区分，不画分隔线；标题与行内文字同在 8px 文字线 */
.nb-recent-downloads header {
  height: 40px;
  padding: 0 0 0 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.nb-recent-downloads strong {
  font-size: 13px;
  font-weight: 600;
}
</style>

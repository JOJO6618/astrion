<template>
  <div
    class="app-root"
    :class="{ 'native-browser-visible': browserVisible }"
    :style="browserVisible ? { '--native-browser-width': `${browser.width}px` } : undefined"
  >
    <DetachedTopBar />
    <QuotaToast />
    <ToastStack />
    <ConfirmDialog />
    <slot />
    <NativeBrowserHost v-if="browserAvailable" />
    <EasterEggOverlay />
    <FileContextMenu :download-file="downloadFile" :download-folder="downloadFolder" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import ConfirmDialog from '@/components/shell/ConfirmDialog.vue';
import EasterEggOverlay from '@/components/shell/EasterEggOverlay.vue';
import FileContextMenu from '@/components/shell/FileContextMenu.vue';
import DetachedTopBar from '@/components/shell/DetachedTopBar.vue';
import QuotaToast from '@/components/shell/QuotaToast.vue';
import ToastStack from '@/components/shell/ToastStack.vue';
import NativeBrowserHost from '@/components/browser/NativeBrowserHost.vue';
import { useNativeBrowserStore, nativeBrowserAvailable } from '@/stores/nativeBrowser';
import { browserConversationPage } from '@/components/browser/browserScope';
const browser = useNativeBrowserStore();
const browserAvailable = nativeBrowserAvailable();
const browserVisible = computed(
  () => browser.open && browserAvailable && browserConversationPage(window.location.pathname)
);

defineProps<{
  downloadFile: (path: string) => void;
  downloadFolder: (path: string) => void;
}>();
</script>

<style scoped>
.app-root.native-browser-visible {
  box-sizing: border-box;
  padding-right: min(var(--native-browser-width), calc(100vw - 360px));
}
</style>

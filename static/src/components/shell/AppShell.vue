<template>
  <div class="app-root">
    <QuotaToast />
    <ToastStack />
    <ConfirmDialog />
    <slot />
    <EasterEggOverlay />
    <FileContextMenu :download-file="downloadFile" :download-folder="downloadFolder" />
  </div>
</template>

<script setup lang="ts">
import ConfirmDialog from '@/components/shell/ConfirmDialog.vue';
import EasterEggOverlay from '@/components/shell/EasterEggOverlay.vue';
import FileContextMenu from '@/components/shell/FileContextMenu.vue';
import QuotaToast from '@/components/shell/QuotaToast.vue';
import ToastStack from '@/components/shell/ToastStack.vue';

defineProps<{
  downloadFile: (path: string) => void;
  downloadFolder: (path: string) => void;
}>();
</script>

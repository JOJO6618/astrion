<template>
  <div
    class="context-menu"
    v-if="contextMenu.visible"
    :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
    @click.stop
  >
    <button
      v-if="contextMenu.node && contextMenu.node.type === 'file'"
      type="button"
      @click.stop="handleDownloadFile"
    >
      {{ $t('shell.downloadFile') }}
    </button>
    <button
      v-if="contextMenu.node && contextMenu.node.type === 'folder'"
      type="button"
      :disabled="!contextMenu.node.path"
      @click.stop="handleDownloadFolder"
    >
      {{ $t('shell.downloadArchive') }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useFileStore } from '@/stores/file';

const props = defineProps<{
  downloadFile: (path: string) => void;
  downloadFolder: (path: string) => void;
}>();

const fileStore = useFileStore();
const { contextMenu } = storeToRefs(fileStore);

const handleDownloadFile = () => {
  const node = contextMenu.value.node;
  if (!node || node.type !== 'file' || !node.path) {
    return;
  }
  props.downloadFile(node.path);
  fileStore.hideContextMenu();
};

const handleDownloadFolder = () => {
  const node = contextMenu.value.node;
  if (!node || node.type !== 'folder' || !node.path) {
    return;
  }
  props.downloadFolder(node.path);
  fileStore.hideContextMenu();
};

const handleClickOutside = (event: MouseEvent) => {
  if (!contextMenu.value.visible) {
    return;
  }
  const target = event.target as HTMLElement | null;
  if (target && target.closest('.context-menu')) {
    return;
  }
  fileStore.hideContextMenu();
};

const handleScroll = () => {
  if (contextMenu.value.visible) {
    fileStore.hideContextMenu();
  }
};

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && contextMenu.value.visible) {
    fileStore.hideContextMenu();
  }
};

onMounted(() => {
  document.addEventListener('click', handleClickOutside);
  window.addEventListener('scroll', handleScroll, true);
  document.addEventListener('keydown', handleKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside);
  window.removeEventListener('scroll', handleScroll, true);
  document.removeEventListener('keydown', handleKeydown);
});
</script>

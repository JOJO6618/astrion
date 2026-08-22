<template>
  <div class="file-node-wrapper" @contextmenu.stop.prevent="handleContextMenu">
    <div v-if="node.type === 'folder'" class="file-node folder-node">
      <button class="folder-header" type="button" :style="folderPadding" @click="toggle">
        <span class="folder-arrow">{{ isExpanded ? '▾' : '▸' }}</span>
        <span
          class="icon icon-sm folder-icon"
          :style="iconStyle(isExpanded ? 'folderOpen' : 'folder')"
          aria-hidden="true"
        ></span>
        <span class="folder-name">{{ node.name }}</span>
      </button>
      <div v-show="isExpanded" class="folder-children">
        <FileNode
          v-for="child in node.children"
          :key="child.path"
          :node="child"
          :level="level + 1"
          :expanded-folders="expandedFolders"
          :icon-style="iconStyle"
        />
      </div>
    </div>
    <div v-else class="file-node file-leaf" :style="filePadding">
      <span class="icon icon-sm file-icon" :style="iconStyle('file')" aria-hidden="true"></span>
      <span class="file-name">{{ node.name }}</span>
      <span v-if="node.annotation" class="annotation">{{ node.annotation }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useFileStore } from '@/stores/file';

defineOptions({ name: 'FileNode' });

const props = defineProps<{
  node: any;
  level: number;
  expandedFolders: Record<string, boolean>;
  iconStyle: (key: string, size?: string) => Record<string, string>;
}>();

const fileStore = useFileStore();

const isExpanded = computed(() => {
  if (props.node.type !== 'folder') {
    return false;
  }
  const value = props.expandedFolders[props.node.path];
  return value === undefined ? true : value;
});

const folderPadding = computed(() => ({
  paddingLeft: `${12 + props.level * 16}px`
}));

const filePadding = computed(() => ({
  paddingLeft: `${40 + props.level * 16}px`
}));

function toggle() {
  if (props.node.type === 'folder') {
    fileStore.toggleFolder(props.node.path);
  }
}

function handleContextMenu(event: MouseEvent) {
  fileStore.showContextMenu({ node: props.node, event });
}
</script>

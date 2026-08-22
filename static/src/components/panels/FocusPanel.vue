<template>
  <aside
    class="sidebar right-sidebar"
    :class="{ collapsed }"
    :style="{ width: collapsed ? '0px' : width + 'px' }"
  >
    <div class="sidebar-header">
      <h3 class="icon-label">
        <span class="icon icon-sm" :style="iconStyle('eye')" aria-hidden="true"></span>
        <span>聚焦文件 ({{ focusedCount }}/3)</span>
      </h3>
      <button
        v-if="showCloseButton"
        type="button"
        class="focus-close-btn"
        aria-label="关闭聚焦面板"
        @click="$emit('close')"
      >
        ×
      </button>
    </div>
    <div class="focused-files" v-if="!collapsed">
      <div v-if="!focusedCount" class="no-files">暂无聚焦文件</div>
      <div v-else class="file-tabs">
        <div v-for="(file, path) in focusedFileMap" :key="path" class="file-tab">
          <div class="tab-header">
            <span class="file-name">{{ path.split('/').pop() }}</span>
            <span class="file-size">{{ formatSize(file.size) }}</span>
          </div>
          <div class="file-content">
            <pre><code :class="languageClass(path)">{{ file.content }}</code></pre>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import { useFocusStore } from '@/stores/focus';

defineOptions({ name: 'FocusPanel' });

const props = defineProps<{
  collapsed: boolean;
  width: number;
  iconStyle: (key: string) => Record<string, string>;
  getLanguageClass: (path: string) => string;
  showCloseButton?: boolean;
}>();

defineEmits<{
  (event: 'close'): void;
}>();

const focusStore = useFocusStore();
const { focusedFiles } = storeToRefs(focusStore);
const focusedFileMap = computed(() => focusedFiles.value || {});
const focusedCount = computed(() => Object.keys(focusedFileMap.value).length);

const languageClass = (path: string) => props.getLanguageClass(path);
const formatSize = (size: number) => `${(size / 1024).toFixed(1)}KB`;
const showCloseButton = computed(() => props.showCloseButton === true);
</script>

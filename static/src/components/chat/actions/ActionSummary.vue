<template>
  <div class="action-summary">
    <div class="summary-header icon-label">
      <span
        class="icon icon-sm"
        :style="iconStyle(action.type === 'apply' ? 'pencil' : 'clipboard')"
        aria-hidden="true"
      ></span>
      <span>{{ action.title || (action.type === 'apply' ? '应用修改' : '追加内容') }}</span>
    </div>
    <div class="summary-content" v-if="action.content">
      <pre>{{ action.content }}</pre>
    </div>
    <div class="summary-actions">
      <button type="button" class="ghost" @click="$emit('copy', action, blockId)">复制</button>
      <button
        type="button"
        class="ghost"
        v-if="action.type === 'apply'"
        @click="$emit('apply', action, blockId)"
      >
        应用
      </button>
      <button
        type="button"
        class="ghost"
        v-if="action.type === 'append'"
        @click="$emit('run', action, blockId)"
      >
        执行
      </button>
      <button
        v-if="action.path"
        type="button"
        class="ghost"
        @click="$emit('download', action.path)"
      >
        下载文件
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ActionSummary' });

defineProps<{
  action: any;
  blockId: string;
  iconStyle: (key: string) => Record<string, string>;
}>();

defineEmits<{
  (event: 'copy', action: any, blockId: string): void;
  (event: 'apply', action: any, blockId: string): void;
  (event: 'run', action: any, blockId: string): void;
  (event: 'download', path: string): void;
}>();
</script>

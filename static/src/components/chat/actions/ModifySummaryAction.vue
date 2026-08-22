<template>
  <div class="modify-placeholder">
    <div class="modify-placeholder-content">
      <div class="icon-label">
        <span class="icon icon-sm" :style="iconStyle('hammer')" aria-hidden="true"></span>
        <span>{{ action.modify?.summary || `已处理 ${action.modify?.path || '目标文件'}` }}</span>
      </div>
      <div class="modify-meta" v-if="action.modify">
        <span v-if="action.modify.total">· 共 {{ action.modify.total }} 处</span>
        <span v-if="action.modify.completed"
          >· 完成 {{ action.modify.completed.length || action.modify.completed }} 处</span
        >
        <span v-if="action.modify.failed"
          >· 未完成 {{ action.modify.failed.length || action.modify.failed }} 处</span
        >
      </div>
      <div class="modify-warning icon-label" v-if="action.modify?.forced">
        <span class="icon icon-sm" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
        <span>未检测到结束标记，系统已自动处理。</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ModifySummaryAction' });
defineProps<{ action: any; iconStyle: (key: string) => Record<string, string> }>();
</script>

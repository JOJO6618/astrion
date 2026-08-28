<template>
  <div class="modify-placeholder">
    <div class="modify-placeholder-content">
      <div class="icon-label">
        <span class="icon icon-sm" :style="iconStyle('hammer')" aria-hidden="true"></span>
        <span>{{ action.modify?.summary || $t('chatActions.modifyProcessed', { path: action.modify?.path || $t('chat.targetFile') }) }}</span>
      </div>
      <div class="modify-meta" v-if="action.modify">
        <span v-if="action.modify.total">{{ $t('chatActions.modifyTotal', { n: action.modify.total }) }}</span>
        <span v-if="action.modify.completed"
          >{{ $t('chatActions.modifyDone', { n: action.modify.completed.length || action.modify.completed }) }}</span
        >
        <span v-if="action.modify.failed"
          >{{ $t('chatActions.modifyRemaining', { n: action.modify.failed.length || action.modify.failed }) }}</span
        >
      </div>
      <div class="modify-warning icon-label" v-if="action.modify?.forced">
        <span class="icon icon-sm" :style="iconStyle('triangleAlert')" aria-hidden="true"></span>
        <span>{{ $t('chatActions.modifyWarning') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'ModifySummaryAction' });
defineProps<{ action: any; iconStyle: (key: string) => Record<string, string> }>();
</script>

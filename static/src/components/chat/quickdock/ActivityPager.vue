<script setup lang="ts">
import { useSubAgentStore } from '@/stores/subAgent';

const activity = useSubAgentStore();
function turn(direction: 'older' | 'newer') {
  const taskId = activity.activeAgent?.task_id ?? activity.silentActivityTaskId;
  if (taskId) void activity.fetchSubAgentActivity(taskId, direction);
}
</script>

<template>
  <div v-if="activity.activityStart > 0 || activity.historyPage" class="activity-pager">
    <button type="button" :disabled="!activity.activityStart || activity.activityLoading" @click="turn('older')">{{ $t('quickdock.olderSteps') }}</button>
    <template v-if="activity.historyPage">
      <button type="button" :disabled="activity.activityLoading" @click="turn('newer')">{{ $t('quickdock.newerSteps') }}</button>
      <button type="button" :disabled="activity.activityLoading" @click="activity.historyPage = null">{{ $t('quickdock.latestSteps') }}</button>
    </template>
  </div>
</template>

<style scoped>
.activity-pager { display: flex; gap: 4px; flex-wrap: wrap; }
.activity-pager button { height: 30px; padding: 0 8px; border: 0; border-radius: 7px; background: transparent; color: var(--text-secondary); font: inherit; cursor: pointer; }
.activity-pager button:hover:not(:disabled) { color: var(--text-primary); background: var(--hover-bg); }
.activity-pager button:disabled { opacity: 0.4; cursor: default; }
</style>

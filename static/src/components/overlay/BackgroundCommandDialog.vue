<template>
  <transition name="overlay-fade">
    <div v-if="activeCommand" class="subagent-activity-overlay" @click.self="close">
      <div class="subagent-activity-modal bg-command-modal">
        <div class="subagent-activity-header">
          <div class="subagent-activity-title">后台指令 {{ activeCommand.command_id }}</div>
          <button type="button" class="subagent-activity-close" @click="close">×</button>
        </div>
        <div class="subagent-activity-meta">
          <span
            class="subagent-activity-status"
            :class="activeDetail?.status || activeCommand.status || ''"
          >
            {{ activeDetail?.status || activeCommand.status || 'running' }}
          </span>
          <span
            class="subagent-activity-summary"
            v-if="activeDetail?.command || activeCommand.command"
          >
            {{ activeDetail?.command || activeCommand.command }}
          </span>
        </div>
        <div class="subagent-activity-actions" v-if="canStop">
          <button
            type="button"
            class="subagent-stop-btn"
            :disabled="stopLoading"
            @click="handleStop"
          >
            {{ stopLoading ? '停止中...' : '手动停止' }}
          </button>
          <span v-if="stopError" class="subagent-activity-error">{{ stopError }}</span>
        </div>
        <div class="subagent-activity-body">
          <div v-if="detailError" class="subagent-activity-error">{{ detailError }}</div>
          <div v-else-if="detailLoading && !displayOutput" class="subagent-activity-empty">
            正在读取后台指令输出...
          </div>
          <pre v-else class="bg-command-output">{{ displayOutput || '[no_output]' }}</pre>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useBackgroundCommandStore } from '@/stores/backgroundCommand';

const commandStore = useBackgroundCommandStore();
const { activeCommand, activeDetail, detailLoading, detailError, stoppingCommandIds } =
  storeToRefs(commandStore);
const stopError = ref('');

const close = () => {
  commandStore.closeCommand();
};

const isTerminalStatus = (status?: string) => {
  const normalized = (status || '').toString().toLowerCase();
  return ['completed', 'failed', 'timeout', 'cancelled'].includes(normalized);
};

const canStop = computed(() => {
  const status = activeDetail.value?.status || activeCommand.value?.status;
  if (!activeCommand.value?.command_id) return false;
  return !isTerminalStatus(status);
});

const stopLoading = computed(() => {
  const commandId = activeCommand.value?.command_id;
  if (!commandId) return false;
  return !!stoppingCommandIds.value?.[commandId];
});

const handleStop = async () => {
  const commandId = activeCommand.value?.command_id;
  if (!commandId || stopLoading.value) return;
  stopError.value = '';
  const result = await commandStore.cancelCommand(commandId);
  if (!result?.success) {
    stopError.value = result?.error || '停止失败';
  }
};

const displayOutput = computed(() => {
  const text = (activeDetail.value?.output || '').toString();
  return text;
});
</script>

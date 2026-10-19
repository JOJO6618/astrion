<template>
  <section v-if="visible" class="qd-window goal-window">
    <header class="qd-window__header">
      <span class="qd-window__title">{{ $t('goal.title') }}</span>
      <button type="button" class="goal-window__details" @click="input.goalDialogOpen = true">
        {{ statusLabel }}
      </button>
    </header>
    <div class="goal-window__target">{{ progress?.goal }}</div>
    <div v-if="message" class="goal-window__message">{{ message }}</div>
    <div v-if="retryTime" class="goal-window__retry">
      {{ $t('goal.retryAt', { time: retryTime }) }}
    </div>
    <footer class="goal-window__actions">
      <button v-if="resumable" type="button" :disabled="taskBusy" @click="$emit('continue')">
        {{ $t('goal.resume') }}
      </button>
      <button v-if="progress?.status === 'retrying'" type="button" @click="$emit('pause')">
        {{ $t('goal.pause') }}
      </button>
      <button v-if="!terminal" type="button" @click="$emit('close-goal')">
        {{ $t('goal.closeGoal') }}
      </button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useInputStore } from '@/stores/input';
import { goalReason, goalStatusLabel } from '@/utils/goal';

defineProps<{ taskBusy?: boolean }>();
defineEmits<{ (event: 'continue'): void; (event: 'close-goal'): void; (event: 'pause'): void }>();
const input = useInputStore();
const progress = computed(() => input.goalProgress);
const visible = computed(() => !!progress.value?.goal && progress.value.status !== 'stopped');
const terminal = computed(() => ['done', 'stopped'].includes(progress.value?.status));
const resumable = computed(() => ['paused', 'blocked'].includes(progress.value?.status));
const statusLabel = computed(() => goalStatusLabel(progress.value?.status));
const message = computed(
  () => goalReason(progress.value?.stopped_reason) || progress.value?.last_message || ''
);
const retryTime = computed(() =>
  progress.value?.status === 'retrying' && progress.value?.next_retry_at
    ? new Date(progress.value.next_retry_at * 1000).toLocaleTimeString()
    : ''
);
</script>

<style scoped>
.goal-window__details {
  height: 28px;
  margin-left: auto;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.goal-window__details:hover {
  color: var(--text-primary);
}
.goal-window__target {
  height: 40px;
  margin: 4px 12px 8px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 12px;
  line-height: 20px;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}
.goal-window__message {
  height: 36px;
  margin: 0 12px 8px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 11px;
  line-height: 18px;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}
.goal-window__retry {
  height: 24px;
  padding: 0 12px;
  font-size: 11px;
  color: var(--text-secondary);
}
.goal-window__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  padding: 0 10px 10px;
}
.goal-window__actions:empty {
  display: none;
}
.goal-window__actions button {
  height: 28px;
  padding: 0 8px;
  font: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--surface-base);
  border: 1px solid var(--border-default);
  border-radius: 5px;
  cursor: pointer;
}
.goal-window__actions button:hover {
  color: var(--text-primary);
  background: var(--hover-bg);
}
.goal-window__actions button:disabled {
  opacity: 0.45;
  cursor: default;
}
.goal-window__actions button:focus-visible,
.goal-window__details:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>

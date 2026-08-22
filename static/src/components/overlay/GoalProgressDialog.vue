<template>
  <transition name="overlay-fade">
    <div v-if="open" class="subagent-activity-overlay" @click.self="$emit('close')">
      <div class="subagent-activity-modal goal-progress-modal">
        <div class="subagent-activity-header">
          <div class="subagent-activity-title">
            {{ isDone ? '目标已完成' : isStopped ? '目标模式停止' : '目标模式进行中' }}
          </div>
          <button type="button" class="subagent-activity-close" @click="$emit('close')">×</button>
        </div>

        <div class="goal-progress-meta">
          <span class="goal-progress-status" :class="statusClass">{{ statusLabel }}</span>
          <span v-if="goal" class="goal-progress-goal" :title="goal">目标：{{ goal }}</span>
        </div>

        <div class="goal-progress-metrics">
          <div class="goal-metric">
            <div class="goal-metric__value">{{ turnCount }}</div>
            <div class="goal-metric__label">已运行轮数</div>
          </div>
          <div class="goal-metric">
            <div class="goal-metric__value">{{ formattedTokens }}</div>
            <div class="goal-metric__label">消耗 token</div>
          </div>
          <div class="goal-metric">
            <div class="goal-metric__value">{{ toolCalls }}</div>
            <div class="goal-metric__label">工具调用</div>
          </div>
          <div class="goal-metric">
            <div class="goal-metric__value">{{ formattedDuration }}</div>
            <div class="goal-metric__label">用时</div>
          </div>
        </div>

        <div v-if="isStopped && stoppedReasonLabel" class="goal-progress-reason">
          停止原因：{{ stoppedReasonLabel }}
        </div>

        <div v-if="summary" class="goal-progress-summary">
          <div class="goal-progress-summary__title">{{ isDone ? '完成总结' : '最后进展' }}</div>
          <div class="goal-progress-summary__body">{{ summary }}</div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';

defineOptions({ name: 'GoalProgressDialog' });

const props = defineProps<{
  open: boolean;
  progress: Record<string, any> | null;
}>();

defineEmits<{ (event: 'close'): void }>();

const status = computed(() => (props.progress?.status || 'running') as string);
const isDone = computed(() => status.value === 'done');
const isStopped = computed(() => status.value === 'stopped');

const statusLabel = computed(() => {
  if (isDone.value) return '已完成';
  if (isStopped.value) return '已停止';
  return '运行中';
});
const statusClass = computed(() => ({
  'is-done': isDone.value,
  'is-stopped': isStopped.value,
  'is-running': !isDone.value && !isStopped.value
}));

const goal = computed(() => (props.progress?.goal || '').toString());
const turnCount = computed(() => Number(props.progress?.turn_count ?? 0));
const toolCalls = computed(() => Number(props.progress?.tool_calls ?? 0));
const summary = computed(() => (props.progress?.summary || props.progress?.final_summary || '').toString());
const durationBaseSeconds = ref(0);
const durationObservedAtMs = ref(Date.now());
const nowMs = ref(Date.now());
let durationTimer: ReturnType<typeof window.setInterval> | null = null;

const syncDurationBase = () => {
  const raw = Number(props.progress?.duration_seconds ?? 0);
  durationBaseSeconds.value = Number.isFinite(raw) && raw > 0 ? raw : 0;
  durationObservedAtMs.value = Date.now();
  nowMs.value = durationObservedAtMs.value;
};

const ensureDurationTimer = () => {
  if (durationTimer || !props.open || isDone.value || isStopped.value) return;
  durationTimer = window.setInterval(() => {
    nowMs.value = Date.now();
  }, 1000);
};

const clearDurationTimer = () => {
  if (durationTimer) {
    window.clearInterval(durationTimer);
    durationTimer = null;
  }
};

watch(
  () => [props.progress?.duration_seconds, props.progress?.status, props.open],
  () => {
    syncDurationBase();
    if (props.open && !isDone.value && !isStopped.value) {
      ensureDurationTimer();
    } else {
      clearDurationTimer();
    }
  },
  { immediate: true }
);

onBeforeUnmount(clearDurationTimer);

const formattedTokens = computed(() => {
  const n = Number(props.progress?.tokens_used ?? 0);
  if (!Number.isFinite(n)) return '0';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
});

const formattedDuration = computed(() => {
  const liveExtra = !isDone.value && !isStopped.value
    ? Math.max(0, (nowMs.value - durationObservedAtMs.value) / 1000)
    : 0;
  const s = durationBaseSeconds.value + liveExtra;
  if (!Number.isFinite(s) || s <= 0) return '0s';
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  if (m < 60) return `${m}m${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60}m`;
});

const stoppedReasonLabel = computed(() => {
  const map: Record<string, string> = {
    idle_no_tool: '主模型停止时未调用任何工具（可能卡住）',
    max_turns: '已达到最大轮数上限',
    max_tokens: '已达到累计 token 上限',
    user_cancel: '用户手动停止'
  };
  const reason = (props.progress?.stopped_reason || '').toString();
  return map[reason] || reason;
});
</script>

<style scoped>
.goal-progress-modal {
  min-width: 360px;
  max-width: 520px;
}
.goal-progress-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 4px 0 12px;
}
.goal-progress-status {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--theme-chip-border);
  background: color-mix(in srgb, var(--theme-surface-soft) 92%, var(--text-tertiary) 8%);
  color: var(--text-secondary);
}
.goal-progress-status.is-running {
  color: var(--accent);
  border-color: var(--accent);
  background: color-mix(in srgb, var(--theme-surface-soft) 82%, var(--accent) 18%);
}
.goal-progress-status.is-done {
  color: var(--state-success);
  border-color: var(--state-success);
  background: color-mix(in srgb, var(--theme-surface-soft) 82%, var(--state-success) 18%);
}
.goal-progress-status.is-stopped {
  color: var(--state-warning);
  border-color: var(--state-warning);
  background: color-mix(in srgb, var(--theme-surface-soft) 82%, var(--state-warning) 18%);
}
:global(:root[data-theme='dark']) .goal-progress-status,
:global(body[data-theme='dark']) .goal-progress-status {
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--theme-surface-muted) 82%, var(--text-tertiary) 18%);
  border-color: var(--theme-control-border-strong);
}
:global(:root[data-theme='dark']) .goal-progress-status.is-running,
:global(body[data-theme='dark']) .goal-progress-status.is-running {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--theme-surface-muted) 72%, var(--accent) 28%);
  border-color: color-mix(in srgb, var(--accent) 70%, white 30%);
}
:global(:root[data-theme='dark']) .goal-progress-status.is-done,
:global(body[data-theme='dark']) .goal-progress-status.is-done {
  color: color-mix(in srgb, var(--state-success) 30%, white);
  background: color-mix(in srgb, var(--theme-surface-muted) 72%, var(--state-success) 28%);
  border-color: color-mix(in srgb, var(--state-success) 70%, white 30%);
}
:global(:root[data-theme='dark']) .goal-progress-status.is-stopped,
:global(body[data-theme='dark']) .goal-progress-status.is-stopped {
  color: color-mix(in srgb, var(--state-warning) 30%, white);
  background: color-mix(in srgb, var(--theme-surface-muted) 72%, var(--state-warning) 28%);
  border-color: color-mix(in srgb, var(--state-warning) 70%, white 30%);
}
.goal-progress-goal {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.goal-progress-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}
.goal-metric {
  text-align: center;
  padding: 10px 6px;
  border-radius: 10px;
  background: var(--theme-surface-muted);
  border: 1px solid var(--theme-control-border);
}
.goal-metric__value {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}
.goal-metric__label {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}
.goal-progress-reason {
  font-size: 13px;
  color: var(--state-warning);
  margin-bottom: 10px;
}
.goal-progress-summary {
  border-top: 1px solid var(--theme-control-border);
  padding-top: 10px;
}
.goal-progress-summary__title {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 6px;
}
.goal-progress-summary__body {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}
@media (max-width: 520px) {
  .goal-progress-metrics {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>

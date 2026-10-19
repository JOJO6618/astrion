<template>
  <Transition name="overlay-fade">
    <div
      v-if="open"
      class="goal-overlay"
      @click.self="$emit('close')"
      @keydown.esc="$emit('close')"
    >
      <section class="goal-dialog" role="dialog" aria-modal="true" :aria-label="$t('goal.title')">
        <header class="goal-dialog__header">
          <span>{{ $t('goal.title') }}</span>
          <CloseButton :label="$t('common.close')" @click="$emit('close')" />
        </header>
        <form class="goal-dialog__body" @submit.prevent="submit">
          <template v-if="isNew">
            <label class="goal-label" for="goal-target">{{ $t('goal.target') }}</label>
            <textarea
              id="goal-target"
              ref="targetInput"
              v-model="target"
              class="goal-target"
              :placeholder="$t('goal.placeholder')"
              maxlength="100000"
            />
          </template>
          <template v-else>
            <div class="goal-dialog__state">{{ stateLabel }}</div>
            <div class="goal-dialog__target">{{ progress?.goal }}</div>
            <div class="goal-dialog__metrics">
              <span
                >{{ $t('goal.turns') }} <strong>{{ progress?.turn_count || 0 }}</strong></span
              >
              <span
                >{{ $t('goal.tokens') }}
                <strong>{{ Number(progress?.tokens_used || 0).toLocaleString() }}</strong></span
              >
            </div>
            <p v-if="reason" class="goal-dialog__reason">{{ reason }}</p>
            <p
              v-if="progress?.last_message || progress?.final_summary"
              class="goal-dialog__message"
            >
              {{ progress?.final_summary || progress?.last_message }}
            </p>
            <p v-if="retryTime" class="goal-dialog__reason">
              {{ $t('goal.retryAt', { time: retryTime }) }}
            </p>
          </template>
          <details v-if="isNew || resumable" class="goal-limits">
            <summary>{{ $t('goal.advanced') }}</summary>
            <div class="goal-limit">
              <button
                type="button"
                class="goal-limit__toggle"
                :aria-pressed="turnsEnabled"
                @click="turnsEnabled = !turnsEnabled"
              >
                <FancyCheck :checked="turnsEnabled" /><span>{{ $t('goal.maxTurns') }}</span>
              </button>
              <input
                v-model.number="maxTurns"
                type="number"
                min="1"
                max="10000"
                :disabled="!turnsEnabled"
                :aria-label="$t('goal.maxTurns')"
              />
            </div>
            <div class="goal-limit">
              <button
                type="button"
                class="goal-limit__toggle"
                :aria-pressed="tokensEnabled"
                @click="tokensEnabled = !tokensEnabled"
              >
                <FancyCheck :checked="tokensEnabled" /><span>{{ $t('goal.maxTokens') }}</span>
              </button>
              <input
                v-model.number="maxTokens"
                type="number"
                min="1"
                max="1000000000"
                :disabled="!tokensEnabled"
                :aria-label="$t('goal.maxTokens')"
              />
            </div>
          </details>
          <p v-if="validation" class="goal-dialog__error">{{ validation }}</p>
          <footer class="goal-dialog__footer">
            <button
              v-if="!isNew && !terminal"
              type="button"
              class="goal-button"
              @click="$emit('close-goal')"
            >
              {{ $t('goal.closeGoal') }}
            </button>
            <button
              v-if="terminal && !isNew"
              type="button"
              class="goal-button goal-button--primary"
              :disabled="taskBusy"
              @click="creating = true"
            >
              {{ $t('goal.newGoal') }}
            </button>
            <button
              v-if="isNew || resumable"
              type="submit"
              class="goal-button goal-button--primary"
              :disabled="taskBusy || (isNew && !target.trim())"
            >
              {{ isNew ? $t('goal.start') : $t('goal.resume') }}
            </button>
            <button
              v-if="progress?.status === 'retrying'"
              type="button"
              class="goal-button"
              @click="$emit('pause')"
            >
              {{ $t('goal.pause') }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { t } from '@/locales';
import CloseButton from '@/components/common/CloseButton.vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { goalReason, goalStatusLabel } from '@/utils/goal';

const props = defineProps<{
  open: boolean;
  progress: Record<string, any> | null;
  taskBusy?: boolean;
}>();
const emit = defineEmits<{
  (event: 'close'): void;
  (
    event: 'start',
    value: { goal: string; max_turns: number | null; max_tokens: number | null }
  ): void;
  (event: 'continue', value: { max_turns: number | null; max_tokens: number | null }): void;
  (event: 'close-goal'): void;
  (event: 'pause'): void;
}>();
const target = ref('');
const targetInput = ref<HTMLTextAreaElement | null>(null);
const maxTurns = ref(5);
const maxTokens = ref(100000);
const turnsEnabled = ref(false);
const tokensEnabled = ref(false);
const creating = ref(false);
const validation = ref('');
const terminal = computed(() => ['done', 'stopped'].includes(props.progress?.status));
const isNew = computed(() => creating.value || !props.progress?.goal);
const resumable = computed(() => ['paused', 'blocked'].includes(props.progress?.status));
const stateLabel = computed(() => goalStatusLabel(props.progress?.status));
const reason = computed(() => goalReason(props.progress?.stopped_reason));
const retryTime = computed(() =>
  props.progress?.status === 'retrying' && props.progress?.next_retry_at
    ? new Date(props.progress.next_retry_at * 1000).toLocaleTimeString()
    : ''
);
watch(
  () => [props.open, props.progress?.run_id],
  async () => {
    validation.value = '';
    creating.value = false;
    turnsEnabled.value = props.progress?.max_turns != null;
    tokensEnabled.value = props.progress?.max_tokens != null;
    maxTurns.value = props.progress?.max_turns || 5;
    maxTokens.value = props.progress?.max_tokens || 100000;
    if (props.open && isNew.value) {
      await nextTick();
      targetInput.value?.focus();
    }
  }
);
// The task-owned parent rejects duplicate submissions; failed sends stay editable.
function submit() {
  if (props.taskBusy) return;
  const turns = turnsEnabled.value ? Number(maxTurns.value) : null;
  const tokens = tokensEnabled.value ? Number(maxTokens.value) : null;
  if (
    (turns != null && (!Number.isSafeInteger(turns) || turns < 1 || turns > 10000)) ||
    (tokens != null && (!Number.isSafeInteger(tokens) || tokens < 1 || tokens > 1000000000))
  ) {
    validation.value = t('goal.invalidLimit');
    return;
  }
  validation.value = '';
  const limits = { max_turns: turns, max_tokens: tokens };
  if (isNew.value) emit('start', { goal: target.value.trim(), ...limits });
  else emit('continue', limits);
}
</script>

<style scoped>
.goal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--overlay-scrim);
}
.goal-dialog {
  width: 520px;
  max-width: 100%;
  max-height: min(760px, 90dvh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface-base);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
  border-radius: 14px;
  box-shadow: var(--shadow-mid);
}
.goal-dialog__header {
  height: 58px;
  min-height: 58px;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  border-bottom: 1px solid var(--border-default);
}
.goal-dialog__body {
  padding: 20px;
  overflow: auto;
  scrollbar-width: thin;
}
.goal-label {
  display: block;
  height: 30px;
  font-size: 13px;
  color: var(--text-secondary);
}
.goal-target {
  width: 100%;
  height: 170px;
  box-sizing: border-box;
  resize: vertical;
  min-height: 100px;
  max-height: 300px;
  padding: 12px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  line-height: 1.6;
}
.goal-target:focus-visible,
.goal-button:focus-visible,
.goal-limit__toggle:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.goal-dialog__state {
  height: 28px;
  font-size: 13px;
  color: var(--text-secondary);
}
.goal-dialog__target {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 160px;
  overflow: auto;
  line-height: 1.6;
}
.goal-dialog__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  padding: 16px 0;
  font-size: 12px;
  color: var(--text-secondary);
}
.goal-dialog__metrics strong {
  margin-left: 6px;
  color: var(--text-primary);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.goal-dialog__reason {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.goal-dialog__message {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 180px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
}
.goal-limits {
  margin-top: 18px;
  border-top: 1px solid var(--border-default);
}
.goal-limits summary {
  height: 42px;
  display: flex;
  align-items: center;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  list-style: none;
}
.goal-limits summary::-webkit-details-marker {
  display: none;
}
.goal-limit {
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.goal-limit__toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.goal-limit input {
  width: 112px;
  height: 32px;
  padding: 0 8px;
  box-sizing: border-box;
  border: 1px solid var(--border-default);
  border-radius: 5px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
}
.goal-limit input:disabled {
  opacity: 0.4;
}
.goal-dialog__footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}
.goal-button {
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--border-default);
  border-radius: 7px;
  background: var(--surface-base);
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.goal-button:hover {
  background: var(--hover-bg);
}
.goal-button--primary {
  background: var(--accent);
  color: var(--on-accent);
  border-color: transparent;
}
.goal-button--primary:hover {
  background: var(--accent-hover);
}
.goal-button:disabled {
  opacity: 0.45;
  cursor: default;
}
.goal-dialog__error {
  color: var(--state-danger);
  font-size: 12px;
}
</style>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import CloseButton from '@/components/common/CloseButton.vue';

/**
 * 对话级深压缩阈值编辑弹窗（屏幕居中）。
 * 只负责收集数值；保存、校验失败与运行中阻断由使用方传入。
 */
const props = defineProps<{
  open: boolean;
  value: number;
  saving?: boolean;
  error?: string;
}>();
const emit = defineEmits<{ close: []; save: [value: number] }>();

const draft = ref('');
const inputRef = ref<HTMLInputElement | null>(null);

const min = 1000;
const max = 2000000;

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    draft.value = props.value > 0 ? String(Math.round(props.value)) : '';
    await nextTick();
    inputRef.value?.focus();
    inputRef.value?.select();
  }
);

const parsed = computed(() => {
  const raw = Number(draft.value);
  return Number.isFinite(raw) ? Math.round(raw) : 0;
});
const invalid = computed(() => parsed.value < min || parsed.value > max);

const submit = () => {
  if (props.saving || invalid.value) return;
  emit('save', parsed.value);
};
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="compress-limit-overlay" @click.self="emit('close')">
      <div class="compress-limit-dialog" role="dialog" :aria-label="$t('input.compressLimitTitle')">
        <div class="compress-limit-header">
          <span class="compress-limit-title">{{ $t('input.compressLimitTitle') }}</span>
          <CloseButton :disabled="saving" @click="emit('close')" />
        </div>
        <div class="compress-limit-body">
          <p class="compress-limit-desc">{{ $t('input.compressLimitDesc') }}</p>
          <label class="compress-limit-field">
            <input
              ref="inputRef"
              v-model="draft"
              type="number"
              inputmode="numeric"
              :min="min"
              :max="max"
              :step="1000"
              :disabled="saving"
              @keydown.enter.prevent="submit"
              @keydown.esc.prevent="emit('close')"
            />
            <span class="compress-limit-unit">tokens</span>
          </label>
          <div class="compress-limit-hint">{{ $t('input.compressLimitRange') }}</div>
          <div v-if="invalid && draft" class="compress-limit-error">
            {{ $t('input.compressLimitInvalid') }}
          </div>
          <div v-else-if="error" class="compress-limit-error">{{ error }}</div>
        </div>
        <div class="compress-limit-actions">
          <button
            type="button"
            class="compress-limit-btn"
            :disabled="saving"
            @click="emit('close')"
          >
            {{ $t('input.compressLimitCancel') }}
          </button>
          <button
            type="button"
            class="compress-limit-btn compress-limit-btn--primary"
            :disabled="saving || invalid || !draft"
            @click="submit"
          >
            {{ $t('input.compressLimitSave') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.compress-limit-overlay {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--overlay-scrim);
}

.compress-limit-dialog {
  width: 100%;
  max-width: 380px;
  max-height: min(70vh, 420px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  box-shadow: var(--shadow-card);
}

.compress-limit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-default);
}

.compress-limit-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.compress-limit-body {
  padding: 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.compress-limit-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.compress-limit-field {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  background: var(--surface-soft);
}

.compress-limit-field input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.compress-limit-field input:focus-visible {
  outline: none;
}

.compress-limit-unit {
  font-size: 12px;
  color: var(--text-tertiary);
}

.compress-limit-hint,
.compress-limit-error {
  font-size: 11px;
  line-height: 1.5;
}

.compress-limit-hint {
  color: var(--text-tertiary);
}

.compress-limit-error {
  color: var(--state-danger);
}

.compress-limit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--border-default);
}

.compress-limit-btn {
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--border-default);
  border-radius: 7px;
  background: var(--surface-soft);
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.compress-limit-btn:hover:not(:disabled) {
  background: var(--hover-bg);
}

.compress-limit-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.compress-limit-btn--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.compress-limit-btn--primary:hover:not(:disabled) {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}
</style>

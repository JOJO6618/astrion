<script setup lang="ts">
import { ref } from 'vue';
import { t } from '@/locales';
import { useProvidersStore } from '@/stores/providers';
import type { CustomModelEntry } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import SettingsModal from './SettingsModal.vue';
import PillSwitch from './PillSwitch.vue';

/**
 * 自定义模型添加/编辑对话框（等价于手写 custom_models.json 的一个条目）。
 * 字段对齐 /api/custom-models 白名单契约；编辑时未在表单暴露的高级字段
 * （thinkmode_status / extra_parameter 等）从原条目透传，避免 PUT 整体替换时丢配置。
 */
const props = withDefaults(
  defineProps<{
    mode: 'add' | 'edit';
    initial?: CustomModelEntry | null;
  }>(),
  { initial: null }
);

const emit = defineEmits<{ (e: 'close'): void }>();

const providersStore = useProvidersStore();
const uiStore = useUiStore();

const init = props.initial;
const modelName = ref(init?.model_name || '');
const displayName = ref(init?.display_name || '');
const description = ref(init?.description || '');
const url = ref(init?.url || '');
const apikey = ref(init?.apikey || '');
const modelId = ref(init?.model_id || '');
const multimodal = ref(init?.multimodal || 'none');
const reasoningCapability = ref(init?.reasoning_capability || 'fast');
const reasoningEffort = ref(!!init?.reasoning_effort);
const visible = ref(init?.visible !== false);
const contextWindow = ref(init?.context_window ? String(init.context_window) : '');
const maxOutputTokens = ref(init?.max_output_tokens ? String(init.max_output_tokens) : '');
const submitting = ref(false);
const errorText = ref('');

const MULTIMODAL_OPTIONS = ['none', 'image', 'video', 'image,video'] as const;
const REASONING_OPTIONS = ['fast', 'thinking', 'fast,thinking'] as const;

const multimodalLabel = (value: string): string => {
  switch (value) {
    case 'image':
      return t('settings.capImage');
    case 'video':
      return t('settings.capVideo');
    case 'image,video':
      return t('settings.customModelCapImageVideo');
    default:
      return t('settings.customModelCapNone');
  }
};

const reasoningLabel = (value: string): string => {
  switch (value) {
    case 'thinking':
      return t('settings.customModelReasoningThinking');
    case 'fast,thinking':
      return t('settings.customModelReasoningBoth');
    default:
      return t('settings.customModelReasoningFast');
  }
};

const parseIntOrUndef = (raw: string): number | undefined => {
  const parsed = Number(raw);
  if (!raw.trim() || !Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }
  return Math.round(parsed);
};

const submit = async () => {
  if (submitting.value) return;
  submitting.value = true;
  errorText.value = '';
  // 未暴露的高级字段（thinkmode_status / extra_parameter / 其余白名单外自定义键）整体透传
  const payload: CustomModelEntry = {
    ...(props.mode === 'edit' && init ? { ...init } : {}),
    model_name: modelName.value.trim(),
    display_name: displayName.value.trim(),
    description: description.value.trim(),
    url: url.value.trim(),
    apikey: apikey.value.trim(),
    model_id: modelId.value.trim(),
    multimodal: multimodal.value,
    reasoning_capability: reasoningCapability.value,
    reasoning_effort: reasoningEffort.value,
    visible: visible.value
  };
  const ctx = parseIntOrUndef(contextWindow.value);
  if (ctx !== undefined) {
    payload.context_window = ctx;
  } else {
    delete payload.context_window;
  }
  const maxOut = parseIntOrUndef(maxOutputTokens.value);
  if (maxOut !== undefined) {
    payload.max_output_tokens = maxOut;
  } else {
    delete payload.max_output_tokens;
  }

  const result =
    props.mode === 'edit' && init?.model_name
      ? await providersStore.updateCustomModel(init.model_name, payload)
      : await providersStore.createCustomModel(payload);
  submitting.value = false;
  if (!result.ok) {
    errorText.value = result.error || t('common.unknownError');
    return;
  }
  uiStore.pushToast({ message: t('settings.customModelSaved'), type: 'success' });
  emit('close');
};
</script>

<template>
  <SettingsModal
    :title="
      mode === 'edit' ? $t('settings.customModelEditTitle') : $t('settings.customModelAddTitle')
    "
    @close="emit('close')"
  >
    <p class="provider-form-note">{{ $t('settings.customModelNote') }}</p>

    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldModelName') }}</div>
      <input
        v-model="modelName"
        type="text"
        class="provider-form-input mono"
        placeholder="my-model"
        :disabled="submitting"
        spellcheck="false"
        autocomplete="off"
      />
      <div class="provider-form-hint">{{ $t('settings.fieldModelNameHint') }}</div>
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldDisplayName') }}</div>
      <input
        v-model="displayName"
        type="text"
        class="provider-form-input"
        :placeholder="$t('settings.fieldModelDisplayNamePlaceholder')"
        :disabled="submitting"
        autocomplete="off"
      />
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldModelDesc') }}</div>
      <input
        v-model="description"
        type="text"
        class="provider-form-input"
        :placeholder="$t('settings.fieldModelDescPlaceholder')"
        :disabled="submitting"
        autocomplete="off"
      />
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldApiUrl') }}</div>
      <input
        v-model="url"
        type="text"
        class="provider-form-input mono"
        placeholder="https://api.example.com"
        :disabled="submitting"
        spellcheck="false"
        autocomplete="off"
      />
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.connectApiKeyLabel') }}</div>
      <input
        v-model="apikey"
        type="password"
        class="provider-form-input mono"
        :placeholder="$t('settings.fieldApiKeyEnvPlaceholder', { example: '${ENV_VAR}' })"
        :disabled="submitting"
        spellcheck="false"
        autocomplete="off"
      />
      <div class="provider-form-hint">
        {{ $t('settings.fieldApiKeyEnvHint', { example: '${ENV_VAR}' }) }}
      </div>
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldModelId') }}</div>
      <input
        v-model="modelId"
        type="text"
        class="provider-form-input mono"
        placeholder="qwen3-coder-480b"
        :disabled="submitting"
        spellcheck="false"
        autocomplete="off"
      />
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldMultimodal') }}</div>
      <div class="custom-model-chips">
        <button
          v-for="option in MULTIMODAL_OPTIONS"
          :key="option"
          type="button"
          class="custom-model-chip"
          :class="{ active: multimodal === option }"
          :disabled="submitting"
          @click="multimodal = option"
        >
          {{ multimodalLabel(option) }}
        </button>
      </div>
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldReasoning') }}</div>
      <div class="custom-model-chips">
        <button
          v-for="option in REASONING_OPTIONS"
          :key="option"
          type="button"
          class="custom-model-chip"
          :class="{ active: reasoningCapability === option }"
          :disabled="submitting"
          @click="reasoningCapability = option"
        >
          {{ reasoningLabel(option) }}
        </button>
      </div>
    </div>
    <div class="provider-form-field">
      <div class="custom-model-switch-row">
        <span class="provider-form-switch-line">
          <PillSwitch
            :on="reasoningEffort"
            :label="$t('settings.fieldReasoningEffort')"
            :disabled="submitting"
            @toggle="reasoningEffort = !reasoningEffort"
          />
          {{ $t('settings.fieldReasoningEffort') }}
        </span>
        <span class="provider-form-switch-line">
          <PillSwitch
            :on="visible"
            :label="$t('settings.fieldModelEnabled')"
            :disabled="submitting"
            @toggle="visible = !visible"
          />
          {{ $t('settings.fieldModelEnabled') }}
        </span>
      </div>
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldContextWindow') }}</div>
      <input
        v-model="contextWindow"
        type="text"
        class="provider-form-input mono"
        placeholder="262144"
        :disabled="submitting"
        spellcheck="false"
        autocomplete="off"
        inputmode="numeric"
      />
    </div>
    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.fieldMaxOutput') }}</div>
      <input
        v-model="maxOutputTokens"
        type="text"
        class="provider-form-input mono"
        placeholder="65536"
        :disabled="submitting"
        spellcheck="false"
        autocomplete="off"
        inputmode="numeric"
      />
    </div>

    <div v-if="errorText" class="provider-form-error">{{ errorText }}</div>

    <div class="provider-form-actions">
      <button
        type="button"
        class="provider-form-btn primary"
        :disabled="
          submitting || !modelName.trim() || !url.trim() || !apikey.trim() || !modelId.trim()
        "
        @click="submit"
      >
        {{ submitting ? $t('common.saving') : $t('common.save') }}
      </button>
    </div>
  </SettingsModal>
</template>

<style scoped>
.custom-model-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.custom-model-chip {
  border: 1px solid var(--border-default);
  border-radius: 999px;
  background: transparent;
  color: var(--text-primary);
  height: 28px;
  padding: 0 12px;
  font-size: 12.5px;
  cursor: pointer;
  white-space: nowrap;
}

.custom-model-chip:hover:not(:disabled) {
  background: var(--hover-bg);
}

.custom-model-chip.active {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.custom-model-chip:disabled {
  opacity: 0.5;
  cursor: default;
}

.custom-model-switch-row {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
</style>

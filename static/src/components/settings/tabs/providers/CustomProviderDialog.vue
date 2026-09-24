<script setup lang="ts">
import { ref } from 'vue';
import { t } from '@/locales';
import { useProvidersStore } from '@/stores/providers';
import SettingsModal from './SettingsModal.vue';

/**
 * 自定义提供商连接对话框（OpenAI 兼容）。
 * 字段对齐 POST /api/providers/connect 自定义形态：
 * { provider_id, name, base_url, api_key?, headers? }。
 */
const emit = defineEmits<{ (e: 'close'): void }>();

const providersStore = useProvidersStore();

const providerId = ref('');
const displayName = ref('');
const baseUrl = ref('');
const apiKey = ref('');
const headers = ref<Array<{ key: string; value: string }>>([]);
const submitting = ref(false);
const errorText = ref('');
const successCount = ref<number | null>(null);
const modelsError = ref('');

const addHeader = () => {
  headers.value = [...headers.value, { key: '', value: '' }];
};

const removeHeader = (index: number) => {
  headers.value = headers.value.filter((_, i) => i !== index);
};

const submit = async () => {
  if (submitting.value) return;
  submitting.value = true;
  errorText.value = '';
  successCount.value = null;
  modelsError.value = '';
  const headerMap: Record<string, string> = {};
  for (const pair of headers.value) {
    if (pair.key.trim()) {
      headerMap[pair.key.trim()] = pair.value;
    }
  }
  const result = await providersStore.connectCustom({
    provider_id: providerId.value.trim(),
    name: displayName.value.trim() || providerId.value.trim(),
    base_url: baseUrl.value.trim(),
    api_key: apiKey.value.trim(),
    headers: headerMap
  });
  submitting.value = false;
  if (!result.ok) {
    errorText.value = result.error || t('common.unknownError');
    return;
  }
  successCount.value = result.modelsCount ?? 0;
  modelsError.value = result.modelsError || '';
};
</script>

<template>
  <SettingsModal :title="$t('settings.customProviderTitle')" @close="emit('close')">
    <p class="provider-form-note">{{ $t('settings.customProviderNote') }}</p>

    <template v-if="successCount === null">
      <div class="provider-form-field">
        <div class="provider-form-label">{{ $t('settings.fieldProviderId') }}</div>
        <input
          v-model="providerId"
          type="text"
          class="provider-form-input mono"
          placeholder="myprovider"
          :disabled="submitting"
          spellcheck="false"
          autocomplete="off"
        />
        <div class="provider-form-hint">{{ $t('settings.fieldProviderIdHint') }}</div>
      </div>
      <div class="provider-form-field">
        <div class="provider-form-label">{{ $t('settings.fieldDisplayName') }}</div>
        <input
          v-model="displayName"
          type="text"
          class="provider-form-input"
          :placeholder="$t('settings.fieldDisplayNamePlaceholder')"
          :disabled="submitting"
          autocomplete="off"
        />
      </div>
      <div class="provider-form-field">
        <div class="provider-form-label">{{ $t('settings.fieldBaseUrl') }}</div>
        <input
          v-model="baseUrl"
          type="text"
          class="provider-form-input mono"
          placeholder="https://api.example.com/v1"
          :disabled="submitting"
          spellcheck="false"
          autocomplete="off"
        />
      </div>
      <div class="provider-form-field">
        <div class="provider-form-label">{{ $t('settings.connectApiKeyLabel') }}</div>
        <input
          v-model="apiKey"
          type="password"
          class="provider-form-input mono"
          :placeholder="$t('settings.connectApiKeyPlaceholder')"
          :disabled="submitting"
          spellcheck="false"
          autocomplete="off"
        />
        <div class="provider-form-hint">{{ $t('settings.customProviderApiKeyHint') }}</div>
      </div>
      <div class="provider-form-field">
        <div class="provider-form-label">{{ $t('settings.fieldHeaders') }}</div>
        <div v-for="(pair, index) in headers" :key="index" class="provider-form-pair-row">
          <input
            v-model="pair.key"
            type="text"
            class="provider-form-input mono"
            placeholder="Header-Name"
            :disabled="submitting"
            spellcheck="false"
            autocomplete="off"
          />
          <input
            v-model="pair.value"
            type="text"
            class="provider-form-input"
            placeholder="value"
            :disabled="submitting"
            autocomplete="off"
          />
          <button
            type="button"
            class="provider-form-icon-btn"
            :aria-label="$t('common.delete')"
            :title="$t('common.delete')"
            :disabled="submitting"
            @click="removeHeader(index)"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path
                d="M3 4.5h10M6.5 4V3h3v1.5M4.5 4.5l.5 8.5h6l.5-8.5"
                stroke="currentColor"
                stroke-width="1.2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
        <button
          type="button"
          class="provider-form-add-row"
          :disabled="submitting"
          @click="addHeader"
        >
          {{ $t('settings.addHeaderRow') }}
        </button>
      </div>

      <div v-if="errorText" class="provider-form-error">{{ errorText }}</div>

      <div class="provider-form-actions">
        <button
          type="button"
          class="provider-form-btn primary"
          :disabled="submitting || !providerId.trim() || !baseUrl.trim()"
          @click="submit"
        >
          {{ submitting ? $t('settings.connectTesting') : $t('settings.connectSubmit') }}
        </button>
      </div>
    </template>

    <template v-else>
      <div class="provider-form-success">
        {{ $t('settings.connectSuccess', { count: successCount }) }}
      </div>
      <div v-if="modelsError" class="provider-form-warning">{{ modelsError }}</div>
      <div class="provider-form-actions">
        <button type="button" class="provider-form-btn primary" @click="emit('close')">
          {{ $t('common.ok') }}
        </button>
      </div>
    </template>
  </SettingsModal>
</template>

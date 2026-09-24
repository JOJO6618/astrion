<script setup lang="ts">
import { computed, ref } from 'vue';
import { t } from '@/locales';
import { useProvidersStore } from '@/stores/providers';
import type { ProviderCatalogEntry } from '@/stores/providers';
import SettingsModal from './SettingsModal.vue';
import ProviderIcon from './ProviderIcon.vue';

/**
 * API Key 连接对话框（auth=api_key 目录条目）。
 * 失败原样展示后端 error（不 i18n）；成功展示拉取到的模型数；
 * 连接成功但 /models 拉取失败时展示 models_error 告警（凭证已保存）。
 */
const props = defineProps<{
  entry: ProviderCatalogEntry;
}>();

const emit = defineEmits<{ (e: 'close'): void }>();

const providersStore = useProvidersStore();

const apiKey = ref('');
const submitting = ref(false);
const errorText = ref('');
const successCount = ref<number | null>(null);
const modelsError = ref('');

const keyHost = computed(() => {
  try {
    return props.entry.key_url ? new URL(props.entry.key_url).hostname : '';
  } catch {
    return props.entry.key_url || '';
  }
});

const submit = async () => {
  if (submitting.value) return;
  submitting.value = true;
  errorText.value = '';
  successCount.value = null;
  modelsError.value = '';
  const result = await providersStore.connectCatalog(props.entry, apiKey.value);
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
  <SettingsModal
    :title="$t('settings.connectDialogTitle', { name: entry.name })"
    @close="emit('close')"
  >
    <template #icon>
      <ProviderIcon
        :name="entry.name"
        :icon-url="entry.icon_url"
        :mono="!!entry.icon_mono"
        size="sm"
      />
    </template>

    <p class="provider-form-note">
      {{ $t('settings.connectApiDesc', { url: entry.base_url }) }}
    </p>
    <p v-if="entry.key_url" class="provider-form-note">
      {{ $t('settings.connectKeyHint') }}
      <a :href="entry.key_url" target="_blank" rel="noopener noreferrer">{{ keyHost }}</a>
    </p>

    <div class="provider-form-field">
      <div class="provider-form-label">{{ $t('settings.connectApiKeyLabel') }}</div>
      <input
        v-model="apiKey"
        type="password"
        class="provider-form-input mono"
        :placeholder="$t('settings.connectApiKeyPlaceholder')"
        :disabled="submitting || successCount !== null"
        spellcheck="false"
        autocomplete="off"
      />
      <div class="provider-form-hint">{{ $t('settings.connectApiKeyHint') }}</div>
    </div>

    <div v-if="errorText" class="provider-form-error">{{ errorText }}</div>
    <div v-if="successCount !== null" class="provider-form-success">
      {{ $t('settings.connectSuccess', { count: successCount }) }}
    </div>
    <div v-if="modelsError" class="provider-form-warning">{{ modelsError }}</div>

    <div class="provider-form-actions">
      <button
        v-if="successCount === null"
        type="button"
        class="provider-form-btn primary"
        :disabled="submitting || !apiKey.trim()"
        @click="submit"
      >
        {{ submitting ? $t('settings.connectTesting') : $t('settings.connectSubmit') }}
      </button>
      <button v-else type="button" class="provider-form-btn primary" @click="emit('close')">
        {{ $t('common.ok') }}
      </button>
    </div>
  </SettingsModal>
</template>

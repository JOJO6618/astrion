<template>
  <section class="settings-page">
    <template v-if="settings">
      <div class="settings-select-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ $t('chatMode.defaultModel') }}</span>
        </span>
        <ModelSelectDropdown
          :model-value="settings.chat_default_model"
          :options="models"
          @select="settings.chat_default_model = $event"
        />
      </div>
      <div class="settings-select-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ $t('chatMode.effort') }}</span>
        </span>
        <SettingsChoiceDropdown
          :model-value="settings.chat_default_reasoning_effort"
          dropdown-id="chat-reasoning-effort"
          :options="efforts"
          :disabled="busy"
          @select="settings.chat_default_reasoning_effort = $event"
        />
      </div>
      <label class="settings-toggle-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ $t('chatMode.defaultNetwork') }}</span>
        </span>
        <input v-model="settings.chat_network_enabled" type="checkbox" :disabled="busy" />
        <FancyCheck :checked="settings.chat_network_enabled" />
      </label>
      <label class="settings-input-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ $t('personalization.deepTriggerTokens') }}</span>
          <span class="settings-row-desc">{{ $t('chatMode.deepCompressionContextDesc') }}</span>
        </span>
        <input
          v-model.number="settings.chat_deep_compress_trigger_tokens"
          type="number"
          min="1000"
          max="2000000"
          step="1000"
          :aria-label="$t('personalization.deepTriggerTokens')"
          :disabled="busy"
        />
      </label>
      <label class="settings-textarea-row">
        <span class="settings-row-copy">
          <span class="settings-row-title">{{ $t('chatMode.instructions') }}</span>
        </span>
        <textarea
          v-model="settings.chat_instructions"
          rows="6"
          maxlength="20000"
          :aria-label="$t('chatMode.instructions')"
          :disabled="busy"
        />
      </label>
      <div class="settings-inline-actions right">
        <button type="button" class="settings-primary-button" :disabled="busy" @click="save">
          {{ busy ? $t('common.saving') : $t('common.save') }}
        </button>
      </div>
    </template>
    <div class="personal-status-group" role="status" aria-live="polite">
      <span v-if="error" class="status error">{{ error }}</span>
      <span v-else-if="saved" class="status success">{{ $t('chatMode.saved') }}</span>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, inject, onMounted, ref, unref } from 'vue';
import { chatApi, command } from '@/chat/api';
import type { ChatDefaults, ChatSettings } from '@/chat/types';
import type { ModelGroupOption } from '@/utils/modelGroups';
import FancyCheck from '@/components/common/FancyCheck.vue';
import ModelSelectDropdown from '@/components/personalization/ModelSelectDropdown.vue';
import SettingsChoiceDropdown from '@/components/personalization/SettingsChoiceDropdown.vue';

const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const settings = ref<ChatDefaults | null>(null);
const models = ref<ModelGroupOption[]>([]);
const busy = ref(false);
const error = ref('');
const saved = ref(false);
const efforts = computed(() => unref(ctx.reasoningEffortOptions));
async function save() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  saved.value = false;
  try {
    await command('/settings', { ...settings.value }, 'PATCH');
    saved.value = true;
  } catch (cause) {
    error.value = String((cause as Error).message || cause);
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  try {
    const result = await chatApi<ChatSettings>('/settings');
    settings.value = result.settings;
    models.value = result.models.map((model) => ({
      key: model.key,
      label: model.name,
      providerId: model.provider_id,
      providerName: model.provider_name
    }));
  } catch (cause) {
    error.value = String((cause as Error).message || cause);
  }
});
</script>

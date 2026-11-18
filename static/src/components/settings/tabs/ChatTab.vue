<template>
  <section class="stg-page" @focusout="flushSave">
    <template v-if="settings">
      <div class="stg-card">
        <div class="stg-select-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('chatMode.defaultModel') }}</span>
            <span class="stg-row-desc">{{ $t('chatMode.defaultModelDesc') }}</span>
          </span>
          <UiModelSelect
            :model-value="settings.chat_default_model"
            :options="models"
            align-end
            @update:model-value="settings.chat_default_model = $event"
          />
        </div>
        <div class="stg-select-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('chatMode.effort') }}</span>
            <span class="stg-row-desc">{{ $t('chatMode.effortDesc') }}</span>
          </span>
          <UiSelect
            :model-value="settings.chat_default_reasoning_effort || ''"
            :options="effortItems"
            align-end
            @update:model-value="updateEffort"
          />
        </div>
        <label class="stg-toggle-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('chatMode.defaultNetwork') }}</span>
            <span class="stg-row-desc">{{ $t('chatMode.defaultNetworkDesc') }}</span>
          </span>
          <input v-model="settings.chat_network_enabled" type="checkbox" />
          <UiSwitch decorative :checked="settings.chat_network_enabled" />
        </label>
        <label class="stg-input-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('personalization.deepTriggerTokens') }}</span>
            <span class="stg-row-desc">{{ $t('chatMode.deepCompressionContextDesc') }}</span>
          </span>
          <input
            v-model.number="settings.chat_deep_compress_trigger_tokens"
            type="number"
            class="stg-number-input"
            min="1000"
            max="2000000"
            step="1000"
            :aria-label="$t('personalization.deepTriggerTokens')"
          />
        </label>
        <label class="stg-textarea-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('chatMode.instructions') }}</span>
            <span class="stg-row-desc">{{ $t('chatMode.instructionsDesc') }}</span>
          </span>
          <textarea
            v-model="settings.chat_instructions"
            rows="6"
            maxlength="20000"
            :aria-label="$t('chatMode.instructions')"
          />
        </label>
      </div>
    </template>
    <p v-if="error" class="chat-settings-error" role="alert">{{ error }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, unref, watch } from 'vue';
import { chatApi } from '@/chat/api';
import { createSaveQueue } from '@/utils/saveQueue';
import type { ChatDefaults, ChatSettings } from '@/chat/types';
import type { ModelGroupOption } from '@/utils/modelGroups';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { t } from '@/locales';

const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const settings = ref<ChatDefaults | null>(null);
const models = ref<ModelGroupOption[]>([]);
const busy = ref(false);
const error = ref('');
const efforts = computed(() => unref(ctx.reasoningEffortOptions));
const effortItems = computed(() =>
  efforts.value.map((option: any) => ({
    value: option.value || '',
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
function updateEffort(value: string) {
  if (settings.value) settings.value.chat_default_reasoning_effort = value || null;
}
// 改动后自动保存（与其他分区一致，不放保存按钮），统一走 utils/saveQueue：
// 停手 1s 保存、连续修改最长 3s 一次、同时最多一个请求在途；失焦／离开分区／关闭页面立即保存。
const saveQueue = createSaveQueue<void>({
  run: async ({ keepalive }) => {
    if (!settings.value) return;
    busy.value = true;
    error.value = '';
    try {
      await chatApi('/settings', {
        method: 'PATCH',
        keepalive,
        body: JSON.stringify({ ...settings.value })
      });
    } catch (cause) {
      error.value = String((cause as Error).message || cause);
    } finally {
      busy.value = false;
    }
  }
});
const scheduleSave = () => saveQueue.schedule();
const flushSave = () => {
  if (saveQueue.hasPending()) void saveQueue.flush();
};
const flushForUnload = () => {
  if (saveQueue.hasPending()) void saveQueue.flush({ keepalive: true });
};
window.addEventListener('pagehide', flushForUnload);
let loadedOnce = false;
watch(
  settings,
  () => {
    // 首次加载赋值不触发保存
    if (!loadedOnce) {
      loadedOnce = true;
      return;
    }
    scheduleSave();
  },
  { deep: true }
);
onBeforeUnmount(() => {
  // 离开分区时立即提交尚未保存的改动
  window.removeEventListener('pagehide', flushForUnload);
  flushSave();
});
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

<style scoped>
.chat-settings-error {
  margin: 8px 0 0;
  color: var(--state-danger);
  font-size: 12px;
}
</style>

<script setup lang="ts">
import { computed, inject } from 'vue';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import { t } from '@/locales';

defineOptions({ name: 'SubAgentsTab' });
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  form,
  personalization,
  saveSubAgentSettings,
  subAgentMaxInstances,
  subAgentCompressThreshold,
  subAgentModels,
  subAgentSettingsLoading,
  subAgentSettingsLoaded,
  subAgentSettingsError
} = ctx;
const auxModelOptions = computed(() =>
  (subAgentModels.value || []).map((model: any) => ({
    key: model.key,
    label: model.name || model.key
  }))
);
const defaultExtraOptions = computed(() => [
  { key: '', label: t('personalization.subAgentModelUnconfigured') }
]);
const intelligenceLevels = ['high', 'medium', 'low'] as const;
</script>

<template>
  <section class="stg-page">
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">
        {{ $t('personalization.subAgentModelTitle') }}
      </span>
    </div>
    <div class="stg-card">
      <div v-for="level in intelligenceLevels" :key="level" class="stg-select-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{
              $t(`personalization.subAgentIntelligence.${level}`)
            }}</span>
            <span class="stg-row-desc">{{
              $t(`personalization.subAgentIntelligenceDesc.${level}`)
            }}</span>
          </span>
          <UiModelSelect
            :model-value="form[`sub_agent_model_${level}`]"
            :options="auxModelOptions"
            :extra-options="defaultExtraOptions"
            align-end
            @update:model-value="
              (value) => personalization.updateField({ key: `sub_agent_model_${level}`, value })
            "
          />
        </div>
    </div>
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">
        {{ $t('settings.subAgentsTitle') }}
      </span>
    </div>
    <!-- 数量与压缩阈值：改动后失焦即保存（不放保存按钮） -->
    <div class="stg-card">
      <div class="stg-input-row">
        <label class="stg-row-copy" for="sub-agent-max-instances">
          <span class="stg-row-title">{{ $t('personalization.subAgentMaxInstancesTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.subAgentMaxInstancesDesc') }}</span>
        </label>
        <input
          id="sub-agent-max-instances"
          v-model.number="subAgentMaxInstances"
          type="number"
          class="stg-number-input"
          :min="1"
          :step="1"
          :disabled="subAgentSettingsLoading || !subAgentSettingsLoaded"
          @change="saveSubAgentSettings"
        />
      </div>
      <div class="stg-input-row">
        <label class="stg-row-copy" for="sub-agent-compress-threshold">
          <span class="stg-row-title">{{ $t('personalization.compressThresholdTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.compressThresholdDesc') }}</span>
        </label>
        <input
          id="sub-agent-compress-threshold"
          v-model.number="subAgentCompressThreshold"
          type="number"
          class="stg-number-input"
          :min="10000"
          :step="10000"
          :disabled="subAgentSettingsLoading || !subAgentSettingsLoaded"
          @change="saveSubAgentSettings"
        />
      </div>
    </div>
    <p v-if="subAgentSettingsError" class="sub-agent-settings-error" role="alert">
      {{ subAgentSettingsError }}
    </p>
  </section>
</template>

<style scoped>
.sub-agent-settings-error {
  margin: 8px 0;
  max-height: 60px;
  overflow: auto;
  color: var(--state-danger);
  font-size: 12px;
}
</style>

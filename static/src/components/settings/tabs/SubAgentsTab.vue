<script setup lang="ts">
import { computed, inject } from 'vue';
import ModelSelectDropdown from '@/components/personalization/ModelSelectDropdown.vue';
import { t } from '@/locales';

defineOptions({ name: 'SubAgentsTab' });
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  form,
  personalization,
  saveSubAgentSettings,
  subAgentCompressThreshold,
  subAgentModels,
  subAgentSettingsSaving
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
  <section class="settings-page">
    <p class="settings-section-desc">{{ $t('personalization.subAgentsIntro') }}</p>
    <div class="settings-section-header">
      <span class="settings-section-title">{{ $t('personalization.subAgentModelTitle') }}</span>
    </div>
    <p class="settings-section-desc">{{ $t('personalization.subAgentModelDesc') }}</p>
    <div
      v-for="level in intelligenceLevels"
      :key="level"
      class="settings-select-row sub-agent-model-row"
    >
      <span class="settings-row-copy">
        <span class="settings-row-title">{{
          $t(`personalization.subAgentIntelligence.${level}`)
        }}</span>
        <span class="settings-row-desc">{{
          $t(`personalization.subAgentIntelligenceDesc.${level}`)
        }}</span>
      </span>
      <ModelSelectDropdown
        :model-value="form[`sub_agent_model_${level}`]"
        :options="auxModelOptions"
        :extra-options="defaultExtraOptions"
        @select="(value) => personalization.updateField({ key: `sub_agent_model_${level}`, value })"
      />
    </div>
    <div class="settings-action-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.compressThresholdTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.compressThresholdDesc') }}</span>
      </span>
      <div class="sub-agent-compression-controls">
        <input
          v-model.number="subAgentCompressThreshold"
          type="number"
          class="settings-number-input"
          :min="10000"
          :step="10000"
        />
        <button
          type="button"
          class="settings-secondary-button"
          :disabled="subAgentSettingsSaving"
          @click="saveSubAgentSettings"
        >
          {{ subAgentSettingsSaving ? $t('personalization.saving') : $t('common.save') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.sub-agent-model-row {
  height: 64px;
  margin-bottom: 16px;
}
.sub-agent-compression-controls {
  display: flex;
  gap: 6px;
  align-items: center;
}
.sub-agent-compression-controls input {
  width: 120px;
}
</style>

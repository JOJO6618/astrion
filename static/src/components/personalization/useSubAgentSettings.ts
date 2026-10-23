import { ref } from 'vue';
import { t } from '@/locales';

export function useSubAgentSettings() {
  const subAgentMaxInstances = ref(20);
  const subAgentCompressThreshold = ref(250000);
  const subAgentSettingsSaving = ref(false);
  const subAgentSettingsLoading = ref(false);
  const subAgentSettingsLoaded = ref(false);
  const subAgentSettingsError = ref('');

  const applySettings = (settings: Record<string, number>) => {
    subAgentMaxInstances.value = settings.sub_agent_max_instances ?? 20;
    subAgentCompressThreshold.value = settings.sub_agent_compress_threshold_tokens ?? 250000;
  };

  const loadSubAgentSettings = async () => {
    subAgentSettingsLoading.value = true;
    subAgentSettingsLoaded.value = false;
    subAgentSettingsError.value = '';
    try {
      const response = await fetch('/api/sub_agents/settings', { credentials: 'same-origin' });
      const data = await response.json();
      if (!response.ok || !data.success || !data.settings) {
        throw new Error(data.error || t('personalization.subAgentSettingsLoadFailed'));
      }
      applySettings(data.settings);
      subAgentSettingsLoaded.value = true;
    } catch (error) {
      subAgentSettingsError.value =
        error instanceof Error ? error.message : t('personalization.subAgentSettingsLoadFailed');
    } finally {
      subAgentSettingsLoading.value = false;
    }
  };

  const saveSubAgentSettings = async () => {
    if (!subAgentSettingsLoaded.value || subAgentSettingsSaving.value) return;
    subAgentSettingsError.value = '';
    if (!Number.isSafeInteger(subAgentMaxInstances.value) || subAgentMaxInstances.value < 1) {
      subAgentSettingsError.value = t('personalization.subAgentMaxInstancesInvalid');
      return;
    }
    if (
      !Number.isSafeInteger(subAgentCompressThreshold.value) ||
      subAgentCompressThreshold.value < 10000
    ) {
      subAgentSettingsError.value = t('personalization.subAgentCompressThresholdInvalid');
      return;
    }
    subAgentSettingsSaving.value = true;
    try {
      const response = await fetch('/api/sub_agents/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          settings: {
            sub_agent_max_instances: subAgentMaxInstances.value,
            sub_agent_compress_threshold_tokens: subAgentCompressThreshold.value
          }
        })
      });
      const data = await response.json();
      if (!response.ok || !data.success || !data.settings) {
        throw new Error(data.error || t('personalization.subAgentSettingsSaveFailed'));
      }
      applySettings(data.settings);
    } catch (error) {
      subAgentSettingsError.value =
        error instanceof Error ? error.message : t('personalization.subAgentSettingsSaveFailed');
    } finally {
      subAgentSettingsSaving.value = false;
    }
  };

  return {
    subAgentMaxInstances,
    subAgentCompressThreshold,
    subAgentSettingsSaving,
    subAgentSettingsLoading,
    subAgentSettingsLoaded,
    subAgentSettingsError,
    loadSubAgentSettings,
    saveSubAgentSettings
  };
}

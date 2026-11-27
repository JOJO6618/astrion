<script setup lang="ts">
import { computed, inject } from 'vue';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import { t } from '@/locales';

defineOptions({ name: 'ModelPrefTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  form,
  filteredModelOptions,
  selectDefaultModel,
  selectDefaultReasoningEffort,
  selectDefaultRunMode,
  runModeOptions,
  reasoningEffortOptions
} = ctx;
const runModeItems = computed(() =>
  runModeOptions.map((option: any) => ({
    value: option.value,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const reasoningEffortItems = computed(() =>
  reasoningEffortOptions.map((option: any) => ({
    value: option.value || '',
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const updateReasoningEffort = (value: string) => selectDefaultReasoningEffort(value || null);
</script>

<template>
  <section class="stg-page" data-tutorial="personal-page-model">
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.defaultModelTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.defaultModelDesc') }}</span></span
        >
        <UiModelSelect
          :model-value="form.default_model"
          :options="filteredModelOptions"
          align-end
          @update:model-value="selectDefaultModel"
        />
      </div>
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.defaultThinkingModeTitle') }}</span
          ><span class="stg-row-desc">{{
            $t('personalization.defaultThinkingModeDesc')
          }}</span></span
        >
        <UiSelect
          :model-value="form.default_run_mode"
          :options="runModeItems"
          align-end
          @update:model-value="selectDefaultRunMode"
        />
      </div>
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.reasoningEffortTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.reasoningEffortDesc') }}</span></span
        >
        <UiSelect
          :model-value="form.default_reasoning_effort || ''"
          :options="reasoningEffortItems"
          align-end
          @update:model-value="updateReasoningEffort"
        />
      </div>
      <!-- 标题生成模型选择器已挪到「常规」页 autoTitle 开关下方 -->
    </div>
  </section>
</template>

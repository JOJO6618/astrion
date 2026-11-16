<script setup lang="ts">
import { computed, inject } from 'vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import { t } from '@/locales';

defineOptions({ name: 'FilesTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const { form, imageCompressionOptions, selectImageCompression } = ctx;
const imageCompressionItems = computed(() =>
  imageCompressionOptions.map((option: any) => ({
    value: option.id,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
</script>

<template>
  <section class="stg-page" data-tutorial="personal-page-image">
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.imageCompressionTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.imageCompressionDesc') }}</span></span
        >
        <UiSelect
          :model-value="form.image_compression"
          :options="imageCompressionItems"
          align-end
          @update:model-value="selectImageCompression"
        />
      </div>
    </div>
  </section>
</template>

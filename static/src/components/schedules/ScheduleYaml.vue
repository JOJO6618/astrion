<script setup lang="ts">
import { t } from '@/locales';
import ScheduleIcon from './ScheduleIcon.vue';

defineProps<{
  text: string | null;
  revision?: number;
  error: string;
  applying: boolean;
}>();
const emit = defineEmits<{
  (event: 'back'): void;
  (event: 'input', value: string): void;
  (event: 'copy'): void;
  (event: 'download'): void;
  (event: 'apply'): void;
}>();
</script>
<template>
  <div class="sch-subview-toolbar">
    <div class="sch-subview-heading">
      <button
        type="button"
        class="sch-icon-button"
        :aria-label="t('schedules.backToSettings')"
        @click="emit('back')"
      >
        <ScheduleIcon name="arrow-left" />
      </button>
      <h3>{{ t('schedules.yaml') }}</h3>
      <span v-if="revision">v{{ revision }}</span>
    </div>
    <div class="sch-inline-actions">
      <button
        type="button"
        class="sch-button sch-button-ghost"
        :disabled="text === null"
        @click="emit('copy')"
      >
        <ScheduleIcon name="copy" />{{ t('schedules.copy') }}
      </button>
      <button
        type="button"
        class="sch-button sch-button-ghost"
        :disabled="text === null"
        @click="emit('download')"
      >
        <ScheduleIcon name="download" />{{ t('schedules.downloadShort') }}
      </button>
    </div>
  </div>
  <textarea
    class="sch-yaml-editor"
    :value="text ?? ''"
    :placeholder="text === null ? t('schedules.loading') : ''"
    :aria-label="t('schedules.yamlAria')"
    :disabled="text === null"
    spellcheck="false"
    @input="emit('input', ($event.target as HTMLTextAreaElement).value)"
  />
  <div v-if="error" class="sch-yaml-error" role="alert">{{ error }}</div>
  <div class="sch-yaml-actions">
    <button
      type="button"
      class="sch-button sch-button-quiet"
      :disabled="text === null || applying"
      @click="emit('apply')"
    >
      <ScheduleIcon name="check" />{{ t('schedules.applyYaml') }}
    </button>
  </div>
</template>

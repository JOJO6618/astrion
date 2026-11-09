<template>
  <div class="nb-file-import">
    <p class="nb-credential-note">{{ $t('browser.fileImportHint') }}</p>
    <p class="nb-credential-note">{{ $t('browser.passwordCsvHelp') }}</p>
    <button class="nb-text-button" :disabled="disabled || reading" @click="choose('password-csv')">
      {{ $t('browser.choosePasswordCsv') }}
    </button>
    <p class="nb-credential-note">{{ $t('browser.cookieMigrationHelp') }}</p>
    <button class="nb-text-button" :disabled="disabled || reading" @click="choose('cookie-json')">
      {{ $t('browser.chooseCookieExport') }}
    </button>
    <p class="nb-credential-note">{{ $t('browser.exportFileSensitive') }}</p>
    <input
      ref="picker"
      class="nb-file-picker"
      type="file"
      :accept="format === 'password-csv' ? '.csv,text/csv' : '.json,application/json'"
      @change="read"
    />
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
import { t } from '@/locales';
const props = defineProps<{ disabled: boolean }>();
const emit = defineEmits<{
  preview: [value: { format: 'password-csv' | 'cookie-json'; content: string }];
  error: [message: string];
}>();
const picker = ref<HTMLInputElement>();
const format = ref<'password-csv' | 'cookie-json'>('password-csv');
const reading = ref(false);
let generation = 0;
onBeforeUnmount(() => {
  generation++;
});
function choose(value: typeof format.value) {
  format.value = value;
  if (picker.value) {
    picker.value.value = '';
    picker.value.accept = value === 'password-csv' ? '.csv,text/csv' : '.json,application/json';
    picker.value.click();
  }
}
async function read(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  const selectedFormat = format.value;
  input.value = '';
  if (!file || props.disabled || reading.value) return;
  if (file.size > 4 * 1024 * 1024) {
    emit('error', t('browser.importFileTooLarge'));
    return;
  }
  const ticket = ++generation;
  reading.value = true;
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let content: string;
    try {
      content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } finally {
      bytes.fill(0);
    }
    if (ticket === generation && !props.disabled)
      emit('preview', { format: selectedFormat, content });
  } catch {
    if (ticket === generation) emit('error', t('browser.invalidImportFile'));
  } finally {
    if (ticket === generation) reading.value = false;
  }
}
</script>
<style scoped>
.nb-file-picker {
  display: none;
}
.nb-file-import > button {
  height: 32px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { t } from '@/locales';

const supported = ref(false);
const enabled = ref(false);
const busy = ref(true);
const error = ref('');
const controller = new AbortController();
let disposed = false;

onBeforeUnmount(() => {
  disposed = true;
  controller.abort();
});

async function requestSettings(value?: boolean) {
  const response = await fetch('/api/crosspet', {
    method: value === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    signal: controller.signal,
    ...(value === undefined
      ? {}
      : {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: value })
        })
  });
  const data = await response.json();
  if (!response.ok || data.success !== true) {
    throw new Error(data.error || t('auth.serviceUnavailable'));
  }
  return data;
}

onMounted(async () => {
  try {
    const data = await requestSettings();
    if (disposed) return;
    supported.value = data.supported === true;
    enabled.value = data.enabled === true;
  } catch (err) {
    if (!disposed) error.value = err instanceof Error ? err.message : t('auth.networkErrorRetry');
  } finally {
    if (!disposed) busy.value = false;
  }
});

async function setEnabled(value: boolean) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    const data = await requestSettings(value);
    if (!disposed) enabled.value = data.enabled === true;
  } catch (err) {
    if (!disposed) error.value = err instanceof Error ? err.message : t('auth.networkErrorRetry');
  } finally {
    if (!disposed) busy.value = false;
  }
}
</script>

<template>
  <div v-if="supported" class="stg-section-divider">
    <span class="stg-section-divider__label">{{ $t('settings.crosspetTitle') }}</span>
  </div>
  <div v-if="supported" class="stg-card">
    <label class="stg-toggle-row">
      <span class="stg-row-copy">
        <span class="stg-row-desc">{{ $t('settings.crosspetDesc') }}</span>
      </span>
      <input
        type="checkbox"
        :checked="enabled"
        :disabled="busy"
        @change="setEnabled(($event.target as HTMLInputElement).checked)"
      />
      <UiSwitch decorative :checked="enabled" />
    </label>
    <p v-if="error" class="crosspet-settings-error" role="alert">
      {{ $t('settings.crosspetError') }} {{ error }}
    </p>
  </div>
  <p v-else-if="error" class="crosspet-settings-error" role="alert">
    {{ $t('settings.crosspetError') }} {{ error }}
  </p>
</template>

<style scoped>
.crosspet-settings-error {
  margin: 8px 0;
  max-height: 60px;
  overflow: auto;
  font-size: 13px;
  line-height: 20px;
  color: var(--state-danger);
}
</style>

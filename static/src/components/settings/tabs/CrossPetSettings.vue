<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
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
  <label v-if="supported" class="settings-toggle-row">
    <span class="settings-row-copy">
      <span class="settings-row-title">{{ $t('settings.crosspetTitle') }}</span>
      <span class="settings-row-desc">{{ $t('settings.crosspetDesc') }}</span>
    </span>
    <input
      type="checkbox"
      :checked="enabled"
      :disabled="busy"
      @change="setEnabled(($event.target as HTMLInputElement).checked)"
    />
    <FancyCheck :checked="enabled" />
  </label>
  <p v-if="error" class="crosspet-settings-error" role="alert">
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

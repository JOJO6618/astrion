<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { isDesktopShell } from '@/utils/desktopPlatform';

const applicable = ref(false);
const enabled = ref(false);
const confirming = ref(false);
const password = ref('');
const busy = ref(false);
const error = ref('');
const controller = new AbortController();
let disposed = false;
const command = "python3 scripts/host_password.py --password 'YOUR_PASSWORD'";

onBeforeUnmount(() => {
  disposed = true;
  password.value = '';
  controller.abort();
});

onMounted(async () => {
  if (isDesktopShell()) return;
  try {
    const response = await fetch('/api/host-password', {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal
    });
    const data = await response.json();
    if (disposed) return;
    if (!response.ok || !data.success) throw new Error(data.error || t('auth.serviceUnavailable'));
    applicable.value = data.applicable === true;
    enabled.value = data.enabled === true;
  } catch (err) {
    if (!disposed) error.value = err instanceof Error ? err.message : t('auth.networkErrorRetry');
  }
});

function cancel() {
  confirming.value = false;
  password.value = '';
  error.value = '';
}

async function disable() {
  if (busy.value || !password.value) return;
  busy.value = true;
  error.value = '';
  try {
    const response = await fetch('/api/host-password/disable', {
      method: 'POST',
      credentials: 'same-origin',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value })
    });
    const data = await response.json();
    if (disposed) return;
    if (!response.ok || !data.success) throw new Error(data.error || t('auth.serviceUnavailable'));
    password.value = '';
    window.location.assign('/login');
  } catch (err) {
    if (!disposed) error.value = err instanceof Error ? err.message : t('auth.networkErrorRetry');
  } finally {
    if (!disposed) busy.value = false;
  }
}
</script>

<template>
  <div v-if="applicable" class="host-password-settings">
    <div class="settings-action-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('settings.hostPasswordTitle') }}</span>
        <span class="settings-row-desc">
          {{ $t(enabled ? 'settings.hostPasswordEnabled' : 'settings.hostPasswordDisabled') }}
        </span>
      </span>
      <button
        v-if="enabled && !confirming"
        type="button"
        class="settings-secondary-button"
        @click="confirming = true"
      >
        {{ $t('settings.hostPasswordDisable') }}
      </button>
    </div>
    <div v-if="!enabled" class="host-password-command">
      <span>{{ $t('settings.hostPasswordScriptHint') }}</span>
      <code>{{ command }}</code>
    </div>
    <form v-if="confirming" class="host-password-confirm" @submit.prevent="disable">
      <label for="disable-host-password">{{ $t('settings.hostPasswordConfirmHint') }}</label>
      <div class="host-password-controls">
        <input
          id="disable-host-password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          :disabled="busy"
        />
        <button type="button" class="settings-secondary-button" :disabled="busy" @click="cancel">
          {{ $t('common.cancel') }}
        </button>
        <button type="submit" class="settings-secondary-button" :disabled="busy || !password">
          {{ $t('settings.hostPasswordDisable') }}
        </button>
      </div>
    </form>
    <p v-if="error" class="host-password-error" role="alert">{{ error }}</p>
  </div>
  <p v-else-if="error" class="host-password-error" role="alert">{{ error }}</p>
</template>

<style scoped>
.host-password-settings {
  border-bottom: 1px solid var(--border-default);
}
.host-password-confirm,
.host-password-command {
  padding: 0 0 16px;
}
.host-password-confirm label,
.host-password-command span {
  display: block;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
}
.host-password-controls {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}
.host-password-controls input {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  color: var(--text-primary);
  outline: none;
}
.host-password-controls input:focus {
  border-color: var(--accent);
}
.host-password-controls button {
  flex-shrink: 0;
}
.host-password-command code {
  display: block;
  max-width: 100%;
  height: 32px;
  line-height: 32px;
  overflow: auto hidden;
  white-space: nowrap;
  scrollbar-width: thin;
  font-size: 12px;
  color: var(--text-primary);
}
.host-password-error {
  margin: 8px 0;
  font-size: 13px;
  color: var(--state-danger);
}
@media (width <= 480px) {
  .host-password-controls {
    flex-wrap: wrap;
  }
  .host-password-controls input {
    flex-basis: 100%;
  }
}
</style>

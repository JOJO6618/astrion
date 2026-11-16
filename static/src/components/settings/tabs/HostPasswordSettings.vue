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
  <div v-if="applicable" class="stg-card host-password-settings">
    <div class="stg-action-row">
      <span class="stg-row-copy">
        <span class="stg-row-title">{{ $t('settings.hostPasswordTitle') }}</span>
        <span class="stg-row-desc">
          {{ $t(enabled ? 'settings.hostPasswordEnabled' : 'settings.hostPasswordDisabled') }}
        </span>
      </span>
      <button
        v-if="enabled"
        type="button"
        class="stg-secondary-button"
        :disabled="busy"
        @click="confirming ? cancel() : (confirming = true)"
      >
        {{ confirming ? $t('common.cancel') : $t('settings.hostPasswordDisable') }}
      </button>
    </div>
    <div v-if="!enabled" class="host-password-command">
      <span>{{ $t('settings.hostPasswordScriptHint') }}</span>
      <code>{{ command }}</code>
    </div>
    <!-- 关闭保护：标题行按钮切换为「取消」，下方长输入框输入当前密码，确认作为框内尾部操作 -->
    <form v-if="confirming" class="host-password-confirm" @submit.prevent="disable">
      <div class="stg-field">
        <input
          id="disable-host-password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          :placeholder="$t('settings.hostPasswordConfirmHint')"
          :aria-label="$t('settings.hostPasswordConfirmHint')"
          :disabled="busy"
        />
        <button v-if="password" type="submit" class="stg-field-action is-text" :disabled="busy">
          {{ $t('settings.hostPasswordDisable') }}
        </button>
      </div>
    </form>
    <p v-if="error" class="host-password-error" role="alert">{{ error }}</p>
  </div>
  <p v-else-if="error" class="host-password-error" role="alert">{{ error }}</p>
</template>

<style scoped>
.host-password-confirm,
.host-password-command {
  padding: 0 0 12px;
}
.host-password-command span {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-tertiary);
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
  font-size: 12px;
  color: var(--state-danger);
}
</style>

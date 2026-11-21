<template>
  <main class="auth-page">
    <section class="auth-card" :aria-busy="!ready || submitting">
      <h1 class="auth-title">{{ t('auth.hostLoginTitle') }}</h1>
      <p v-if="ready" class="auth-subtitle">
        {{ t('auth.hostLoginSubtitle') }}
      </p>

      <form v-if="ready" @submit.prevent="hostLogin">
        <div v-if="hostPasswordRequired" class="auth-form-group">
          <label class="auth-label" for="password">{{ t('auth.password') }}</label>
          <input
            id="password"
            v-model="password"
            type="password"
            class="auth-input"
            autocomplete="current-password"
            :disabled="!canSubmit || submitting"
          />
        </div>

        <button type="submit" class="auth-button" :disabled="!canSubmit || submitting">
          {{ t(hostPasswordRequired ? 'auth.hostLogin' : 'auth.hostModeNoLogin') }}
        </button>
      </form>

      <div class="auth-error" role="alert">{{ error }}</div>
      <button v-if="!ready && error" class="auth-secondary-button" @click="loadStatus">
        {{ t('common.retry') }}
      </button>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { applyTheme, loadTheme } from './theme';

declare global {
  interface Window {
    ensureCsrfToken?: () => Promise<unknown>;
  }
}

const password = ref('');
const error = ref('');
const submitting = ref(false);
const ready = ref(false);
const hostModeEnabled = ref(false);
const hostPasswordRequired = ref(false);
const configValid = ref(true);

const canSubmit = computed(() => {
  if (!ready.value || !configValid.value || !hostModeEnabled.value) return false;
  return !hostPasswordRequired.value || !!password.value;
});

async function loadStatus() {
  error.value = '';
  try {
    const response = await fetch('/api/host-mode-enabled', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || data.success !== true) throw new Error('status');
    hostModeEnabled.value = data.enabled === true;
    hostPasswordRequired.value = hostModeEnabled.value && data.password_required === true;
    configValid.value = data.config_valid !== false;
    if (!configValid.value) {
      error.value = data.error || t('auth.serviceUnavailable');
    } else if (!hostModeEnabled.value) {
      error.value = t('auth.hostModeUnavailable');
    }
    ready.value = true;
  } catch {
    ready.value = false;
    error.value = t('auth.networkErrorRetry');
  }
}

async function hostLogin() {
  if (!canSubmit.value || submitting.value) return;
  if (hostPasswordRequired.value && !password.value) {
    error.value = t('auth.hostPasswordRequired');
    return;
  }
  submitting.value = true;
  error.value = '';
  try {
    if (window.ensureCsrfToken) await window.ensureCsrfToken();
    const response = await fetch('/host-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value })
    });
    const data = await response.json();
    if (response.ok && data.success) {
      password.value = '';
      window.location.href = '/';
      return;
    }
    // The script can enable/reset protection while this page is already open.
    const message = data.error || t('auth.hostModeUnavailable');
    await loadStatus();
    error.value = message;
  } catch {
    error.value = t('auth.networkErrorRetry');
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  applyTheme(loadTheme());
  await loadStatus();
  // The shell flag only triggers the UX; exemption is decided by the backend.
  if (
    ready.value &&
    configValid.value &&
    hostModeEnabled.value &&
    !hostPasswordRequired.value &&
    (window as any).__ASTRION_DESKTOP__
  ) {
    await hostLogin();
  }
});
</script>

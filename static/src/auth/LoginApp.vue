<template>
  <main class="auth-page">
    <section class="auth-card" :aria-busy="!ready || submitting">
      <h1 class="auth-title">
        {{ t(hostPasswordRequired ? 'auth.hostLoginTitle' : 'auth.loginTitle') }}
      </h1>
      <p v-if="ready" class="auth-subtitle">
        {{ t(hostPasswordRequired ? 'auth.hostLoginSubtitle' : 'auth.loginSubtitle') }}
      </p>

      <form v-if="ready" @submit.prevent="login">
        <div v-if="!hostPasswordRequired" class="auth-form-group">
          <label class="auth-label" for="email">{{ t('auth.email') }}</label>
          <input
            id="email"
            v-model.trim="email"
            type="email"
            class="auth-input"
            autocomplete="email"
          />
        </div>

        <div class="auth-form-group">
          <label class="auth-label" for="password">{{ t('auth.password') }}</label>
          <input
            id="password"
            v-model="password"
            type="password"
            class="auth-input"
            autocomplete="current-password"
            :disabled="!configValid || submitting"
          />
        </div>

        <button type="submit" class="auth-button" :disabled="submitting || !configValid">
          {{ t(hostPasswordRequired ? 'auth.hostLogin' : 'auth.login') }}
        </button>
        <button
          v-if="hostModeEnabled && !hostPasswordRequired"
          type="button"
          class="auth-secondary-button"
          :disabled="submitting || !configValid"
          @click="hostLogin"
        >
          {{ t('auth.hostModeNoLogin') }}
        </button>
      </form>

      <div class="auth-error" role="alert">{{ error }}</div>
      <div v-if="ready && !hostPasswordRequired" class="auth-link">
        {{ t('auth.noAccount') }}<a href="/register">{{ t('auth.signUp') }}</a>
      </div>
      <button v-if="!ready && error" class="auth-secondary-button" @click="loadStatus">
        {{ t('common.retry') }}
      </button>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { t } from '@/locales';
import { applyTheme, loadTheme } from './theme';

declare global {
  interface Window {
    ensureCsrfToken?: () => Promise<unknown>;
  }
}

const email = ref('');
const password = ref('');
const error = ref('');
const submitting = ref(false);
const ready = ref(false);
const hostModeEnabled = ref(false);
const hostPasswordRequired = ref(false);
const configValid = ref(true);

async function loadStatus() {
  error.value = '';
  try {
    const response = await fetch('/api/host-mode-enabled', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || data.success !== true) throw new Error('status');
    hostModeEnabled.value = data.enabled === true;
    hostPasswordRequired.value = hostModeEnabled.value && data.password_required === true;
    configValid.value = data.config_valid !== false;
    if (!configValid.value) error.value = data.error || t('auth.serviceUnavailable');
    ready.value = true;
  } catch {
    ready.value = false;
    error.value = t('auth.networkErrorRetry');
  }
}

async function sendLogin(host: boolean) {
  if (!ready.value || !configValid.value || submitting.value) return;
  if (
    (!host && (!email.value || !password.value)) ||
    (host && hostPasswordRequired.value && !password.value)
  ) {
    error.value = t(host ? 'auth.hostPasswordRequired' : 'auth.emailAndPasswordRequired');
    return;
  }
  submitting.value = true;
  error.value = '';
  try {
    if (window.ensureCsrfToken) await window.ensureCsrfToken();
    const response = await fetch(host ? '/host-login' : '/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(
        host ? { password: password.value } : { email: email.value, password: password.value }
      )
    });
    const data = await response.json();
    if (response.ok && data.success) {
      password.value = '';
      window.location.href = '/';
      return;
    }
    // The script can enable/reset protection while this page is already open.
    const message = data.error || t(host ? 'auth.hostModeUnavailable' : 'auth.loginFailed');
    await loadStatus();
    error.value = message;
  } catch {
    error.value = t('auth.networkErrorRetry');
  } finally {
    submitting.value = false;
  }
}

const login = () => sendLogin(hostPasswordRequired.value);
const hostLogin = () => sendLogin(true);

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

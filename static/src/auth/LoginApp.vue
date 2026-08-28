<template>
  <main class="auth-page">
    <section class="auth-card">
      <h1 class="auth-title">{{ t('auth.loginTitle') }}</h1>
      <p class="auth-subtitle">{{ t('auth.loginSubtitle') }}</p>

      <div class="auth-form-group">
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
          @keydown.enter="login"
        />
      </div>

      <button class="auth-button" :disabled="submitting" @click="login">{{ t('auth.login') }}</button>

      <button
        v-if="hostModeEnabled"
        class="auth-secondary-button"
        :disabled="hostSubmitting"
        @click="hostLogin"
      >
        {{ t('auth.hostModeNoLogin') }}
      </button>

      <div class="auth-error">{{ error }}</div>
      <div class="auth-link">{{ t('auth.noAccount') }}<a href="/register">{{ t('auth.signUp') }}</a></div>
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
const hostSubmitting = ref(false);
const hostModeEnabled = ref(false);

const doLogin = async (redirectUrl = '/') => {
  if (!email.value || !password.value) {
    error.value = t('auth.emailAndPasswordRequired');
    return;
  }

  submitting.value = true;
  error.value = '';

  try {
    const resp = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.value, password: password.value })
    });

    // 503 = ensure_container 失败（Docker 未启动/镜像缺失等），error 带具体原因，原地显示
    if (resp.status === 503) {
      const data = await resp.json().catch(() => ({} as any));
      error.value = data.error || t('auth.serviceUnavailable');
      return;
    }

    const data = await resp.json();
    if (data.success) {
      window.location.href = redirectUrl;
      return;
    }

    error.value = data.error || t('auth.loginFailed');
  } catch (_err) {
    error.value = t('auth.networkErrorRetry');
  } finally {
    submitting.value = false;
  }
};

const login = () => doLogin('/');

const doHostLogin = async (redirectUrl = '/') => {
  hostSubmitting.value = true;
  error.value = '';

  try {
    const resp = await fetch('/host-login', { method: 'POST' });
    // 503 = 容量满或终端创建失败，error 带具体原因，原地显示
    if (resp.status === 503) {
      const data = await resp.json().catch(() => ({} as any));
      error.value = data.error || t('auth.resourceBusy');
      return;
    }

    const data = await resp.json();
    if (data.success) {
      window.location.href = redirectUrl;
      return;
    }

    error.value = data.error || t('auth.hostModeUnavailable');
  } catch (_err) {
    error.value = t('auth.networkErrorRetry');
  } finally {
    hostSubmitting.value = false;
  }
};

const hostLogin = () => doHostLogin('/');

onMounted(async () => {
  applyTheme(loadTheme());

  if (window.ensureCsrfToken) {
    try {
      await window.ensureCsrfToken();
    } catch (_err) {
      // ignore
    }
  }

  try {
    const resp = await fetch('/api/host-mode-enabled');
    const data = await resp.json();
    hostModeEnabled.value = !!(data && data.success && data.enabled);
  } catch (_err) {
    hostModeEnabled.value = false;
  }

  try {
    const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
    const data = await resp.json();
  } catch (err) {
    console.warn('[auth-debug] login page session-status failed:', err);
  }
});
</script>

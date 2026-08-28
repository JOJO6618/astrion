<template>
  <main class="auth-page">
    <section class="auth-card">
      <h1 class="auth-title">{{ t('auth.createAccount') }}</h1>
      <p class="auth-subtitle">{{ t('auth.registerDoneHint') }}</p>

      <div class="auth-form-group">
        <label class="auth-label" for="username">{{ t('auth.username') }}</label>
        <input
          id="username"
          v-model.trim="username"
          type="text"
          class="auth-input"
          :placeholder="t('auth.usernamePlaceholder')"
          autocomplete="username"
        />
      </div>

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
          autocomplete="new-password"
          @keydown.enter="register"
        />
      </div>

      <div class="auth-form-group">
        <label class="auth-label" for="invite">{{ t('auth.inviteCode') }}</label>
        <input
          id="invite"
          v-model.trim="inviteCode"
          type="text"
          class="auth-input"
          :placeholder="t('auth.requiredPlaceholder')"
          autocomplete="off"
          @keydown.enter="register"
        />
      </div>

      <button class="auth-button" :disabled="submitting" @click="register">{{ t('auth.register') }}</button>
      <div class="auth-error">{{ error }}</div>
      <div class="auth-link">{{ t('auth.haveAccount') }}<a href="/login">{{ t('auth.backToLogin') }}</a></div>
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

const username = ref('');
const email = ref('');
const password = ref('');
const inviteCode = ref('');
const error = ref('');
const submitting = ref(false);

const register = async () => {
  if (!username.value || !email.value || !password.value || !inviteCode.value) {
    error.value = t('auth.fillAllFields');
    return;
  }

  submitting.value = true;
  error.value = '';

  try {
    const resp = await fetch('/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username.value,
        email: email.value,
        password: password.value,
        invite_code: inviteCode.value
      })
    });
    const data = await resp.json();

    if (data.success) {
      window.location.href = '/login';
      return;
    }

    error.value = data.error || t('auth.registerFailed');
  } catch (_err) {
    error.value = t('auth.networkErrorRetry');
  } finally {
    submitting.value = false;
  }
};

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
    const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
    const data = await resp.json();
  } catch (err) {
    console.warn('[auth-debug] register page session-status failed:', err);
  }
});
</script>

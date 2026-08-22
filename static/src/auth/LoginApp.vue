<template>
  <main class="auth-page">
    <section class="auth-card">
      <h1 class="auth-title">Astrion 登录</h1>
      <p class="auth-subtitle">使用账号继续访问工作台</p>

      <div class="auth-form-group">
        <label class="auth-label" for="email">邮箱</label>
        <input
          id="email"
          v-model.trim="email"
          type="email"
          class="auth-input"
          autocomplete="email"
        />
      </div>

      <div class="auth-form-group">
        <label class="auth-label" for="password">密码</label>
        <input
          id="password"
          v-model="password"
          type="password"
          class="auth-input"
          autocomplete="current-password"
          @keydown.enter="login"
        />
      </div>

      <button class="auth-button" :disabled="submitting" @click="login">登录</button>

      <button
        v-if="hostModeEnabled"
        class="auth-secondary-button"
        :disabled="hostSubmitting"
        @click="hostLogin"
      >
        宿主机模式（免登录）
      </button>

      <div class="auth-error">{{ error }}</div>
      <div class="auth-link">还没有账号？<a href="/register">点击注册</a></div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
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
    error.value = '请输入邮箱和密码';
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
      error.value = data.error || '服务暂时不可用，请稍后重试';
      return;
    }

    const data = await resp.json();
    if (data.success) {
      window.location.href = redirectUrl;
      return;
    }

    error.value = data.error || '登录失败';
  } catch (_err) {
    error.value = '网络错误，请重试';
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
      error.value = data.error || '资源繁忙，请稍后再试';
      return;
    }

    const data = await resp.json();
    if (data.success) {
      window.location.href = redirectUrl;
      return;
    }

    error.value = data.error || '宿主机模式不可用';
  } catch (_err) {
    error.value = '网络错误，请重试';
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

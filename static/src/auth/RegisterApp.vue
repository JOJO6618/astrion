<template>
  <main class="auth-page">
    <section class="auth-card">
      <h1 class="auth-title">创建账号</h1>
      <p class="auth-subtitle">完成注册后即可登录使用</p>

      <div class="auth-form-group">
        <label class="auth-label" for="username">用户名</label>
        <input
          id="username"
          v-model.trim="username"
          type="text"
          class="auth-input"
          placeholder="仅限小写字母/数字/下划线"
          autocomplete="username"
        />
      </div>

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
          autocomplete="new-password"
          @keydown.enter="register"
        />
      </div>

      <div class="auth-form-group">
        <label class="auth-label" for="invite">邀请码</label>
        <input
          id="invite"
          v-model.trim="inviteCode"
          type="text"
          class="auth-input"
          placeholder="必填"
          autocomplete="off"
          @keydown.enter="register"
        />
      </div>

      <button class="auth-button" :disabled="submitting" @click="register">注册</button>
      <div class="auth-error">{{ error }}</div>
      <div class="auth-link">已有账号？<a href="/login">返回登录</a></div>
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

const username = ref('');
const email = ref('');
const password = ref('');
const inviteCode = ref('');
const error = ref('');
const submitting = ref(false);

const register = async () => {
  if (!username.value || !email.value || !password.value || !inviteCode.value) {
    error.value = '请完整填写所有字段';
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

    error.value = data.error || '注册失败';
  } catch (_err) {
    error.value = '网络错误，请稍后再试';
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

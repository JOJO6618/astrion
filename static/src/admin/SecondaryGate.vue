<template>
  <div class="secondary-overlay">
    <!-- 未配置二级密码：设置引导页（verify 恒 401，给输入框是死锁） -->
    <div v-if="!configured" class="secondary-card secondary-card--guide">
      <h3>尚未设置二级密码</h3>
      <p class="muted">
        管理面板的敏感操作（邀请码、密码管理、策略配置等）由二级密码保护。
        当前服务端尚未配置二级密码，请先完成设置：
      </p>
      <ol class="guide-steps">
        <li>
          <span class="step-text">在服务器上生成密码哈希：</span>
          <code class="step-code">python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('你的二级密码'))"</code>
        </li>
        <li>
          <span class="step-text">将输出写入 settings.json 的 <code>admin</code> 段：</span>
          <code class="step-code">"admin": { "secondary_password_hash": "pbkdf2:sha256:..." }</code>
        </li>
        <li>
          <span class="step-text">重启服务，然后点击下方「重新检测」。</span>
        </li>
      </ol>
      <div class="secondary-actions">
        <button type="button" :disabled="loading" @click="$emit('recheck')">
          {{ loading ? '检测中...' : '重新检测' }}
        </button>
      </div>
      <p v-if="error" class="secondary-error">{{ error }}</p>
    </div>

    <!-- 已配置：常规的二级密码输入框 -->
    <div v-else class="secondary-card">
      <h3>请输入管理员二级密码</h3>
      <p class="muted">{{ description }}</p>
      <input
        class="secondary-input"
        type="password"
        v-model="password"
        :disabled="loading"
        placeholder="二级密码"
        @keyup.enter="submit"
      />
      <div class="secondary-actions">
        <button type="button" :disabled="loading" @click="submit">
          {{ loading ? '校验中...' : '确认进入' }}
        </button>
        <button type="button" class="ghost-btn" :disabled="loading" @click="$emit('recheck')">
          重新检测
        </button>
      </div>
      <p v-if="error" class="secondary-error">{{ error }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';

const props = withDefaults(
  defineProps<{
    configured: boolean;
    loading: boolean;
    error: string | null;
    description?: string;
  }>(),
  { description: '为保护敏感数据，需二级校验后查看。' }
);

const emit = defineEmits<{
  (e: 'verify', password: string): void;
  (e: 'recheck'): void;
}>();

const password = ref('');

const submit = () => {
  if (props.loading) return;
  emit('verify', password.value);
};
</script>

<!-- 非 scoped：admin 各入口未引入全局样式，token 定义随组件 chunk 走，确保 var() 可用 -->
<style lang="scss">
@use '../styles/base/tokens';
</style>

<style scoped>
.secondary-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.secondary-card {
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 24px;
  width: 420px;
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 96px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.secondary-card--guide {
  width: 520px;
}

.secondary-card h3 {
  margin: 0;
  font-size: 15px;
  color: var(--text-primary);
}

.secondary-card p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.guide-steps {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 13px;
  color: var(--text-secondary);
}

.step-text {
  display: block;
  margin-bottom: 4px;
  line-height: 1.5;
}

.step-code {
  display: block;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--surface-muted);
  border: 1px solid var(--border-default);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-primary);
  word-break: break-all;
  user-select: all;
}

.secondary-input {
  height: 34px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid var(--border-default);
  background: var(--surface-base);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}

.secondary-input:focus {
  border-color: var(--accent);
}

.secondary-actions {
  display: flex;
  gap: 8px;
}

.secondary-actions button {
  height: 32px;
  padding: 0 14px;
  border-radius: 6px;
  border: 1px solid var(--border-default);
  background: var(--surface-soft);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
}

.secondary-actions button:hover:not(:disabled) {
  background: var(--surface-muted);
}

.secondary-actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.secondary-actions .ghost-btn {
  background: transparent;
}

.secondary-error {
  margin: 0;
  font-size: 12px;
  color: var(--state-danger);
}
</style>

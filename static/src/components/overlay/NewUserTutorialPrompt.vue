<template>
  <transition name="overlay-fade">
    <div v-if="visible" class="new-user-tutorial-mask" @click.self="emitSkip">
      <div
        class="new-user-tutorial-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="新手教程提示"
      >
        <p class="eyebrow">欢迎</p>
        <h3>新用户 {{ username || '用户' }}</h3>
        <p class="desc">是否需要通过新手教程来快速认识这个系统？</p>
        <div class="actions">
          <button type="button" class="primary" :disabled="loading" @click="emitStart">
            {{ loading ? '处理中...' : '开始吧！' }}
          </button>
          <button type="button" class="ghost" :disabled="loading" @click="emitSkip">
            不再提示
          </button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
defineProps<{
  visible: boolean;
  username: string;
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: 'start'): void;
  (e: 'skip'): void;
}>();

const emitStart = () => emit('start');
const emitSkip = () => emit('skip');
</script>

<style scoped>
.new-user-tutorial-mask {
  position: fixed;
  inset: 0;
  z-index: 2100;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--theme-overlay-scrim);
  backdrop-filter: blur(2px);
}

.new-user-tutorial-dialog {
  width: min(520px, calc(100vw - 32px));
  border-radius: 18px;
  border: 1px solid var(--theme-control-border-strong);
  background: var(--theme-surface-card);
  box-shadow: var(--theme-shadow-strong);
  padding: 22px 22px 18px;
}

.eyebrow {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
}

h3 {
  margin: 6px 0 8px;
  font-size: 20px;
}

.desc {
  margin: 0;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.actions {
  margin-top: 18px;
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

button {
  min-height: 38px;
  border-radius: 999px;
  border: 1px solid var(--theme-control-border);
  padding: 0 15px;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
  transition:
    transform 0.16s ease,
    box-shadow 0.2s ease,
    opacity 0.2s ease;
}

.primary {
  border: none;
  background: linear-gradient(135deg, var(--accent), var(--accent-strong));
  color: var(--on-accent);
}

.ghost {
  background: var(--theme-surface-soft);
  color: var(--text-primary);
}

button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--theme-shadow-soft);
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>

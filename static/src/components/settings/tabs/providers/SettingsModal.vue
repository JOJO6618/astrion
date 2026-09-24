<script setup lang="ts">
/**
 * 设置页通用模态框骨架（对齐设计稿 .modal：不透明面板 + scrim 遮罩 + 头/体分离）。
 * 头部固定高，标题省略号；body 超限内部滚动（隐藏滚动条）。
 */
defineProps<{
  title: string;
}>();

const emit = defineEmits<{ (e: 'close'): void }>();

const onOverlayMouseDown = (event: MouseEvent) => {
  if (event.target === event.currentTarget && event.button === 0) {
    emit('close');
  }
};
</script>

<template>
  <div class="settings-modal-overlay" @mousedown="onOverlayMouseDown">
    <div class="settings-modal" role="dialog" aria-modal="true" :aria-label="title">
      <div class="settings-modal__head">
        <span class="settings-modal__icon"><slot name="icon" /></span>
        <span class="settings-modal__title">{{ title }}</span>
        <button
          type="button"
          class="settings-modal__close"
          :aria-label="$t('common.close')"
          :title="$t('common.close')"
          @click="emit('close')"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </div>
      <div class="settings-modal__body">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-modal-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 500;
}

.settings-modal {
  width: 560px;
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 96px);
  background: var(--surface-raised);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  box-shadow: var(--shadow-strong);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.settings-modal__head {
  height: 52px;
  flex: 0 0 52px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-default);
}

.settings-modal__icon {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

.settings-modal__icon:empty {
  display: none;
}

.settings-modal__title {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.settings-modal__close {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
}

.settings-modal__close:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.settings-modal__body {
  padding: 20px 20px 24px;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-modal__body::-webkit-scrollbar {
  display: none;
}
</style>

<!-- 对话框表单控件（全局类，供 providers/models 各弹窗复用；前缀 provider-form- 冲突风险低） -->
<style>
.provider-form-note {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 16px;
}

.provider-form-note a {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid var(--border-strong);
}

.provider-form-field {
  margin-bottom: 16px;
}

.provider-form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.provider-form-input {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border-radius: 6px;
  font-size: 13.5px;
  border: 1px solid var(--border-default);
  background: transparent;
  color: var(--text-primary);
  outline: none;
  font-family: inherit;
}

.provider-form-input:focus {
  border-color: var(--border-strong);
}

.provider-form-input::placeholder {
  color: var(--text-muted);
}

.provider-form-input.mono,
.provider-form-mono {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
}

.provider-form-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 5px;
  line-height: 1.5;
}

.provider-form-error {
  font-size: 12.5px;
  color: var(--state-danger);
  margin-top: 10px;
  line-height: 1.5;
  word-break: break-all;
}

.provider-form-success {
  font-size: 12.5px;
  color: var(--state-success);
  margin-top: 10px;
  line-height: 1.5;
}

.provider-form-warning {
  font-size: 12.5px;
  color: var(--state-warning);
  margin-top: 10px;
  line-height: 1.5;
  word-break: break-all;
}

.provider-form-actions {
  margin-top: 20px;
  display: flex;
  gap: 8px;
  align-items: center;
}

.provider-form-btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 6px;
  font-size: 13px;
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  background: var(--surface-raised);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  white-space: nowrap;
  cursor: pointer;
  flex-shrink: 0;
}

.provider-form-btn:hover:not(:disabled) {
  background: var(--surface-soft);
}

.provider-form-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.provider-form-btn.primary {
  background: var(--accent);
  border-color: transparent;
  color: var(--on-accent);
}

.provider-form-btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.provider-form-pair-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}

.provider-form-pair-row .provider-form-input {
  flex: 1;
  min-width: 0;
}

.provider-form-icon-btn {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
}

.provider-form-icon-btn:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.provider-form-add-row {
  font-size: 13px;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 4px;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.provider-form-add-row:hover {
  color: var(--text-primary);
}

.provider-form-switch-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  min-height: 24px;
}
</style>

<template>
  <transition name="confirm-dialog-fade">
    <div
      class="confirm-overlay"
      v-if="dialog?.visible"
      @click.self="dialog?.closeOnBackdrop !== false && resolve(false)"
    >
      <div
        class="confirm-modal"
        :class="{ 'confirm-modal--with-checkbox': dialog.checkboxLabel }"
        role="dialog"
        aria-modal="true"
        :aria-label="dialog.title || $t('shell.confirmOperation')"
        @keydown.esc.stop="resolve(false)"
      >
        <div class="confirm-title">{{ dialog.title || $t('shell.confirmOperation') }}</div>
        <div class="confirm-message">{{ dialog.message }}</div>
        <div v-if="dialog.warningText" class="confirm-warning">{{ dialog.warningText }}</div>
        <button
          v-if="dialog.checkboxLabel"
          type="button"
          class="confirm-checkbox"
          role="checkbox"
          :aria-checked="dialog.checkboxChecked"
          @click="toggleCheckbox"
        >
          <FancyCheck :checked="dialog.checkboxChecked" />
          <span>{{ dialog.checkboxLabel }}</span>
        </button>
        <div
          class="confirm-actions"
          :class="{ 'confirm-actions--confirm-left': dialog.confirmOnLeft }"
        >
          <button
            type="button"
            class="confirm-button confirm-button--cancel"
            @click="resolve(false)"
          >
            {{ dialog.cancelText || $t('common.cancel') }}
          </button>
          <button
            type="button"
            class="confirm-button confirm-button--confirm"
            :class="
              dialog.confirmVariant === 'danger'
                ? 'confirm-button--danger'
                : 'confirm-button--primary'
            "
            @click="resolve(true)"
          >
            {{ dialog.confirmText || $t('common.confirm') }}
          </button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';
import FancyCheck from '@/components/common/FancyCheck.vue';

const uiStore = useUiStore();
const { confirmDialog: dialog } = storeToRefs(uiStore);
const resolve = (choice: boolean) => uiStore.resolveConfirm(choice);
const toggleCheckbox = () => {
  if (!dialog.value) return;
  dialog.value.checkboxChecked = !dialog.value.checkboxChecked;
  dialog.value.onCheckboxChange?.(dialog.value.checkboxChecked);
};
</script>

<style scoped>
.confirm-checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  margin: 12px 0 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  cursor: pointer;
}
.confirm-checkbox:focus-visible {
  outline: 2px solid var(--border-strong);
  outline-offset: 3px;
}
</style>

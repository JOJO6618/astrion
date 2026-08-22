<template>
  <transition name="confirm-dialog-fade">
    <div
      class="confirm-overlay"
      v-if="dialog?.visible"
      @click.self="dialog?.closeOnBackdrop !== false && resolve(false)"
    >
      <div class="confirm-modal">
        <div class="confirm-title">{{ dialog.title || '确认操作' }}</div>
        <div class="confirm-message">{{ dialog.message }}</div>
        <div v-if="dialog.warningText" class="confirm-warning">{{ dialog.warningText }}</div>
        <div
          class="confirm-actions"
          :class="{ 'confirm-actions--confirm-left': dialog.confirmOnLeft }"
        >
          <button
            type="button"
            class="confirm-button confirm-button--cancel"
            @click="resolve(false)"
          >
            {{ dialog.cancelText || '取消' }}
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
            {{ dialog.confirmText || '确认' }}
          </button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';

const uiStore = useUiStore();
const { confirmDialog: dialog } = storeToRefs(uiStore);
const resolve = (choice: boolean) => uiStore.resolveConfirm(choice);
</script>

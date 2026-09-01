<template>
  <div class="toast-stack" :class="{ 'toast-stack--empty': !toasts.length }">
    <transition-group name="quota-toast-fade" tag="div">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        :class="['app-toast', toast.type ? `app-toast--${toast.type}` : '']"
      >
        <div class="app-toast-body">
          <div v-if="toast.title" class="app-toast-title">{{ toast.title }}</div>
          <div class="app-toast-message">{{ toast.message }}</div>
        </div>
        <CloseButton
          v-if="toast.closable !== false"
          size="sm"
          :label="$t('shell.closeNotification')"
          @click="dismiss(toast.id)"
        />
      </div>
    </transition-group>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';
import CloseButton from '@/components/common/CloseButton.vue';

const uiStore = useUiStore();
const { toastQueue: toasts } = storeToRefs(uiStore);
const dismiss = (id: number) => uiStore.dismissToast(id);
</script>

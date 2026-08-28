<template>
  <transition name="quota-toast-fade">
    <div class="quota-toast" v-if="quotaToast">
      <span class="quota-toast-label">{{ quotaToast.message }}</span>
      <button type="button" class="toast-close" :aria-label="$t('shell.closeNotification')" @click="dismiss">×</button>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';

const uiStore = useUiStore();
const { quotaToast } = storeToRefs(uiStore);
const dismiss = () => uiStore.dismissQuotaToast();
</script>

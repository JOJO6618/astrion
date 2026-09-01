<template>
  <transition name="quota-toast-fade">
    <div class="quota-toast" v-if="quotaToast">
      <span class="quota-toast-label">{{ quotaToast.message }}</span>
      <CloseButton size="sm" :label="$t('shell.closeNotification')" @click="dismiss" />
    </div>
  </transition>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';
import CloseButton from '@/components/common/CloseButton.vue';

const uiStore = useUiStore();
const { quotaToast } = storeToRefs(uiStore);
const dismiss = () => uiStore.dismissQuotaToast();
</script>

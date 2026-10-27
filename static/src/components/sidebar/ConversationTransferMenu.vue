<template>
  <button type="button" @click="$emit('export')">
    <span class="icon icon-sm" :style="iconStyle?.('save')" aria-hidden="true"></span>
    <span>{{ $t('sidebar.exportConversation') }}</span>
  </button>
  <button v-if="!chatMode" type="button" @click="$emit('migrate')">
    <span class="icon icon-sm" :style="iconStyle?.('folder')" aria-hidden="true"></span>
    <span>{{ $t('sidebar.migrateConversation') }}</span>
  </button>
</template>
<script setup lang="ts">
defineProps<{ chatMode?: boolean; iconStyle?: (key: string) => Record<string, string> }>();
defineEmits<{ export: []; migrate: [] }>();
</script>

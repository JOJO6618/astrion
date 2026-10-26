<template>
  <div class="assistant-token-speed">
    <slot />
    <span v-if="tokenSpeed != null" class="assistant-token-speed-value">
      {{ $t('chat.tokenSpeedValue', { value: tokenSpeed }) }}
    </span>
    <slot name="after-speed" />
  </div>
</template>

<script setup lang="ts">
defineProps<{ tokenSpeed?: string | null }>();
</script>

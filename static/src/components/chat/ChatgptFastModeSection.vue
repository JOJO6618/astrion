<script setup lang="ts">
import PillSwitch from '@/components/settings/tabs/providers/PillSwitch.vue';

defineProps<{
  enabled: boolean;
  disabled: boolean;
}>();
const emit = defineEmits<{ (e: 'toggle'): void }>();
</script>

<template>
  <section class="chatgpt-fast-mode">
    <div class="chatgpt-fast-mode__title">{{ $t('appCore.chatgptFastMode') }}</div>
    <div class="chatgpt-fast-mode__row">
      <PillSwitch
        :on="enabled"
        :disabled="disabled"
        :label="$t('appCore.chatgptFastMode')"
        @toggle="emit('toggle')"
      />
      <span class="chatgpt-fast-mode__description">{{ $t('appCore.chatgptFastModeDesc') }}</span>
    </div>
  </section>
</template>

<style scoped>
.chatgpt-fast-mode {
  margin-top: 8px;
  padding: 10px 4px 2px;
  border-top: 1px solid var(--border-default);
}

.chatgpt-fast-mode__title {
  height: 20px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 20px;
  white-space: nowrap;
}

.chatgpt-fast-mode__row {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
}

.chatgpt-fast-mode__description {
  min-width: 0;
  max-height: 32px;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 16px;
}

@media (max-width: 360px) {
  .chatgpt-fast-mode__row {
    height: 72px;
  }

  .chatgpt-fast-mode__description {
    max-height: 64px;
  }
}
</style>

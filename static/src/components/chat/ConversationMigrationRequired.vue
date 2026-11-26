<template>
  <section class="migration-required" aria-labelledby="migration-required-title">
    <h2 id="migration-required-title">{{ $t('appMessages.storageMigration.incompatible') }}</h2>
    <p>{{ $t('appMessages.storageMigration.incompatibleDescription') }}</p>
    <UiDialogButton variant="primary" @click="migration.show">
      {{ $t('appMessages.storageMigration.open') }}
    </UiDialogButton>
  </section>
</template>

<script setup lang="ts">
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import { useConversationStorageMigrationStore } from '@/stores/conversationStorageMigration';

const migration = useConversationStorageMigrationStore();
</script>

<style scoped>
.migration-required {
  flex: 1;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 12px;
  min-height: 0;
  padding: 20px;
  overflow: auto;
  text-align: center;
}

.migration-required h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
  line-height: 28px;
}

.migration-required p {
  max-width: 36rem;
  margin: 0;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 22px;
}
</style>

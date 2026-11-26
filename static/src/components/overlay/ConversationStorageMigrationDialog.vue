<template>
  <UiDialog
    :open="migration.open"
    :title="$t('appMessages.storageMigration.title')"
    :busy="migration.busy"
    @close="migration.dismiss"
  >
    <p v-if="migration.running" class="ui-dialog-text" role="status">
      {{ $t('appMessages.storageMigration.running') }}
    </p>
    <p v-else-if="migration.loading" class="ui-dialog-text" role="status">
      {{ $t('common.loading') }}
    </p>
    <p v-else-if="migration.error !== null" class="ui-dialog-error" role="alert">
      {{ migration.error || $t('appMessages.storageMigration.requestFailed') }}
    </p>
    <template v-else-if="migration.status">
      <p v-if="migration.status.pending" class="ui-dialog-text">
        {{ $t('appMessages.storageMigration.pending', { count: migration.status.pending }) }}
      </p>
      <p
        v-if="migration.status.migrated || migration.status.failed"
        class="ui-dialog-text"
        role="status"
      >
        {{ $t('appMessages.storageMigration.result', migration.status) }}
      </p>
      <ul v-if="migration.status.errors.length" class="ui-dialog-error migration-errors">
        <li v-for="(error, index) in migration.status.errors" :key="index">{{ error }}</li>
      </ul>
      <p
        v-if="!migration.status.pending && !migration.status.migrated && !migration.status.failed"
        class="ui-dialog-text"
      >
        {{ $t('appMessages.storageMigration.upToDate') }}
      </p>
    </template>
    <p v-else class="ui-dialog-error" role="alert">
      {{ $t('appMessages.storageMigration.requestFailed') }}
    </p>
    <template #actions>
      <UiDialogButton :disabled="migration.busy" @click="migration.dismiss">
        {{ $t(canMigrate ? 'appMessages.storageMigration.later' : 'common.close') }}
      </UiDialogButton>
      <UiDialogButton
        v-if="canMigrate"
        variant="primary"
        :disabled="migration.busy"
        @click="migration.load('POST')"
      >
        {{ $t(migration.status?.failed ? 'common.retry' : 'appMessages.storageMigration.start') }}
      </UiDialogButton>
      <UiDialogButton
        v-else-if="migration.error !== null || (!migration.status && !migration.loading)"
        variant="primary"
        @click="migration.load()"
      >
        {{ $t('common.retry') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import {
  onConversationStorageMigrated,
  useConversationStorageMigrationStore
} from '@/stores/conversationStorageMigration';

const emit = defineEmits<{ migrated: [] }>();
const props = withDefaults(defineProps<{ checkOnMount?: boolean }>(), { checkOnMount: true });
const migration = useConversationStorageMigrationStore();
const canMigrate = computed(() => !!(migration.status?.pending || migration.status?.failed));
let unsubscribe: (() => void) | undefined;

function checkStartup() {
  if (props.checkOnMount && document.visibilityState === 'visible') void migration.checkStartup();
}

onMounted(() => {
  unsubscribe = onConversationStorageMigrated(() => emit('migrated'));
  document.addEventListener('visibilitychange', checkStartup);
  checkStartup();
});

onBeforeUnmount(() => {
  unsubscribe?.();
  document.removeEventListener('visibilitychange', checkStartup);
});
</script>

<style scoped>
.migration-errors {
  margin: 0;
  padding-inline-start: 20px;
  overflow-wrap: anywhere;
}
</style>

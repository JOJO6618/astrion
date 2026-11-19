<template>
  <input
    ref="fileInput"
    class="transfer-file-input"
    type="file"
    accept=".zip,application/zip"
    @change="selectedFile"
  />
  <UiDialog :open="visible" :title="heading" :busy="busy" @close="close">
    <p v-if="file" class="ui-dialog-text transfer-file-name">{{ file.name }}</p>
    <div v-if="mode === 'migrate'" class="ui-dialog-field">
      <span class="ui-dialog-label">{{
        isProject ? $t('sidebar.transferTargetProject') : $t('sidebar.transferTarget')
      }}</span>
      <UiSelect
        :model-value="target || ''"
        :options="targets"
        :label="isProject ? $t('sidebar.transferTargetProject') : $t('sidebar.transferTarget')"
        :placeholder="
          isProject ? $t('sidebar.transferTargetProject') : $t('sidebar.transferTarget')
        "
        :disabled="busy || !targets.length"
        @update:model-value="target = $event || null"
      />
    </div>
    <div v-if="mode === 'import'" class="ui-dialog-field">
      <span class="ui-dialog-label">{{ $t('sidebar.transferModelLabel') }}</span>
      <UiModelSelect
        v-model="selectedModel"
        :options="models.models"
        :label="$t('sidebar.transferModelLabel')"
        :placeholder="$t('sidebar.transferKeepModel')"
        :extra-options="[{ key: '', label: $t('sidebar.transferKeepModel') }]"
      />
    </div>
    <p v-if="error" class="ui-dialog-error transfer-error" role="alert">{{ error }}</p>
    <template #actions>
      <UiDialogButton :disabled="busy" @click="close">{{ $t('common.cancel') }}</UiDialogButton>
      <UiDialogButton
        variant="primary"
        :disabled="busy || (mode === 'migrate' && !target)"
        @click="submit"
      >
        {{ busy ? $t('sidebar.transferWorking') : heading }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useUiStore } from '@/stores/ui';
import { useModelStore } from '@/stores/model';
import { useConversationStore } from '@/stores/conversation';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import {
  exportConversationFile,
  transferRequest,
  type TransferDestination,
  type TransferScope
} from '@/utils/conversationTransfer';

const props = defineProps<{
  chatMode?: boolean;
  workspaceId?: string;
  workspaceKind?: 'workspace' | 'project';
}>();
const emit = defineEmits<{ completed: [value: { conversationId: string; workspaceId: string }] }>();
const { t } = useI18n();
const ui = useUiStore();
const models = useModelStore();
const conversations = useConversationStore();
const fileInput = ref<HTMLInputElement>();
const visible = ref(false);
const busy = ref(false);
const mode = ref<'import' | 'migrate'>('import');
const file = ref<File | null>(null);
const selectedModel = ref('');
const target = ref<string | null>(null);
const targets = ref<Array<{ value: string; label: string }>>([]);
const error = ref('');
const isProject = computed(() => props.workspaceKind === 'project');
const heading = computed(() =>
  mode.value === 'import'
    ? t('sidebar.importConversation')
    : isProject.value
      ? t('sidebar.migrateConversationProject')
      : t('sidebar.migrateConversation')
);
let alive = true;
let requestGeneration = 0;
let conversationId = '';
let sourceScope: TransferScope = {};
let pendingImportScope: TransferScope = {};
let operationId = '';

function begin(
  scope: TransferScope = { chatMode: props.chatMode, workspaceId: props.workspaceId }
) {
  operationId = crypto.randomUUID();
  sourceScope = { ...scope };
  error.value = '';
  target.value = null;
  selectedModel.value = '';
  visible.value = true;
}
function close() {
  if (busy.value) return;
  requestGeneration++;
  visible.value = false;
}
function pickImport(workspaceId?: string) {
  if (busy.value) return;
  pendingImportScope = { chatMode: props.chatMode, workspaceId: workspaceId ?? props.workspaceId };
  fileInput.value?.click();
}
function selectedFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const selected = input.files?.[0];
  input.value = '';
  if (!selected || busy.value) return;
  mode.value = 'import';
  file.value = selected;
  begin(pendingImportScope);
}
async function openMigration(id: string, workspaceId: string) {
  if (busy.value) return;
  mode.value = 'migrate';
  file.value = null;
  conversationId = id;
  begin();
  sourceScope = { workspaceId };
  const generation = ++requestGeneration;
  targets.value = [];
  try {
    const rows = await transferRequest<Array<{ id: string; label: string }>>(
      '/api/conversation-transfer/workspaces',
      sourceScope
    );
    if (!alive || generation !== requestGeneration) return;
    targets.value = rows
      .filter((row) => row.id !== workspaceId)
      .map((row) => ({ value: row.id, label: row.label }));
    if (!targets.value.length)
      error.value = isProject.value
        ? t('sidebar.transferNoTargetProject')
        : t('sidebar.transferNoTarget');
  } catch (cause) {
    if (alive && generation === requestGeneration) error.value = (cause as Error).message;
  }
}
async function startExport(id: string, workspaceId: string) {
  if (busy.value) return;
  busy.value = true;
  const toast = ui.pushToast({ message: t('sidebar.transferExporting'), duration: null });
  try {
    const outcome = await exportConversationFile(id, { chatMode: props.chatMode, workspaceId });
    ui.dismissToast(toast);
    if (alive && outcome.status !== 'cancelled')
      ui.pushToast({ message: t('sidebar.transferExported'), type: 'success' });
  } catch (cause) {
    ui.dismissToast(toast);
    if (alive) ui.pushToast({ message: (cause as Error).message, type: 'error' });
  } finally {
    if (alive) busy.value = false;
  }
}
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  const generation = ++requestGeneration;
  try {
    let destination: TransferDestination;
    if (mode.value === 'import') {
      if (!file.value) return;
      const body = new FormData();
      body.append('file', file.value);
      body.append('operation_id', operationId);
      if (selectedModel.value) body.append('model_key', selectedModel.value);
      destination = await transferRequest('/api/conversations/import', sourceScope, {
        method: 'POST',
        body
      });
    } else {
      destination = await transferRequest(
        `/api/conversations/${encodeURIComponent(conversationId)}/migrate`,
        sourceScope,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_workspace_id: target.value, operation_id: operationId })
        }
      );
    }
    if (!alive || generation !== requestGeneration) return;
    if (!props.chatMode) {
      for (const id of [sourceScope.workspaceId, destination.workspace_id]) {
        if (!id) continue;
        conversations.invalidateWorkspaceCache(id);
        // The transfer is already committed; a list refresh must not report it
        // as failed or prevent navigation to the destination.
        await conversations
          .loadWorkspaceConversations(id, { reset: true, refresh: true })
          .catch(() => {});
      }
    }
    if (!alive || generation !== requestGeneration) return;
    visible.value = false;
    emit('completed', {
      conversationId: destination.conversation_id,
      workspaceId: destination.workspace_id || ''
    });
  } catch (cause) {
    if (alive && generation === requestGeneration) error.value = (cause as Error).message;
  } finally {
    if (alive && generation === requestGeneration) busy.value = false;
  }
}
onBeforeUnmount(() => {
  alive = false;
  requestGeneration++;
});
defineExpose({ pickImport, openMigration, startExport });
</script>
<style scoped lang="scss">
.transfer-file-input {
  display: none;
}

.transfer-file-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.transfer-error {
  max-height: 120px;
  overflow-y: auto;
  overflow-wrap: anywhere;
}
</style>

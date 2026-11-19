<script setup lang="ts">
/**
 * 路径授权（DESIGN.md §6.7：专属交互按语义保留）：生效范围、权限、路径列表。
 * 范围与权限用弹窗规格 UiSelect，选项说明放在次文字里。
 */
import { computed } from 'vue';
import { t } from '@/locales';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiSelect from '@/components/ui/UiSelect.vue';

type Mode = 'writable' | 'readable';
type Scope = 'workspace' | 'global';

const props = defineProps<{
  open: boolean;
  value: string;
  mode: Mode;
  scope: Scope;
  workspacePath?: string;
  saving?: boolean;
}>();
const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'save'): void;
  (e: 'update:value', v: string): void;
  (e: 'update:mode', v: Mode): void;
  (e: 'update:scope', v: Scope): void;
}>();

const scopeOptions = computed(() => [
  {
    value: 'workspace',
    label: t('overlay.pathAuthScopeWorkspace'),
    description: props.workspacePath || undefined
  },
  {
    value: 'global',
    label: t('overlay.pathAuthScopeGlobal'),
    description: t('overlay.pathAuthGlobalHint')
  }
]);
const modeOptions = computed(() => [
  {
    value: 'writable',
    label: t('overlay.writableMode'),
    description: t('overlay.writableModeDesc')
  },
  {
    value: 'readable',
    label: t('overlay.readableMode'),
    description: t('overlay.readableModeDesc')
  }
]);
</script>

<template>
  <UiDialog
    :open="open"
    :title="$t('overlay.pathAuthTitle')"
    size="wide"
    :busy="saving"
    @close="emit('close')"
  >
    <div class="path-auth-grid">
      <div class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('overlay.pathAuthScopeLabel') }}</span>
        <UiSelect
          :model-value="scope"
          :options="scopeOptions"
          :label="$t('overlay.pathAuthScopeLabel')"
          :disabled="saving"
          @update:model-value="emit('update:scope', $event as Scope)"
        />
      </div>
      <div class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('overlay.pathAuthModeLabel') }}</span>
        <UiSelect
          :model-value="mode"
          :options="modeOptions"
          :label="$t('overlay.pathAuthModeLabel')"
          :disabled="saving"
          @update:model-value="emit('update:mode', $event as Mode)"
        />
      </div>
    </div>
    <div class="ui-dialog-field">
      <label class="ui-dialog-label" for="path-authorization-input">{{
        $t('overlay.pathAuthPathsLabel')
      }}</label>
      <textarea
        id="path-authorization-input"
        class="ui-dialog-textarea is-mono"
        :value="value"
        :placeholder="
          mode === 'writable'
            ? $t('overlay.writablePlaceholder')
            : $t('overlay.readablePlaceholder')
        "
        :disabled="saving"
        spellcheck="false"
        @input="emit('update:value', ($event.target as HTMLTextAreaElement).value)"
      />
      <p class="ui-dialog-note">
        {{
          scope === 'workspace'
            ? $t('overlay.pathAuthWorkspaceHint')
            : $t('overlay.pathAuthGlobalHint')
        }}
      </p>
    </div>
    <template #actions>
      <UiDialogButton :disabled="saving" @click="emit('close')">
        {{ $t('common.cancel') }}
      </UiDialogButton>
      <UiDialogButton variant="primary" :disabled="saving" @click="emit('save')">
        {{ $t('common.save') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<style scoped>
.path-auth-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
}

@media (max-width: 520px) {
  .path-auth-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

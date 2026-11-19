<template>
  <UiDialog
    :open="open"
    :title="shown.title || $t('shell.confirmOperation')"
    :close-on-scrim="shown.closeOnBackdrop !== false"
    @close="resolve(false)"
  >
    <p class="ui-dialog-text">{{ shown.message }}</p>
    <p v-if="shown.warningText" class="confirm-warning">
      <UiIcon name="triangle-alert" />
      <span>{{ shown.warningText }}</span>
    </p>
    <label v-if="shown.checkboxLabel" class="ui-dialog-switch-row">
      <span>{{ shown.checkboxLabel }}</span>
      <input type="checkbox" :checked="checked" @change="toggleCheckbox" />
      <UiSwitch decorative :checked="checked" />
    </label>
    <template #actions>
      <UiDialogButton @click="resolve(false)">
        {{ shown.cancelText || $t('common.cancel') }}
      </UiDialogButton>
      <UiDialogButton
        :variant="shown.confirmVariant === 'danger' ? 'danger' : 'primary'"
        @click="resolve(true)"
      >
        {{ shown.confirmText || $t('common.confirm') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiIcon from '@/components/ui/UiIcon.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';

const uiStore = useUiStore();
const { confirmDialog: dialog } = storeToRefs(uiStore);

/** resolveConfirm 会立即清空 store；保留最后一次内容，退出动画期间文字不消失 */
const shown = ref<NonNullable<typeof dialog.value> | Record<string, never>>({});
watch(
  dialog,
  (value) => {
    if (value) shown.value = value;
  },
  { immediate: true }
);

const open = computed(() => Boolean(dialog.value?.visible));
const checked = computed(() => Boolean(dialog.value?.checkboxChecked));

const resolve = (choice: boolean) => uiStore.resolveConfirm(choice);
const toggleCheckbox = () => {
  if (!dialog.value) return;
  dialog.value.checkboxChecked = !dialog.value.checkboxChecked;
  dialog.value.onCheckboxChange?.(dialog.value.checkboxChecked);
};
</script>

<style scoped>
/* 警告只用图标颜色区分，不铺底、不加框 */
.confirm-warning {
  margin: 0;
  display: flex;
  gap: 8px;
  color: var(--text-primary);
  font-size: 14px;
  line-height: 22px;
}

.confirm-warning .ui-icon {
  width: 16px;
  height: 16px;
  margin-top: 3px;
  flex: none;
  color: var(--state-warning);
}
</style>

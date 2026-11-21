<script setup lang="ts">
import { ref, watch } from 'vue';
import { useCommandBlockingStore } from '@/stores/commandBlocking';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';

defineOptions({ name: 'CommandBlockingDialog' });

// 状态全部由 store 驱动：窗口打开/草稿/加载/保存/错误，App.vue 统一挂载，
// 设置路由（settingsRoute）与聊天界面共用同一实例。
const store = useCommandBlockingStore();

/** 本次打开后追加的推荐规则条数（只用于框下提示） */
const appended = ref(0);
watch(
  () => store.dialogOpen,
  (open) => {
    if (open) appended.value = 0;
  }
);

function applyRecommended() {
  const count = store.pendingRecommended.length;
  if (!count) return;
  store.applyRecommended();
  appended.value = count;
}
</script>

<template>
  <UiDialog
    :open="store.dialogOpen"
    :title="$t('commandBlocking.title')"
    :busy="store.saving"
    @close="store.closeDialog()"
  >
    <p class="ui-dialog-text">
      {{
        $t('commandBlocking.summary', {
          status: store.enabled ? $t('commandBlocking.statusOn') : $t('commandBlocking.statusOff')
        })
      }}
    </p>

    <p v-if="store.loading" class="ui-dialog-text" role="status">
      {{ $t('commandBlocking.loading') }}
    </p>
    <div v-else class="ui-dialog-field">
      <div class="ui-dialog-label">
        <label for="command-blocking-rules">{{ $t('commandBlocking.rulesLabel') }}</label>
        <button
          type="button"
          class="ui-dialog-link"
          :disabled="store.loadFailed || store.saving || !store.pendingRecommended.length"
          @click="applyRecommended"
        >
          {{ $t('commandBlocking.useRecommended') }}
        </button>
      </div>
      <textarea
        id="command-blocking-rules"
        v-model="store.draft"
        class="ui-dialog-textarea is-mono command-blocking-rules"
        :placeholder="$t('commandBlocking.rulesPlaceholder')"
        :disabled="store.loadFailed || store.saving"
        spellcheck="false"
      ></textarea>
      <p v-if="appended" class="ui-dialog-note">
        {{ $t('commandBlocking.recommendedHint', { n: appended }) }}
      </p>
    </div>

    <p v-if="store.loadFailed" class="ui-dialog-error" role="alert">
      {{ $t('commandBlocking.loadFailedHint') }}
      <template v-if="store.error">{{ store.error }}</template>
    </p>
    <p v-else-if="store.error" class="ui-dialog-error" role="alert">
      {{ $t('commandBlocking.saveFailed') }} {{ store.error }}
    </p>

    <template #actions>
      <UiDialogButton :disabled="store.saving" @click="store.closeDialog()">
        {{ $t('common.cancel') }}
      </UiDialogButton>
      <UiDialogButton
        variant="primary"
        :disabled="store.loading || store.saving || store.loadFailed"
        @click="store.saveRules()"
      >
        {{ $t('common.save') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<style scoped>
.command-blocking-rules {
  min-height: 176px;
}
</style>

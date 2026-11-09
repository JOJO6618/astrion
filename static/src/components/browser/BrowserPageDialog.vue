<template>
  <div
    v-if="dialog"
    class="nb-page-dialog-scrim nb-permission-scrim"
    @keydown.esc.stop.prevent="answer(false)"
  >
    <form
      class="nb-page-dialog"
      role="dialog"
      aria-modal="true"
      :aria-label="$t('browser.pageDialog')"
      @submit.prevent="answer(true)"
    >
      <strong>{{ $t('browser.pageDialog') }}</strong>
      <p>{{ dialog.message }}</p>
      <input
        v-if="dialog.kind === 'prompt'"
        v-model="prompt"
        :aria-label="$t('browser.pageDialogReply')"
        autocomplete="off"
        maxlength="20000"
      />
      <div class="nb-page-dialog-actions">
        <button v-if="dialog.kind !== 'alert'" type="button" @click="answer(false)">
          {{ $t('common.cancel') }}
        </button>
        <button type="submit">{{ $t('common.confirm') }}</button>
      </div>
    </form>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
const store = useNativeBrowserStore();
const dialog = computed(() => store.snapshot?.dialog);
const prompt = ref('');
watch(
  () => dialog.value?.id,
  () => {
    prompt.value = dialog.value?.defaultPrompt || '';
  },
  { immediate: true }
);
function answer(accept: boolean) {
  if (!dialog.value) return;
  void store.command(
    'dialog',
    { id: dialog.value.id, accept, prompt: prompt.value },
    dialog.value.tabId
  );
}
</script>
<style scoped>
.nb-page-dialog-scrim {
  position: absolute;
  z-index: 14;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 16px;
  background: var(--overlay-scrim);
}
.nb-page-dialog {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 360px;
  max-width: 100%;
  height: 250px;
  max-height: 100%;
  padding: 18px;
  background: var(--surface-base);
  border: 1px solid var(--border-default);
  border-radius: 6px;
}
.nb-page-dialog strong {
  height: 20px;
  flex: none;
}
.nb-page-dialog p {
  margin: 0;
  flex: 1;
  min-height: 0;
  overflow: auto;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  overscroll-behavior: contain;
}
.nb-page-dialog input {
  height: 34px;
  flex: none;
  border: 1px solid var(--border-default);
  padding: 0 8px;
  background: var(--surface-base);
}
.nb-page-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  height: 34px;
  flex: none;
}
.nb-page-dialog-actions button {
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--border-default);
  border-radius: 4px;
  background: var(--surface-base);
}
.nb-page-dialog-actions button:hover {
  background: var(--hover-bg);
}
.nb-page-dialog-actions button:focus-visible,
.nb-page-dialog input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>

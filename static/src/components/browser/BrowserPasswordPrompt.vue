<template>
  <section
    v-if="!store.section && (prompt || fill)"
    class="nb-credential-floating nb-credentials"
    :class="{ 'nb-below-find': store.findOpen }"
    role="dialog"
    :aria-label="
      $t(
        prompt
          ? prompt.update
            ? 'browser.updatePasswordPrompt'
            : 'browser.savePasswordPrompt'
          : 'browser.chooseAccount'
      )
    "
    @keydown.esc.stop.prevent="act('dismiss')"
  >
    <header class="nb-credential-prompt-header">
      <strong>{{
        $t(
          prompt
            ? prompt.update
              ? 'browser.updatePasswordPrompt'
              : 'browser.savePasswordPrompt'
            : 'browser.chooseAccount'
        )
      }}</strong
      ><BrowserButton
        name="x"
        :label="$t('browser.dismiss')"
        :disabled="busy"
        @click="act('dismiss')"
      />
    </header>
    <template v-if="prompt">
      <p class="nb-prompt-site">{{ prompt.origin }}</p>
      <p class="nb-prompt-user">{{ prompt.username || $t('browser.noUsername') }}</p>
      <div class="nb-credential-actions">
        <button class="nb-text-button" :disabled="busy" @click="act('dismiss')">
          {{ $t('browser.notNow') }}</button
        ><button class="nb-text-button primary" :disabled="busy" @click="act('accept', prompt.id)">
          {{ $t(prompt.update ? 'browser.updatePassword' : 'browser.savePassword') }}
        </button>
      </div>
    </template>
    <template v-else-if="fill">
      <p class="nb-prompt-site">{{ fill.origin }}</p>
      <div class="nb-account-options">
        <button
          v-for="account in fill.accounts"
          :key="account.id"
          :disabled="busy"
          @click="act('fill', account.id)"
        >
          {{ account.username || $t('browser.noUsername') }}
        </button>
      </div>
    </template>
    <p v-if="error" class="nb-credential-error" role="alert">{{ error }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import BrowserButton from './BrowserButton.vue';
import { credentialError, useCredentialRequest } from './useCredentialRequest';
const store = useNativeBrowserStore(),
  { request, valid } = useCredentialRequest();
const prompt = computed(() => store.snapshot?.credentials?.prompt);
const fill = computed(() => store.snapshot?.credentials?.fill);
const busy = ref(false),
  error = ref('');
let generation = 0;
watch([() => store.snapshot?.lease, () => prompt.value?.id, () => fill.value?.id], () => {
  generation++;
  error.value = '';
  busy.value = false;
});
async function act(action: string, id?: string) {
  if (busy.value) return;
  const ticket = generation;
  busy.value = true;
  error.value = '';
  try {
    await request({ action, id, tabId: fill.value?.tabId, fillId: fill.value?.id });
  } catch (failure) {
    if (valid() && ticket === generation) error.value = credentialError(failure);
  } finally {
    if (valid() && ticket === generation) busy.value = false;
  }
}
</script>
<style src="./browser-credentials.scss"></style>

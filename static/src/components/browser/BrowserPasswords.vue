<template>
  <div class="nb-credentials">
    <p v-if="error" class="nb-credential-error" role="alert">{{ error }}</p>
    <div class="nb-credential-settings">
      <button
        class="nb-setting"
        role="switch"
        :aria-checked="settings.save"
        :disabled="busy"
        @click="configure('save')"
      >
        <span>{{ $t('browser.offerSave') }}</span
        ><FancyCheck :checked="settings.save" :size="18" />
      </button>
      <button
        class="nb-setting"
        role="switch"
        :aria-checked="settings.autofill"
        :disabled="busy"
        @click="configure('autofill')"
      >
        <span>{{ $t('browser.autoFill') }}</span
        ><FancyCheck :checked="settings.autofill" :size="18" />
      </button>
    </div>
    <div class="nb-credential-toolbar">
      <label class="nb-search"
        ><BrowserIcon name="search" /><input
          v-model="query"
          :placeholder="$t('browser.searchPasswords')"
          :aria-label="$t('browser.searchPasswords')"
      /></label>
      <button class="nb-text-button" :disabled="busy" @click="edit()">
        {{ $t('browser.addPassword') }}
      </button>
    </div>
    <BrowserPasswordEditor
      v-if="editor && !editor.id"
      :value="editor"
      :busy="busy"
      :revealed="revealed"
      :copied="copied"
      @change="editor = $event"
      @save="save"
      @close="closeEditor"
      @reveal="reveal"
      @copy="copy"
    />
    <p v-if="busy && !records.length" class="nb-description">
      {{ $t('browser.readingPasswords') }}
    </p>
    <p v-else-if="!filtered.length" class="nb-description">
      {{ $t(query ? 'browser.noMatches' : 'browser.noPasswords') }}
    </p>
    <template v-for="row in filtered" :key="row.id">
      <div class="nb-password-row" :class="{ 'nb-password-expanded': editor?.id === row.id }">
        <BrowserSiteIcon :url="row.origin" />
        <button
          class="nb-row-main"
          :disabled="busy"
          :aria-expanded="editor?.id === row.id"
          @click="edit(row)"
        >
          <span>{{ row.origin }}</span
          ><small>{{ row.username || $t('browser.noUsername') }}</small>
        </button>
        <template v-if="deleting === row.id">
          <button class="nb-text-button danger" :disabled="busy" @click="remove(row.id)">
            {{ $t('browser.confirmDelete') }}
          </button>
          <button class="nb-text-button" :disabled="busy" @click="deleting = ''">
            {{ $t('browser.cancel') }}
          </button>
        </template>
        <template v-else>
          <button class="nb-text-button" :disabled="busy" @click="edit(row)">
            {{ $t('browser.editPassword') }}
          </button>
          <BrowserButton
            name="x"
            :label="$t('browser.deletePassword')"
            :disabled="busy"
            @click="deleting = row.id"
          />
        </template>
      </div>
      <BrowserPasswordEditor
        v-if="editor?.id === row.id"
        :value="editor"
        :busy="busy"
        :revealed="revealed"
        :copied="copied"
        @change="editor = $event"
        @save="save"
        @close="closeEditor"
        @reveal="reveal"
        @copy="copy"
      />
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import FancyCheck from '@/components/common/FancyCheck.vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserButton from './BrowserButton.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import BrowserPasswordEditor from './BrowserPasswordEditor.vue';
import { credentialError, useCredentialRequest } from './useCredentialRequest';
import type { CredentialResponse, PasswordRecord, PasswordSettings } from './credentialTypes';
const store = useNativeBrowserStore(),
  { request, valid } = useCredentialRequest();
const records = ref<PasswordRecord[]>([]),
  settings = ref<PasswordSettings>({ save: true, autofill: true });
const query = ref(''),
  error = ref(''),
  busy = ref(false),
  configuring = ref(false),
  deleting = ref(''),
  revealed = ref(''),
  copied = ref(false);
const editor = ref<{ id: string; origin: string; username: string; password: string } | null>(null);
let hideTimer: ReturnType<typeof setTimeout> | undefined;
const filtered = computed(() =>
  records.value.filter((row) =>
    `${row.origin} ${row.username}`.toLowerCase().includes(query.value.toLowerCase())
  )
);
function apply(result: CredentialResponse) {
  if (result.records) records.value = result.records;
  if (result.settings) settings.value = result.settings;
}
function hide() {
  clearTimeout(hideTimer);
  revealed.value = '';
  copied.value = false;
}
function closeEditor() {
  hide();
  if (editor.value) editor.value.password = '';
  editor.value = null;
}
function edit(row?: PasswordRecord) {
  if (row && editor.value?.id === row.id) {
    closeEditor();
    return;
  }
  closeEditor();
  deleting.value = '';
  let origin = row?.origin || '';
  if (!row && store.active) {
    try {
      const url = new URL(store.active.url);
      if (/^https?:$/.test(url.protocol)) origin = url.origin;
    } catch {
      /* Empty editor accepts a new site. */
    }
  }
  editor.value = { id: row?.id || '', origin, username: row?.username || '', password: '' };
}
async function run(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    await work();
  } catch (failure) {
    if (valid()) error.value = credentialError(failure);
  } finally {
    if (valid()) busy.value = false;
  }
}
async function save() {
  const value = editor.value;
  if (!value) return;
  await run(async () => {
    apply(
      await request({
        action: 'save',
        id: value.id || undefined,
        value: { origin: value.origin, username: value.username, password: value.password }
      })
    );
    closeEditor();
  });
}
async function configure(key: keyof PasswordSettings) {
  if (busy.value || configuring.value) return;
  const lease = store.snapshot?.lease,
    previous = { ...settings.value };
  const next = { ...previous, [key]: !previous[key] };
  configuring.value = true;
  settings.value = next;
  error.value = '';
  try {
    const result = await request({ action: 'settings', value: next });
    if (result.settings) settings.value = result.settings;
  } catch (failure) {
    if (valid() && store.snapshot?.lease === lease) {
      settings.value = previous;
      error.value = credentialError(failure);
    }
  } finally {
    if (valid() && store.snapshot?.lease === lease) configuring.value = false;
  }
}
async function remove(id: string) {
  await run(async () => {
    apply(await request({ action: 'remove', id }));
    deleting.value = '';
    if (editor.value?.id === id) closeEditor();
  });
}
async function reveal() {
  if (revealed.value) {
    hide();
    return;
  }
  const id = editor.value?.id;
  if (!id) return;
  await run(async () => {
    const result = await request({ action: 'reveal', id });
    if (editor.value?.id !== id) return;
    revealed.value = result.password || '';
    hideTimer = setTimeout(hide, 30000);
  });
}
async function copy() {
  const id = editor.value?.id;
  if (id)
    await run(async () => {
      await request({ action: 'copy', id });
      copied.value = true;
    });
}
watch(
  () => store.snapshot?.lease,
  () => {
    closeEditor();
    records.value = [];
    error.value = '';
    void run(async () => apply(await request({ action: 'list' })));
  },
  { immediate: true }
);
onBeforeUnmount(closeEditor);
</script>
<style src="./browser-credentials.scss"></style>

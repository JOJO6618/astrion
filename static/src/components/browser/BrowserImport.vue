<template>
  <div class="nb-credentials">
    <p v-if="!fileMode" class="nb-credential-note">{{ $t('browser.chromeImportHint') }}</p>
    <p v-if="error" class="nb-credential-error" role="alert">{{ error }}</p>
    <div v-if="fileSupported && !preview && !report" class="nb-choice-options">
      <button role="radio" :aria-checked="fileMode" :disabled="busy" @click="fileMode = true">
        <FancyCheck :checked="fileMode" :size="16" />{{ $t('browser.importExportedFile') }}
      </button>
      <button role="radio" :aria-checked="!fileMode" :disabled="busy" @click="fileMode = false">
        <FancyCheck :checked="!fileMode" :size="16" />{{ $t('browser.importChromeDatabase') }}
      </button>
    </div>
    <BrowserImportFile
      v-if="fileMode && !preview && !report"
      :disabled="busy"
      @preview="readFile"
      @error="error = $event"
    />
    <template v-if="!fileMode && !preview && !report">
      <label class="nb-credential-label">{{ $t('browser.chromeProfile') }}</label>
      <button
        class="nb-source-button"
        :disabled="busy || !sources.length"
        :aria-expanded="sourceOpen"
        @click="sourceOpen = !sourceOpen"
      >
        <span>{{ selectedSource?.name || $t('browser.noChromeProfiles') }}</span
        ><small>{{ selectedSource?.id }}</small
        ><BrowserIcon name="chevronDown" />
      </button>
      <div
        v-if="sourceOpen"
        class="nb-source-options"
        role="listbox"
        :aria-label="$t('browser.chromeProfile')"
      >
        <button
          v-for="item in sources"
          :key="item.id"
          role="option"
          :aria-selected="source === item.id"
          @click="
            source = item.id;
            sourceOpen = false;
          "
        >
          <span
            >{{ item.name }}<small>{{ item.id }}</small></span
          ><FancyCheck :checked="source === item.id" :size="18" />
        </button>
      </div>
      <button
        v-for="type in kinds"
        :key="type"
        class="nb-setting"
        role="checkbox"
        :aria-checked="selection[type]"
        :disabled="busy"
        @click="selection[type] = !selection[type]"
      >
        <span>{{ $t(labels[type]) }}</span
        ><FancyCheck :checked="selection[type]" :size="18" />
      </button>
    </template>
    <p v-if="busy" class="nb-credential-note" role="status">{{ $t(stageLabel) }}</p>
    <template v-if="preview || report">
      <table class="nb-import-stats">
        <thead>
          <tr>
            <th>{{ $t('browser.importContent') }}</th>
            <th>{{ $t(report ? 'browser.importedCount' : 'browser.readyCount') }}</th>
            <th>{{ $t('browser.skippedCount') }}</th>
            <th>{{ $t('browser.failedCount') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="type in selectedKinds" :key="type">
            <th>{{ $t(labels[type]) }}</th>
            <td>{{ stats?.[type]?.imported ?? stats?.[type]?.ready ?? 0 }}</td>
            <td>{{ stats?.[type]?.skipped || 0 }}</td>
            <td>{{ stats?.[type]?.failed || 0 }}</td>
          </tr>
        </tbody>
      </table>
      <div v-for="item in reasons" :key="item.key" class="nb-import-reason">
        <span
          >{{ $t(labels[item.type]) }} ·
          {{ $t(reasonKeys[item.reason] || 'browser.importInvalidRecord') }}</span
        ><span>{{ item.count }}</span>
      </div>
    </template>
    <template v-if="preview && !report">
      <template v-if="selectedKinds.includes('cookies') && preview.stats.cookies.ready">
        <label class="nb-credential-label">{{ $t('browser.cookieConflicts') }}</label>
        <div class="nb-choice-options">
          <button
            role="radio"
            :aria-checked="!replaceCookies"
            :disabled="busy"
            @click="replaceCookies = false"
          >
            <FancyCheck :checked="!replaceCookies" :size="16" />{{ $t('browser.keepExisting') }}
          </button>
          <button
            role="radio"
            :aria-checked="replaceCookies"
            :disabled="busy"
            @click="replaceCookies = true"
          >
            <FancyCheck :checked="replaceCookies" :size="16" />{{ $t('browser.replaceExisting') }}
          </button>
        </div>
      </template>
      <div v-for="conflict in preview.conflicts" :key="conflict.id" class="nb-import-conflict">
        <div class="nb-conflict-heading">
          <strong>{{ conflict.origin }}</strong
          ><span>{{ conflict.username || $t('browser.noUsername') }}</span>
        </div>
        <p class="nb-credential-note">{{ $t('browser.choosePasswordVersion') }}</p>
        <button
          v-if="conflict.hasExisting"
          class="nb-conflict-choice"
          role="radio"
          :aria-checked="choices[conflict.id] === 'keep'"
          :disabled="busy"
          @click="choices[conflict.id] = 'keep'"
        >
          <FancyCheck :checked="choices[conflict.id] === 'keep'" :size="16" /><span>{{
            $t('browser.keepExistingPassword')
          }}</span>
        </button>
        <button
          v-for="(candidate, index) in conflict.candidates"
          :key="candidate.id"
          class="nb-conflict-choice"
          role="radio"
          :aria-checked="choices[conflict.id] === candidate.id"
          :disabled="busy"
          @click="choices[conflict.id] = candidate.id"
        >
          <FancyCheck :checked="choices[conflict.id] === candidate.id" :size="16" /><span
            >{{ $t('browser.useChromePassword', { index: index + 1 })
            }}<small
              >{{ candidate.sourceURL
              }}{{ candidate.modified ? ' · ' + date(candidate.modified) : '' }}</small
            ></span
          >
        </button>
      </div>
    </template>
    <p v-if="report" class="nb-credential-note" role="status">{{ $t('browser.importFinished') }}</p>
    <div class="nb-credential-actions">
      <button v-if="busy && !committing" class="nb-text-button" @click="cancel">
        {{ $t('browser.cancel') }}
      </button>
      <button
        v-else-if="preview || report"
        class="nb-text-button"
        :disabled="busy"
        @click="restart"
      >
        {{ $t('browser.readAgain') }}
      </button>
      <button
        v-if="preview && !report"
        class="nb-text-button primary"
        :disabled="busy || !canImport"
        @click="commit"
      >
        {{ $t('browser.importAvailable') }}
      </button>
      <button
        v-else-if="!report && !fileMode"
        class="nb-text-button primary"
        :disabled="busy || !source || !selectedKinds.length"
        @click="read"
      >
        {{ $t('browser.previewImport') }}
      </button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import FancyCheck from '@/components/common/FancyCheck.vue';
import BrowserIcon from './BrowserIcon.vue';
import BrowserImportFile from './BrowserImportFile.vue';
import { browserBridge } from './types';
import { t } from '@/locales';
import { credentialError, useCredentialRequest } from './useCredentialRequest';
import type { ChromePreview, CredentialResponse, ImportKind } from './credentialTypes';
const store = useNativeBrowserStore(),
  { request, valid } = useCredentialRequest();
const sources = ref<{ id: string; name: string }[]>([]),
  source = ref(''),
  sourceOpen = ref(false);
const selection = ref({ passwords: true, cookies: true, history: true });
const fileSupported = browserBridge()?.credentialFileImport === true;
const fileMode = ref(fileSupported);
const fileKind = ref<ImportKind>('passwords');
const kinds: ImportKind[] = ['passwords', 'cookies', 'history'];
const labels = {
  passwords: 'browser.passwordRecords',
  cookies: 'browser.cookieRecords',
  history: 'browser.clearHistory'
};
const selectedKinds = computed(() =>
  fileMode.value ? [fileKind.value] : kinds.filter((type) => selection.value[type])
);
const selectedSource = computed(() => sources.value.find((item) => item.id === source.value));
const error = ref(''),
  busy = ref(false),
  committing = ref(false),
  stage = ref('reading'),
  replaceCookies = ref(false);
const preview = ref<ChromePreview>(),
  report = ref<CredentialResponse['report']>(),
  choices = ref<Record<string, string>>({});
const stats = computed(() => report.value?.stats || preview.value?.stats);
const canImport = computed(
  () =>
    !!preview.value &&
    selectedKinds.value.some((type) => (preview.value!.stats[type].ready || 0) > 0) &&
    preview.value.conflicts.every((group) => !!choices.value[group.id])
);
const stageLabel = computed(
  () =>
    ({
      passwords: 'browser.readingPasswords',
      cookies: 'browser.readingCookies',
      history: 'browser.readingHistory',
      keychain: 'browser.waitingKeychain',
      decrypt: 'browser.checkingImport',
      committing: 'browser.importingRecords'
    })[stage.value] || 'browser.readingChrome'
);
const reasonKeys: Record<string, string> = {
  database_busy: 'browser.importDatabaseBusy',
  database_unavailable: 'browser.importDatabaseUnavailable',
  missing_database: 'browser.importDatabaseMissing',
  keychain_timeout: 'browser.importKeychainTimeout',
  keychain_denied: 'browser.importKeychainDenied',
  windows_decryption_failed: 'browser.importWindowsDecryptFailed',
  unsupported_encryption: 'browser.importEncryptionUnsupported',
  unsupported_partition: 'browser.importPartitionUnsupported',
  conflicting_cookie_storage: 'browser.importCookieConflict',
  not_password: 'browser.importNotPassword',
  duplicate: 'browser.importDuplicate',
  expired: 'browser.importExpired',
  cookie_rejected: 'browser.importCookieRejected',
  cookie_store_unavailable: 'browser.importCookieStoreUnavailable',
  cookie_flush_failed: 'browser.importCookieFlushFailed',
  vault_unavailable: 'browser.importVaultUnavailable',
  history_write_failed: 'browser.importHistoryWriteFailed',
  history_non_web_url: 'browser.importHistoryNonWeb',
  history_invalid_url: 'browser.importHistoryInvalidUrl',
  history_invalid_time: 'browser.importHistoryInvalidTime',
  cookie_partition_write_failed: 'browser.importPartitionWriteFailed',
  cookie_verify_failed: 'browser.importCookieVerifyFailed'
};
const reasons = computed(() =>
  selectedKinds.value.flatMap((type) =>
    Object.entries(stats.value?.[type]?.reasons || {})
      .filter(([, count]) => count > 0)
      .map(([reason, count]) => ({ key: `${type}:${reason}`, type, reason, count }))
  )
);
const date = (at: number) => new Date(at).toLocaleString();
let sequence = 0,
  ownedLease = '',
  previewId = '';
async function cancel() {
  const ticket = ++sequence;
  const lease = ownedLease,
    id = previewId;
  if (valid()) {
    busy.value = true;
    preview.value = undefined;
    stage.value = 'reading';
  }
  previewId = '';
  if (lease) {
    try {
      await browserBridge()?.credentials({ lease, action: 'cancel-import', id });
    } catch {
      /* The retired owner cannot cancel another lease. */
    }
  }
  if (valid() && ticket === sequence) busy.value = false;
}
async function restart() {
  await cancel();
  if (!valid()) return;
  report.value = undefined;
  choices.value = {};
  error.value = '';
}
function importError(failure: unknown) {
  const code = credentialError(failure);
  const keys: Record<string, string> = {
    invalid_import_file: 'browser.invalidImportFile',
    import_file_too_large: 'browser.importFileTooLarge',
    import_file_too_many_records: 'browser.importFileTooManyRecords',
    browser_import_timed_out_partial: 'browser.importPartialTimeout'
  };
  return keys[code] ? t(keys[code]) : code;
}
async function readFile(value: { format: 'password-csv' | 'cookie-json'; content: string }) {
  if (busy.value) return;
  const ticket = ++sequence;
  const lease = ownedLease;
  fileKind.value = value.format === 'password-csv' ? 'passwords' : 'cookies';
  busy.value = true;
  error.value = '';
  stage.value = 'decrypt';
  try {
    const result = await request({ action: 'preview-file', value });
    if (ticket !== sequence || lease !== ownedLease) return;
    preview.value = result.preview;
    previewId = result.preview?.id || '';
    choices.value = {};
  } catch (failure) {
    if (ticket === sequence && valid()) error.value = importError(failure);
  } finally {
    value.content = '';
    if (ticket === sequence && valid()) busy.value = false;
  }
}
async function read() {
  if (busy.value) return;
  const ticket = ++sequence;
  busy.value = true;
  error.value = '';
  stage.value = 'reading';
  try {
    const result = await request({
      action: 'preview',
      value: { source: source.value, selected: { ...selection.value } }
    });
    if (ticket !== sequence) return;
    preview.value = result.preview;
    previewId = result.preview?.id || '';
    choices.value = {};
  } catch (failure) {
    if (ticket === sequence && valid()) error.value = importError(failure);
  } finally {
    if (ticket === sequence && valid()) busy.value = false;
  }
}
async function commit() {
  if (!preview.value || !canImport.value || busy.value) return;
  const ticket = ++sequence;
  busy.value = committing.value = true;
  stage.value = 'committing';
  error.value = '';
  try {
    const result = await request({
      action: 'import',
      value: {
        id: preview.value.id,
        choices: { ...choices.value },
        replaceCookies: replaceCookies.value
      }
    });
    if (ticket !== sequence) return;
    report.value = result.report;
    preview.value = undefined;
    previewId = '';
  } catch (failure) {
    if (ticket === sequence && valid()) error.value = importError(failure);
  } finally {
    if (ticket === sequence && valid()) {
      busy.value = committing.value = false;
    }
  }
}
const unsubscribe = browserBridge()?.onCredentialProgress((event) => {
  if (event.lease === ownedLease && busy.value) stage.value = event.stage;
});
watch(
  [() => store.snapshot?.lease, () => fileMode.value],
  async ([lease]) => {
    await cancel();
    if (!valid() || store.snapshot?.lease !== lease) return;
    ownedLease = lease || '';
    report.value = undefined;
    sources.value = [];
    source.value = '';
    error.value = '';
    if (!lease || fileMode.value) return;
    try {
      const result = await request({ action: 'sources' });
      sources.value = result.sources || [];
      source.value =
        sources.value.find((row) => row.id === 'Default')?.id || sources.value[0]?.id || '';
    } catch (failure) {
      if (valid() && ownedLease === lease) error.value = credentialError(failure);
    }
  },
  { immediate: true }
);
onBeforeUnmount(() => {
  unsubscribe?.();
  void cancel();
});
</script>
<style src="./browser-credentials.scss"></style>

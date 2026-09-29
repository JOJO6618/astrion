<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import ModelSelectDropdown from '@/components/personalization/ModelSelectDropdown.vue';
import { t } from '@/locales';

defineOptions({ name: 'GeneralTab' });

/**
 * 设置页「通用」分区：标题生成设置、应用更新与桌面运行数据目录。
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  isAppShell,
  appCurrentVersionText,
  appUpdateCheckedText,
  appHasUpdate,
  appUpdateStateText,
  appUpdateChecking,
  checkAppUpdate,
  downloadLatestApp,
  subAgentModels
} = ctx;

const isMacDesktop = computed(
  () => Boolean((window as any).__ASTRION_DESKTOP__) && (window as any).__ASTRION_PLATFORM__ === 'darwin'
);
const runDataInfo = ref<any>(null);
const runDataPath = ref('');
const runDataMode = ref<'migrate' | 'empty'>('migrate');
const runDataDialogOpen = ref(false);
const runDataDialogElement = ref<HTMLElement | null>(null);
const runDataBusy = ref(false);
const runDataError = ref('');
const restartPending = ref(false);
const restarting = ref(false);

const auxModelOptions = computed(() =>
  (subAgentModels.value || []).map((m: any) => ({ key: m.key, label: m.name || m.key }))
);
const defaultExtraOptions = computed(() => [
  { key: '', label: t('personalization.defaultModelOption') }
]);

const knownRunDataErrors = new Set([
  'environment_locked', 'path_required', 'same_directory', 'overlapping_directories',
  'target_not_directory', 'target_not_empty', 'verification_failed', 'restart_failed', 'request_failed'
]);

function setRunDataError(error: any) {
  const key = String(error?.message || error || 'unknown');
  runDataError.value = knownRunDataErrors.has(key) ? key : 'unknown';
}

async function requestRunData(path: string, options: RequestInit = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  let payload: any = null;
  try {
    payload = await response.json();
  } catch {
    /* 由下方统一显示接口错误 */
  }
  if (!response.ok || payload?.success === false) {
    throw new Error(payload?.error || payload?.code || 'request_failed');
  }
  return payload;
}

async function loadRunDataInfo() {
  try {
    const payload = await requestRunData('/api/desktop/rundata/info');
    runDataInfo.value = payload;
    runDataPath.value = payload.configured_path || payload.active_path || '';
  } catch (error: any) {
    setRunDataError(error);
  }
}

async function chooseRunDataFolder() {
  runDataError.value = '';
  try {
    const payload = await requestRunData('/api/desktop/rundata/choose', { method: 'POST' });
    if (!payload.canceled && payload.path) runDataPath.value = payload.path;
  } catch (error: any) {
    setRunDataError(error);
  }
}

async function openRunDataDialog() {
  if (runDataInfo.value?.env_locked || runDataBusy.value || restartPending.value) return;
  runDataError.value = '';
  runDataMode.value = 'migrate';
  runDataDialogOpen.value = true;
  await nextTick();
  runDataDialogElement.value?.focus();
}

function closeRunDataDialog() {
  if (!runDataBusy.value) runDataDialogOpen.value = false;
}

async function applyRunDataChange() {
  if (runDataBusy.value || !runDataPath.value.trim()) return;
  runDataBusy.value = true;
  runDataError.value = '';
  try {
    await requestRunData('/api/desktop/rundata/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data_root: runDataPath.value.trim(), migrate: runDataMode.value === 'migrate' })
    });
    runDataDialogOpen.value = false;
    restartPending.value = true;
    await loadRunDataInfo();
  } catch (error: any) {
    setRunDataError(error);
  } finally {
    runDataBusy.value = false;
  }
}

async function restartDesktop() {
  if (restarting.value) return;
  restarting.value = true;
  runDataError.value = '';
  try {
    await requestRunData('/api/desktop/rundata/restart', { method: 'POST' });
  } catch {
    // 发出重启请求后，连接可能先于 HTTP 响应关闭；此时视为已交给桌面壳重启。
    window.setTimeout(() => {
      if (document.visibilityState === 'visible') {
        restarting.value = false;
        setRunDataError('restart_failed');
      }
    }, 8000);
  }
}

onMounted(() => {
  if (isMacDesktop.value) void loadRunDataInfo();
});
</script>

<template>
  <section class="settings-page">
    <label class="settings-toggle-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.autoTitleTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.autoTitleDesc') }}</span>
      </span>
      <input
        type="checkbox"
        :checked="form.auto_generate_title"
        @change="
          personalization.updateField({
            key: 'auto_generate_title',
            value: ($event.target as HTMLInputElement).checked
          })
        "
      />
      <FancyCheck :checked="form.auto_generate_title" />
    </label>

    <div v-if="form.auto_generate_title" class="settings-select-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.titleModelTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.titleModelDesc') }}</span>
      </span>
      <ModelSelectDropdown
        :model-value="form.title_model"
        :options="auxModelOptions"
        :extra-options="defaultExtraOptions"
        @select="(v) => personalization.updateField({ key: 'title_model', value: v })"
      />
    </div>

    <div v-if="isMacDesktop" class="settings-action-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('settings.runDataTitle') }}</span>
        <span class="settings-row-desc">{{ $t('settings.runDataDesc') }}</span>
      </span>
      <div class="run-data-settings">
        <div v-if="runDataInfo?.env_locked" class="run-data-notice">
          <span>{{ $t('settings.runDataEnvLocked') }}</span>
          <code>{{ runDataInfo.env_path }}</code>
        </div>
        <template v-else>
          <div class="run-data-path-row">
            <input
              v-model="runDataPath"
              class="run-data-path-input"
              type="text"
              :placeholder="runDataInfo?.default_path || $t('settings.runDataPathPlaceholder')"
              :disabled="runDataBusy || restartPending"
              :aria-label="$t('settings.runDataPathLabel')"
              spellcheck="false"
            />
            <button
              type="button"
              class="settings-secondary-button"
              :disabled="runDataBusy || restartPending"
              @click="chooseRunDataFolder"
            >
              {{ $t('settings.runDataBrowse') }}
            </button>
          </div>
          <div class="run-data-apply-row">
            <span class="run-data-current-path">
              {{ $t('settings.runDataCurrentPath') }} <code>{{ runDataInfo?.active_path }}</code>
            </span>
            <button
              type="button"
              class="settings-primary-button"
              :disabled="runDataBusy || restartPending || !runDataPath.trim() || runDataPath.trim() === runDataInfo?.active_path"
              @click="openRunDataDialog"
            >
              {{ $t('settings.runDataApply') }}
            </button>
          </div>
        </template>

        <div v-if="restartPending" class="run-data-restart-row">
          <span class="settings-mini-status warning">{{ $t('settings.runDataRestartRequired') }}</span>
          <button type="button" class="settings-primary-button" :disabled="restarting" @click="restartDesktop">
            {{ restarting ? $t('settings.runDataRestarting') : $t('settings.runDataRestartNow') }}
          </button>
        </div>
        <p v-if="runDataError && !runDataDialogOpen" class="run-data-error" role="alert">
          {{ $t(`settings.runDataError.${runDataError}`) }}
        </p>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="runDataDialogOpen" class="run-data-dialog-backdrop" @click.self="closeRunDataDialog">
        <div
          ref="runDataDialogElement"
          class="run-data-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="run-data-dialog-title"
          tabindex="-1"
          @keydown.esc.stop.prevent="closeRunDataDialog"
        >
          <h2 id="run-data-dialog-title" class="run-data-dialog-title">{{ $t('settings.runDataConfirmTitle') }}</h2>
          <p class="run-data-dialog-path"><code>{{ runDataPath }}</code></p>
          <div class="run-data-choices" role="radiogroup" :aria-label="$t('settings.runDataModeLabel')">
            <button
              type="button"
              role="radio"
              class="run-data-choice"
              :class="{ selected: runDataMode === 'migrate' }"
              :aria-checked="runDataMode === 'migrate'"
              @click="runDataMode = 'migrate'"
            >
              <span class="run-data-choice-mark" aria-hidden="true"></span>
              <span class="run-data-choice-copy">
                <span class="run-data-choice-title">{{ $t('settings.runDataMigrate') }}</span>
                <span class="run-data-choice-desc">{{ $t('settings.runDataMigrateDesc') }}</span>
              </span>
            </button>
            <button
              type="button"
              role="radio"
              class="run-data-choice"
              :class="{ selected: runDataMode === 'empty' }"
              :aria-checked="runDataMode === 'empty'"
              @click="runDataMode = 'empty'"
            >
              <span class="run-data-choice-mark" aria-hidden="true"></span>
              <span class="run-data-choice-copy">
                <span class="run-data-choice-title">{{ $t('settings.runDataStartFresh') }}</span>
                <span class="run-data-choice-desc">{{ $t('settings.runDataFreshDesc') }}</span>
              </span>
            </button>
          </div>
          <p v-if="runDataError" class="run-data-error" role="alert">
            {{ $t(`settings.runDataError.${runDataError}`) }}
          </p>
          <div class="run-data-dialog-actions">
            <button type="button" class="settings-secondary-button" :disabled="runDataBusy" @click="closeRunDataDialog">
              {{ $t('settings.runDataCancel') }}
            </button>
            <button
              type="button"
              class="settings-primary-button"
              :disabled="runDataBusy || !runDataPath.trim()"
              @click="applyRunDataChange"
            >
              {{ runDataBusy ? $t('settings.runDataWorking') : $t('settings.runDataConfirm') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <div v-if="isAppShell" class="settings-action-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.appUpdateTitle') }}</span>
        <span class="settings-row-desc">{{
          $t('personalization.appUpdateDesc', {
            version: appCurrentVersionText,
            checked: appUpdateCheckedText
          })
        }}</span>
      </span>
      <div class="settings-inline-actions">
        <span class="settings-mini-status" :class="{ warning: appHasUpdate }">{{ appUpdateStateText }}</span>
        <button
          type="button"
          class="settings-secondary-button"
          :disabled="appUpdateChecking"
          @click="checkAppUpdate"
        >
          {{ appUpdateChecking ? $t('personalization.appChecking') : $t('personalization.appCheckUpdate') }}
        </button>
        <button v-if="appHasUpdate" type="button" class="settings-primary-button" @click="downloadLatestApp">
          {{ $t('common.download') }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.run-data-settings {
  grid-column: 1 / -1;
  display: flex;
  width: 100%;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
}

.run-data-path-row,
.run-data-apply-row,
.run-data-restart-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.run-data-apply-row {
  justify-content: space-between;
}

.run-data-current-path {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  color: var(--text-secondary);
  font-size: 12px;
}

.run-data-current-path code {
  overflow-wrap: anywhere;
  color: var(--text-primary);
}

.run-data-path-input {
  flex: 1 1 auto;
  min-width: 120px;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--border-default);
  border-radius: 7px;
  background: var(--surface-base);
  color: var(--text-primary);
  font: inherit;
}

.run-data-path-input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.settings-secondary-button.selected {
  border-color: var(--border-strong);
  background: var(--surface-muted);
  color: var(--text-primary);
}

.run-data-error {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.run-data-error {
  color: var(--state-danger);
}

.run-data-notice {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.run-data-notice code {
  overflow-wrap: anywhere;
  color: var(--text-primary);
}

.run-data-restart-row {
  justify-content: flex-end;
  padding-top: 10px;
  border-top: 1px solid var(--border-default);
}

.run-data-dialog-backdrop {
  position: fixed;
  z-index: 10000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--overlay-scrim);
}

.run-data-dialog {
  width: min(100%, 500px);
  max-height: min(80vh, 640px);
  overflow-y: auto;
  padding: 22px;
  border: 1px solid var(--border-default);
  border-radius: 12px;
  background: var(--surface-card);
  color: var(--text-primary);
  box-shadow: var(--shadow-soft);
}

.run-data-dialog:focus {
  outline: none;
}

.run-data-dialog-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.35;
}

.run-data-dialog-path {
  margin: 8px 0 16px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.run-data-choices {
  border-top: 1px solid var(--border-default);
}

.run-data-choice {
  display: flex;
  width: 100%;
  height: 92px;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 8px;
  border: 0;
  border-bottom: 1px solid var(--border-default);
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}

.run-data-choice:hover,
.run-data-choice.selected {
  background: var(--surface-muted);
}

.run-data-choice:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.run-data-choice-mark {
  display: grid;
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
  place-items: center;
  margin-top: 2px;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
}

.run-data-choice.selected .run-data-choice-mark {
  border-color: var(--accent);
}

.run-data-choice.selected .run-data-choice-mark::after {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  content: '';
}

.run-data-choice-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.run-data-choice-title {
  font-size: 13px;
  font-weight: 550;
}

.run-data-choice-desc {
  display: -webkit-box;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.run-data-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}

@media (max-width: 720px) {
  .run-data-path-row {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .run-data-path-input {
    flex-basis: 100%;
  }

  .run-data-dialog {
    padding: 18px;
  }
}
</style>

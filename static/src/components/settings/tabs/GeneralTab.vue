<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref } from 'vue';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import UiIcon from '@/components/ui/UiIcon.vue';
import HostPasswordSettings from './HostPasswordSettings.vue';
import CrossPetSettings from './CrossPetSettings.vue';
import { t } from '@/locales';
import { useCommandBlockingStore } from '@/stores/commandBlocking';
import { desktopPlatform, isDesktopShell } from '@/utils/desktopPlatform';
import { modEnterLabel, normalizeSendShortcut } from '@/utils/sendShortcut';

defineOptions({ name: 'GeneralTab' });

// 个人级指令拦截：独立接口 /api/command-blocking（不走 personalization 字段），
// 开关立即 POST 单字段 { enabled }；规则编辑走统一窗口（App.vue 挂载的 CommandBlockingDialog）
const commandBlocking = useCommandBlockingStore();

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
  appHasUpdate,
  appUpdateStateText,
  appUpdateChecking,
  checkAppUpdate,
  downloadLatestApp,
  subAgentModels,
  wsDescKey
} = ctx;

// 运行数据目录：两个桌面壳（macOS Electron / Windows Tauri）都支持——
// 壳侧各自实现了 /rundata/info|apply|restart 与启动前迁移，网页版不显示该区块。
// 平台判定统一走 desktopPlatform（两个壳注入的标记口径不同，见该模块注释）。
const runDataSupported = isDesktopShell();
// 目录选择器分别：macOS 壳自带原生对话框（桥端 /rundata/choose）；
// Windows 壳没实现该端点，复用后端已有的 /api/project/pick-folder（PowerShell + IFileOpenDialog）
const runDataNativeDialog = desktopPlatform() === 'macos';
const runDataInfo = ref<any>(null);
const runDataPath = ref('');
const runDataMode = ref<'migrate' | 'empty'>('migrate');
const runDataDialogOpen = ref(false);
const runDataDialogElement = ref<HTMLElement | null>(null);
const runDataBusy = ref(false);
const runDataError = ref('');
const runDataErrorDetail = ref('');
const restartPending = ref(false);
const restarting = ref(false);

const runDataDirty = computed(() => {
  const next = runDataPath.value.trim();
  return Boolean(next) && next !== (runDataInfo.value?.active_path || '');
});

const auxModelOptions = computed(() =>
  (subAgentModels.value || []).map((m: any) => ({ key: m.key, label: m.name || m.key }))
);
const defaultExtraOptions = computed(() => [
  { key: '', label: t('personalization.defaultModelOption') }
]);

// 订阅额度来源：自动跟随当前模型所属提供商；也可固定只显示某一家的额度。
const quotaSourceItems = [
  { value: 'auto', label: t('personalization.subscriptionQuotaAuto') },
  { value: 'opencode', label: t('personalization.subscriptionQuotaOpencode') },
  { value: 'chatgpt', label: t('personalization.subscriptionQuotaChatgpt') }
];

// 发送快捷键：主输入框与快捷窗口共用；修饰键按平台显示（Mac ⌘ / 其它 Ctrl）
const modKeyLabel = modEnterLabel().replace('+Enter', '');
const sendShortcutItems = [
  { value: 'mod_enter', label: t('personalization.sendShortcutModEnter', { mod: modKeyLabel }) },
  { value: 'enter', label: t('personalization.sendShortcutEnter') }
];

function updateThinkingSummaryInterval(event: Event) {
  const input = event.target as HTMLInputElement;
  const parsed = input.value.trim() ? Number(input.value) : NaN;
  const value = Number.isFinite(parsed) ? Math.min(3600, Math.max(1, Math.trunc(parsed))) : 20;
  input.value = String(value);
  personalization.updateField({ key: 'thinking_summary_interval_seconds', value });
}

const knownRunDataErrors = new Set([
  'environment_locked',
  'path_required',
  'same_directory',
  'overlapping_directories',
  'target_not_directory',
  'target_not_empty',
  'verification_failed',
  'restart_failed',
  'request_failed'
]);

function setRunDataError(error: any) {
  const raw = String(error?.message || error || 'unknown');
  const known = knownRunDataErrors.has(raw);
  runDataError.value = known ? raw : 'unknown';
  // 非已知错误码时把原文一并回显：迁移链路的 I/O 失败都是带系统原因的描述
  //（如「复制文件失败: 拒绝访问」），只显示「未知错误」用户无从下手
  runDataErrorDetail.value = known ? '' : raw;
}

function clearRunDataError() {
  runDataError.value = '';
  runDataErrorDetail.value = '';
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
    // 上一次启动时的迁移失败原因（迁移发生在启动早期，用户当时看不到结果）
    if (payload.last_error) {
      setRunDataError({ message: payload.last_error });
    } else if (!runDataBusy.value) {
      clearRunDataError();
    }
  } catch (error: any) {
    setRunDataError(error);
  }
}

async function chooseRunDataFolder() {
  clearRunDataError();
  try {
    if (runDataNativeDialog) {
      const payload = await requestRunData('/api/desktop/rundata/choose', { method: 'POST' });
      if (!payload.canceled && payload.path) runDataPath.value = payload.path;
      return;
    }
    // Windows 壳：走后端已有的原生文件夹选择端点（宿主模式限定；取消时 path 为空串）
    const response = await fetch('/api/project/pick-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result?.success) {
      throw new Error(result?.error || 'request_failed');
    }
    const picked = String(result?.data?.path || '').trim();
    if (picked) runDataPath.value = picked;
  } catch (error: any) {
    setRunDataError(error);
  }
}

async function openRunDataDialog() {
  if (runDataInfo.value?.env_locked || runDataBusy.value || restartPending.value) return;
  clearRunDataError();
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
  clearRunDataError();
  try {
    await requestRunData('/api/desktop/rundata/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        data_root: runDataPath.value.trim(),
        migrate: runDataMode.value === 'migrate'
      })
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
  clearRunDataError();
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
  if (runDataSupported) void loadRunDataInfo();
  // 进入通用页签时拉取一次拦截开关状态（失败保留 error，开关仍可重试）
  if (!commandBlocking.loaded) void commandBlocking.fetchState();
});
</script>

<template>
  <section class="stg-page">
    <HostPasswordSettings />
    <CrossPetSettings />
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardGeneralShortcuts') }}</span>
    </div>
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.sendShortcutTitle') }}</span>
          <span class="stg-row-desc">{{
            $t('personalization.sendShortcutDesc', { mod: modKeyLabel })
          }}</span>
        </span>
        <UiSelect
          :model-value="normalizeSendShortcut(form.send_shortcut)"
          :options="sendShortcutItems"
          align-end
          @update:model-value="
            (value) =>
              personalization.updateField({
                key: 'send_shortcut',
                value: normalizeSendShortcut(value)
              })
          "
        />
      </div>
    </div>
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardGeneralQuota') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.subscriptionQuotaTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.subscriptionQuotaDesc') }}</span>
        </span>
        <input
          type="checkbox"
          :checked="form.subscription_quota_enabled"
          @change="
            personalization.updateField({
              key: 'subscription_quota_enabled',
              value: ($event.target as HTMLInputElement).checked
            })
          "
        />
        <UiSwitch decorative :checked="form.subscription_quota_enabled" />
      </label>

      <div v-if="form.subscription_quota_enabled" class="stg-select-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{
            $t('personalization.subscriptionQuotaSourceTitle')
          }}</span>
          <span class="stg-row-desc">{{ $t('personalization.subscriptionQuotaSourceDesc') }}</span>
        </span>
        <UiSelect
          :model-value="form.subscription_quota_source || 'auto'"
          :options="quotaSourceItems"
          align-end
          @update:model-value="
            (value) =>
              personalization.updateField({
                key: 'subscription_quota_source',
                value: value || 'auto'
              })
          "
        />
      </div>
    </div>

    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardGeneralTitle') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.autoTitleTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.autoTitleDesc') }}</span>
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
        <UiSwitch decorative :checked="form.auto_generate_title" />
      </label>

      <div v-if="form.auto_generate_title" class="stg-select-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.titleModelTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.titleModelDesc') }}</span>
        </span>
        <UiModelSelect
          :model-value="form.title_model"
          :options="auxModelOptions"
          :extra-options="defaultExtraOptions"
          align-end
          @update:model-value="(v) => personalization.updateField({ key: 'title_model', value: v })"
        />
      </div>
    </div>

    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardGeneralThinking') }}</span>
    </div>
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.thinkingSummaryModelTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.thinkingSummaryModelDesc') }}</span>
        </span>
        <UiModelSelect
          :model-value="form.thinking_summary_model"
          :options="auxModelOptions"
          :placeholder="$t('personalization.thinkingSummaryModelRequired')"
          align-end
          @update:model-value="
            (v) => personalization.updateField({ key: 'thinking_summary_model', value: v })
          "
        />
      </div>

      <label
        class="stg-toggle-row thinking-summary-toggle"
        :aria-disabled="!form.thinking_summary_model"
      >
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.thinkingSummaryTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.thinkingSummaryDesc') }}</span>
        </span>
        <input
          type="checkbox"
          :checked="form.thinking_summary_enabled"
          :disabled="!form.thinking_summary_model"
          @change="
            personalization.updateField({
              key: 'thinking_summary_enabled',
              value: ($event.target as HTMLInputElement).checked && !!form.thinking_summary_model
            })
          "
        />
        <UiSwitch decorative :checked="form.thinking_summary_enabled" />
      </label>

      <div v-if="form.thinking_summary_enabled" class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{
            $t('personalization.thinkingSummaryIntervalTitle')
          }}</span>
          <span class="stg-row-desc">{{ $t('personalization.thinkingSummaryIntervalDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          min="1"
          max="3600"
          step="1"
          :aria-label="$t('personalization.thinkingSummaryIntervalTitle')"
          :value="form.thinking_summary_interval_seconds"
          @change="updateThinkingSummaryInterval"
        />
      </div>
    </div>

    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardGeneralSecurity') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('commandBlocking.settingsTitle') }}</span>
          <span class="stg-row-desc">{{ $t(wsDescKey('commandBlocking.settingsDesc')) }}</span>
          <span
            v-if="commandBlocking.error && !commandBlocking.dialogOpen"
            class="command-blocking-error"
            role="alert"
          >
            {{ $t('commandBlocking.toggleFailed') }}
            <span class="command-blocking-error-detail">{{ commandBlocking.error }}</span>
          </span>
        </span>
        <input
          type="checkbox"
          :checked="commandBlocking.enabled"
          :disabled="
            commandBlocking.toggling ||
            commandBlocking.saving ||
            commandBlocking.loading ||
            !commandBlocking.loaded ||
            commandBlocking.loadFailed
          "
          @change="commandBlocking.setEnabled(($event.target as HTMLInputElement).checked)"
        />
        <UiSwitch decorative :checked="commandBlocking.enabled" />
      </label>

      <div class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('commandBlocking.rulesLabel') }}</span>
          <span class="stg-row-desc">{{ $t('commandBlocking.rulesDesc') }}</span>
        </span>
        <button
          type="button"
          class="stg-secondary-button"
          :disabled="commandBlocking.saving || commandBlocking.toggling"
          @click="commandBlocking.openDialog()"
        >
          {{ $t('commandBlocking.manageRules') }}
        </button>
      </div>
    </div>

    <div v-if="runDataSupported" class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.runDataTitle') }}</span>
    </div>
    <div v-if="runDataSupported" class="stg-card">
      <div v-if="runDataSupported" class="stg-textarea-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('settings.runDataTitle') }}</span>
          <span class="stg-row-desc">{{
            runDataInfo?.env_locked ? $t('settings.runDataEnvLocked') : $t('settings.runDataDesc')
          }}</span>
        </span>
        <code v-if="runDataInfo?.env_locked" class="run-data-env-path">{{
          runDataInfo.env_path
        }}</code>
        <div v-else class="stg-field">
          <input
            v-model="runDataPath"
            type="text"
            :placeholder="runDataInfo?.default_path || $t('settings.runDataPathPlaceholder')"
            :disabled="runDataBusy || restartPending"
            :aria-label="$t('settings.runDataPathLabel')"
            spellcheck="false"
            @keydown.enter.prevent="runDataDirty && openRunDataDialog()"
          />
          <button
            v-if="runDataDirty"
            type="button"
            class="stg-field-action is-text"
            :disabled="runDataBusy || restartPending"
            @click="openRunDataDialog"
          >
            {{ $t('settings.runDataApply') }}
          </button>
          <button
            type="button"
            class="stg-field-action"
            :disabled="runDataBusy || restartPending"
            :aria-label="$t('settings.runDataBrowse')"
            :title="$t('settings.runDataBrowse')"
            @click="chooseRunDataFolder"
          >
            <UiIcon name="folder" />
          </button>
        </div>
        <p v-if="runDataError && !runDataDialogOpen" class="run-data-error" role="alert">
          {{ $t(`settings.runDataError.${runDataError}`) }}
          <span v-if="runDataErrorDetail" class="run-data-error-detail">{{
            runDataErrorDetail
          }}</span>
        </p>
      </div>

      <div v-if="runDataSupported && restartPending" class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('settings.runDataRestartTitle') }}</span>
          <span class="stg-row-desc">{{ $t('settings.runDataRestartRequired') }}</span>
        </span>
        <button
          type="button"
          class="stg-primary-button"
          :disabled="restarting"
          @click="restartDesktop"
        >
          {{ restarting ? $t('settings.runDataRestarting') : $t('settings.runDataRestartNow') }}
        </button>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="runDataDialogOpen"
        class="run-data-dialog-backdrop"
        @click.self="closeRunDataDialog"
      >
        <div
          ref="runDataDialogElement"
          class="run-data-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="run-data-dialog-title"
          tabindex="-1"
          @keydown.esc.stop.prevent="closeRunDataDialog"
        >
          <h2 id="run-data-dialog-title" class="run-data-dialog-title">
            {{ $t('settings.runDataConfirmTitle') }}
          </h2>
          <p class="run-data-dialog-path">
            <code>{{ runDataPath }}</code>
          </p>
          <div
            class="run-data-choices"
            role="radiogroup"
            :aria-label="$t('settings.runDataModeLabel')"
          >
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
            <button
              type="button"
              class="stg-secondary-button"
              :disabled="runDataBusy"
              @click="closeRunDataDialog"
            >
              {{ $t('settings.runDataCancel') }}
            </button>
            <button
              type="button"
              class="stg-primary-button"
              :disabled="runDataBusy || !runDataPath.trim()"
              @click="applyRunDataChange"
            >
              {{ runDataBusy ? $t('settings.runDataWorking') : $t('settings.runDataConfirm') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <div v-if="isAppShell" class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('personalization.appUpdateTitle') }}</span>
    </div>
    <div v-if="isAppShell" class="stg-card">
      <div v-if="isAppShell" class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.appUpdateTitle') }}</span>
          <span class="stg-row-desc">{{
            $t('personalization.appUpdateDesc', {
              version: appCurrentVersionText,
              state: appUpdateStateText
            })
          }}</span>
        </span>
        <button
          v-if="appHasUpdate"
          type="button"
          class="stg-primary-button"
          @click="downloadLatestApp"
        >
          {{ $t('common.download') }}
        </button>
        <button
          v-else
          type="button"
          class="stg-secondary-button"
          :disabled="appUpdateChecking"
          @click="checkAppUpdate"
        >
          {{
            appUpdateChecking
              ? $t('personalization.appChecking')
              : $t('personalization.appCheckUpdate')
          }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.thinking-summary-toggle[aria-disabled='true'] {
  cursor: not-allowed;
  opacity: 0.55;
}

.command-blocking-error {
  display: block;
  margin-top: 6px;
  color: var(--state-danger);
  font-size: 12px;
  line-height: 1.5;
}

.command-blocking-error-detail {
  display: block;
  color: var(--text-tertiary);
  font-size: 11px;
  word-break: break-all;
}

.run-data-env-path {
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-size: 12px;
}

.run-data-error {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

/* 迁移失败的系统原因原文（后端下发文字，前端原样展示、不做多语言） */
.run-data-error-detail {
  display: block;
  margin-top: 2px;
  color: var(--text-tertiary);
  font-size: 11px;
  word-break: break-all;
}

.run-data-error {
  color: var(--state-danger);
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
  .run-data-dialog {
    padding: 18px;
  }
}
</style>

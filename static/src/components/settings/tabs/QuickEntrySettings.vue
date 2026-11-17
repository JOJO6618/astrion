<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { useModelStore } from '@/stores/model';
import { usePersonalizationStore } from '@/stores/personalization';
import { t } from '@/locales';
import { isWindowsDesktopShell } from '@/utils/desktopPlatform';

const windowsShell = isWindowsDesktopShell();
const modifiers = windowsShell
  ? ['alt', 'left-control', 'right-control', 'shift']
  : ['option', 'command', 'control'];
const modifierLabel = (key: string) =>
  t(
    `quickEntry.${key === 'left-control' ? 'leftControl' : key === 'right-control' ? 'rightControl' : key}`
  );

const modifierItems = computed(() =>
  modifiers.map((key) => ({ value: key, label: modifierLabel(key) }))
);

interface QuickSettings {
  enabled: boolean;
  modifier: string;
  workspace: string;
  model: string;
}
interface SettingsBridge {
  info(): Promise<QuickSettings>;
  configure(patch: Partial<QuickSettings>): Promise<QuickSettings>;
  permissions(): Promise<{
    screenPermission: PermissionStatus;
    inputPermission: PermissionStatus;
    error?: string;
  }>;
  capturePermission(): Promise<PermissionStatus>;
  inputPermission(): Promise<PermissionStatus>;
  open(): Promise<void>;
}
type PermissionStatus = 'granted' | 'denied' | 'unknown';
declare global {
  interface Window {
    astrionQuickSettings?: SettingsBridge;
  }
}
const bridge = window.astrionQuickSettings;
const config = ref<QuickSettings>({
  enabled: false,
  modifier: windowsShell ? 'alt' : 'option',
  model: '',
  workspace: ''
});
const workspaces = ref<{ workspace_id: string; label: string }[]>([]);
const defaultWorkspace = ref('');
const permissions = ref({
  screenPermission: 'unknown' as PermissionStatus,
  inputPermission: 'unknown' as PermissionStatus
});
const checkingPermissions = ref(false),
  authorizing = ref(false),
  permissionError = ref('');
const error = ref(''),
  saving = ref(false),
  loaded = ref(false);
const models = useModelStore();
const personalization = usePersonalizationStore();
const options = computed(() => models.visibleModels.filter((item) => item.supportsImage));
const inheritedWorkspace = computed(
  () =>
    workspaces.value.find((item) => item.workspace_id === defaultWorkspace.value)?.label ||
    t('common.unset')
);
const inheritedModel = computed(
  () =>
    models.allModels.find((item) => item.key === personalization.form.default_model)?.label ||
    t('common.unset')
);
const workspaceItems = computed(() => [
  { value: '', label: t('quickEntry.followGlobalValue', { value: inheritedWorkspace.value }) },
  ...workspaces.value.map((item) => ({ value: item.workspace_id, label: item.label }))
]);
const extraModels = computed(() => [
  { key: '', label: t('quickEntry.followGlobalValue', { value: inheritedModel.value }) }
]);
function permissionLabel(status: PermissionStatus) {
  if (windowsShell && !checkingPermissions.value) {
    return t(
      status === 'granted'
        ? 'quickEntry.capabilityReady'
        : status === 'denied'
          ? 'quickEntry.capabilityUnavailable'
          : 'quickEntry.permissionUnknown'
    );
  }
  return t(
    checkingPermissions.value
      ? 'quickEntry.permissionChecking'
      : status === 'granted'
        ? 'quickEntry.permissionGranted'
        : status === 'denied'
          ? 'quickEntry.permissionDenied'
          : 'quickEntry.permissionUnknown'
  );
}
async function refreshPermissions() {
  if (!bridge || checkingPermissions.value || authorizing.value) return;
  checkingPermissions.value = true;
  try {
    const result = await bridge.permissions();
    permissions.value = result;
    permissionError.value = result.error || '';
  } catch (exception) {
    permissionError.value = String(exception);
  } finally {
    checkingPermissions.value = false;
  }
}
async function authorize(kind: 'screenPermission' | 'inputPermission') {
  if (!bridge || authorizing.value || checkingPermissions.value) return;
  authorizing.value = true;
  try {
    permissions.value[kind] = await (kind === 'screenPermission'
      ? bridge.capturePermission()
      : bridge.inputPermission());
    permissionError.value = '';
  } catch (exception) {
    permissionError.value = String(exception);
  } finally {
    authorizing.value = false;
    void refreshPermissions();
  }
}
async function openQuickChat() {
  if (!bridge) return;
  try {
    await bridge.open();
    error.value = '';
  } catch (exception) {
    error.value = String(exception);
  }
}

async function update(patch: Partial<QuickSettings>) {
  if (!bridge || saving.value || !loaded.value) return;
  saving.value = true;
  try {
    config.value = await bridge.configure(patch);
    error.value = '';
  } catch (exception) {
    error.value = String(exception);
  } finally {
    saving.value = false;
  }
}
async function selectWorkspace(id: string) {
  await update({ workspace: id });
}
onMounted(async () => {
  window.addEventListener('focus', refreshPermissions);
  if (!bridge) return;
  void refreshPermissions();
  try {
    const [settings, response] = await Promise.all([
      bridge.info(),
      fetch('/api/host/workspaces').then((result) => result.json())
    ]);
    if (!response.success) throw new Error(response.error || t('quickEntry.noWorkspaces'));
    config.value = settings;
    workspaces.value = response.data?.workspaces || [];
    defaultWorkspace.value = response.data?.default_workspace_id || '';
    if (!models.allModels.length) await models.fetchModels();
    loaded.value = true;
  } catch (exception) {
    error.value = String(exception);
  }
});
onBeforeUnmount(() => {
  window.removeEventListener('focus', refreshPermissions);
});
</script>

<template>
  <section v-if="bridge" class="stg-page quick-settings" :aria-busy="!loaded && !error">
    <p v-if="!loaded && !error" class="quick-settings-note" role="status">
      {{ t('common.loading') }}
    </p>
    <template v-if="loaded">
      <div class="stg-section-divider">
        <span class="stg-section-divider__label">{{ t('settings.cardQuickBasics') }}</span>
      </div>
      <div class="stg-card">
        <button
          type="button"
          class="stg-toggle-row"
          role="switch"
          :aria-checked="config.enabled"
          :disabled="!loaded || saving"
          @click="update({ enabled: !config.enabled })"
        >
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ t('quickEntry.enabled') }}</span
            ><span class="stg-row-desc">{{ t('quickEntry.desktopHint') }}</span></span
          >
          <UiSwitch decorative :checked="config.enabled" />
        </button>
        <div class="stg-select-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ t('quickEntry.shortcut') }}</span
            ><span class="stg-row-desc">{{ t('quickEntry.shortcutDesc') }}</span></span
          >
          <UiSelect
            :model-value="config.modifier"
            :options="modifierItems"
            :disabled="!loaded || saving"
            align-end
            :label="t('quickEntry.shortcut')"
            @update:model-value="(key) => update({ modifier: key })"
          />
        </div>
        <div class="stg-select-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ t('quickEntry.defaultWorkspace') }}</span
            ><span class="stg-row-desc">{{ t('quickEntry.defaultWorkspaceDesc') }}</span></span
          >
          <UiSelect
            :model-value="config.workspace"
            :options="workspaceItems"
            :disabled="!loaded || saving"
            align-end
            :label="t('quickEntry.defaultWorkspace')"
            @update:model-value="selectWorkspace"
          />
        </div>
        <div class="stg-select-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ t('quickEntry.defaultModel') }}</span
            ><span class="stg-row-desc">{{ t('quickEntry.defaultModelDesc') }}</span></span
          >
          <div
            class="quick-model-control"
            :class="{ inactive: !loaded || saving }"
            :inert="!loaded || saving"
          >
            <UiModelSelect
              :model-value="config.model"
              :options="options"
              :extra-options="extraModels"
              align-end
              @update:model-value="(value) => update({ model: value })"
            />
          </div>
        </div>
      </div>
      <div class="stg-section-divider">
        <span class="stg-section-divider__label">{{ t('settings.cardQuickPermissions') }}</span>
      </div>
      <div class="stg-card">
        <div
          v-for="kind in ['screenPermission', 'inputPermission'] as const"
          :key="kind"
          class="stg-select-row"
        >
          <span class="stg-row-copy">
            <span class="stg-row-title">{{
              t(
                windowsShell
                  ? kind === 'screenPermission'
                    ? 'quickEntry.screenCapability'
                    : 'quickEntry.inputCapability'
                  : kind === 'screenPermission'
                    ? 'quickEntry.screenPermissionTitle'
                    : 'quickEntry.inputPermissionTitle'
              )
            }}</span>
            <span class="stg-row-desc">{{ permissionLabel(permissions[kind]) }}</span>
          </span>
          <button
            v-if="!windowsShell"
            type="button"
            class="stg-secondary-button"
            :disabled="checkingPermissions || authorizing || permissions[kind] === 'granted'"
            @click="authorize(kind)"
          >
            {{
              t(
                kind === 'screenPermission'
                  ? 'quickEntry.permission'
                  : 'quickEntry.inputPermissionAction'
              )
            }}
          </button>
        </div>
        <p v-if="permissionError" class="quick-settings-error" role="status">
          {{ permissionError }}
        </p>
      </div>
      <div class="stg-section-divider">
        <span class="stg-section-divider__label">{{ t('settings.cardQuickActions') }}</span>
      </div>
      <div class="stg-card">
        <div class="stg-action-row">
          <span class="stg-row-copy"
            ><span class="stg-row-title">{{ t('quickEntry.openQuickChat') }}</span
            ><span class="stg-row-desc">{{ t('quickEntry.openQuickChatDesc') }}</span></span
          >
          <button
            type="button"
            class="stg-secondary-button"
            :disabled="!loaded || !config.enabled"
            @click="openQuickChat"
          >
            {{ t('quickEntry.open') }}
          </button>
        </div>
      </div>
    </template>
    <p v-if="error" class="quick-settings-error" role="status">{{ error }}</p>
  </section>
</template>

<style scoped>
/* 开关行的布局与原生外观重置统一由 settings.scss 的 .stg-toggle-row 提供，
   这里不再重复：卡片层级变化时直接子选择器会静默失效。 */
.quick-setting-value {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.quick-model-control {
  max-width: 300px;
}
.quick-model-control.inactive,
.quick-settings button:disabled {
  opacity: 0.5;
  cursor: default;
}
.quick-settings-note {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
  margin: 12px 0 20px;
}
.quick-settings-error {
  color: var(--state-danger);
  font-size: 13px;
  line-height: 1.6;
}
</style>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import ModelSelectDropdown from '@/components/personalization/ModelSelectDropdown.vue';
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
const workspaceOpen = ref(false),
  error = ref(''),
  saving = ref(false),
  loaded = ref(false);
const workspaceRoot = ref<HTMLElement>();
const menuStyle = ref<Record<string, string>>({});
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
const workspaceLabel = computed(() =>
  config.value.workspace
    ? workspaces.value.find((item) => item.workspace_id === config.value.workspace)?.label ||
      config.value.workspace
    : t('quickEntry.followGlobal')
);
const extraModels = computed(() => [{ key: '', label: t('quickEntry.followGlobal') }]);
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
function toggleWorkspace() {
  if (workspaceOpen.value) {
    workspaceOpen.value = false;
    return;
  }
  const bounds = workspaceRoot.value?.getBoundingClientRect();
  if (!bounds) return;
  const width = Math.min(300, window.innerWidth - 24);
  const below = window.innerHeight - bounds.bottom - 16;
  menuStyle.value = {
    width: `${width}px`,
    left: `${Math.max(12, Math.min(bounds.right - width, window.innerWidth - width - 12))}px`,
    ...(below >= 140
      ? { top: `${bounds.bottom + 6}px` }
      : { bottom: `${window.innerHeight - bounds.top + 6}px` }),
    maxHeight: `${Math.max(68, Math.min(240, below >= 140 ? below : bounds.top - 18))}px`
  };
  workspaceOpen.value = true;
}
function outside(event: PointerEvent) {
  if (!workspaceRoot.value?.contains(event.target as Node)) workspaceOpen.value = false;
}
function closeWorkspace() {
  workspaceOpen.value = false;
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeWorkspace();
}
async function selectWorkspace(id: string) {
  closeWorkspace();
  await update({ workspace: id });
}
onMounted(async () => {
  document.addEventListener('pointerdown', outside);
  document.addEventListener('keydown', keydown);
  window.addEventListener('resize', closeWorkspace);
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
  document.removeEventListener('pointerdown', outside);
  document.removeEventListener('keydown', keydown);
  window.removeEventListener('resize', closeWorkspace);
  window.removeEventListener('focus', refreshPermissions);
});
</script>

<template>
  <section v-if="bridge" class="settings-page quick-settings" :aria-busy="!loaded && !error">
    <p v-if="!loaded && !error" class="quick-settings-note" role="status">
      {{ t('common.loading') }}
    </p>
    <template v-if="loaded">
      <button
        type="button"
        class="settings-toggle-row"
        role="switch"
        :aria-checked="config.enabled"
        :disabled="!loaded || saving"
        @click="update({ enabled: !config.enabled })"
      >
        <span class="settings-row-copy"
          ><span class="settings-row-title">{{ t('quickEntry.enabled') }}</span
          ><span class="settings-row-desc">{{ t('quickEntry.desktopHint') }}</span></span
        >
        <FancyCheck :checked="config.enabled" />
      </button>
      <div class="settings-select-row">
        <span class="settings-row-copy"
          ><span class="settings-row-title">{{ t('quickEntry.shortcut') }}</span></span
        >
        <div class="quick-settings-modifiers">
          <button
            v-for="key in modifiers"
            :key="key"
            type="button"
            :disabled="!loaded || saving"
            :aria-pressed="config.modifier === key"
            @click="update({ modifier: key })"
          >
            {{ modifierLabel(key) }}
          </button>
        </div>
      </div>
      <div class="settings-select-row">
        <span class="settings-row-copy"
          ><span class="settings-row-title">{{ t('quickEntry.defaultWorkspace') }}</span
          ><span class="settings-row-desc">{{
            t('quickEntry.globalWorkspaceValue', { value: inheritedWorkspace })
          }}</span></span
        >
        <div ref="workspaceRoot" class="settings-select-wrap" :class="{ open: workspaceOpen }">
          <button
            type="button"
            class="settings-select-button"
            :disabled="!loaded || saving"
            :aria-expanded="workspaceOpen"
            @click="toggleWorkspace"
          >
            <span class="quick-setting-value">{{ workspaceLabel }}</span
            ><span class="select-chevron" aria-hidden="true" />
          </button>
          <div
            v-if="workspaceOpen"
            class="settings-floating-menu quick-workspace-menu"
            :style="menuStyle"
            role="menu"
          >
            <button
              type="button"
              class="settings-menu-option"
              role="menuitemradio"
              :aria-checked="!config.workspace"
              @click="selectWorkspace('')"
            >
              <span>{{ t('quickEntry.followGlobal') }}</span
              ><FancyCheck :checked="!config.workspace" :size="16" />
            </button>
            <button
              v-for="item in workspaces"
              :key="item.workspace_id"
              type="button"
              class="settings-menu-option"
              role="menuitemradio"
              :aria-checked="config.workspace === item.workspace_id"
              @click="selectWorkspace(item.workspace_id)"
            >
              <span>{{ item.label }}</span
              ><FancyCheck :checked="config.workspace === item.workspace_id" :size="16" />
            </button>
          </div>
        </div>
      </div>
      <div class="settings-select-row">
        <span class="settings-row-copy"
          ><span class="settings-row-title">{{ t('quickEntry.defaultModel') }}</span
          ><span class="settings-row-desc">{{
            t('quickEntry.globalModelValue', { value: inheritedModel })
          }}</span></span
        >
        <div
          class="quick-model-control"
          :class="{ inactive: !loaded || saving }"
          :inert="!loaded || saving"
        >
          <ModelSelectDropdown
            :model-value="config.model"
            :options="options"
            :extra-options="extraModels"
            @select="(value) => update({ model: value })"
          />
        </div>
      </div>
      <p class="quick-settings-note">{{ t('quickEntry.visionModelHint') }}</p>
      <div
        v-for="kind in ['screenPermission', 'inputPermission'] as const"
        :key="kind"
        class="settings-select-row"
      >
        <span class="settings-row-copy">
          <span class="settings-row-title">{{
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
          <span class="settings-row-desc">{{ permissionLabel(permissions[kind]) }}</span>
        </span>
        <button
          v-if="!windowsShell"
          type="button"
          class="settings-select-button"
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
      <p class="quick-settings-note">
        {{ t(windowsShell ? 'quickEntry.windowsCapabilityHint' : 'quickEntry.permissionHint') }}
      </p>
      <p v-if="permissionError" class="quick-settings-error" role="status">{{ permissionError }}</p>
      <div class="quick-settings-actions">
        <button
          type="button"
          class="settings-select-button"
          :disabled="!loaded || !config.enabled"
          @click="openQuickChat"
        >
          {{ t('quickEntry.openQuickChat') }}
        </button>
      </div>
    </template>
    <p v-if="error" class="quick-settings-error" role="status">{{ error }}</p>
  </section>
</template>

<style scoped>
.quick-settings > .settings-toggle-row {
  width: 100%;
  text-align: left;
  color: var(--text-primary);
  background: transparent;
  border: 0;
  font: inherit;
  cursor: pointer;
}
.quick-settings-modifiers {
  display: flex;
  gap: 4px;
}
.quick-settings-modifiers button {
  height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  cursor: pointer;
}
.quick-settings-modifiers button:hover,
.quick-settings-modifiers button[aria-pressed='true'] {
  background: var(--hover-bg);
}
.quick-setting-value {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.quick-workspace-menu {
  max-width: calc(100vw - 24px);
}
.quick-workspace-menu .settings-menu-option {
  height: 34px;
  min-height: 34px;
}
.quick-workspace-menu .settings-menu-option > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.quick-settings-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
  border-top: 1px solid var(--border-default);
}
.quick-settings-error {
  color: var(--state-danger);
  font-size: 13px;
  line-height: 1.6;
}
</style>

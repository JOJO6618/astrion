<script setup lang="ts">
import { computed, inject, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { useSandboxSetupStore } from '@/stores/sandboxSetup';

defineOptions({ name: 'WorkspaceTab' });

const { t } = useI18n();
const sandboxSetup = useSandboxSetupStore();

// 沙箱环境区块：进入工作区与权限页时补一次检测（复用 store 内部防抖）
onMounted(() => {
  if (!sandboxSetup.status) void sandboxSetup.fetchStatus();
});

const sandboxStatusText = computed(() => {
  const s = sandboxSetup.status;
  if (!s || sandboxSetup.checking) return t('sandbox.sectionChecking');
  if (!s.applicable) return t('sandbox.sectionUnavailable');
  return s.state === 'ready' ? t('sandbox.sectionReady') : t('sandbox.sectionMissing');
});

const sandboxShowWizard = computed(() => {
  const s = sandboxSetup.status;
  return !!s && s.applicable && s.state !== 'ready';
});

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  form,
  personalization,
  selectDefaultPermissionMode,
  selectDefaultWorkMode,
  selectVersioningBackupMode,
  permissionModeOptions,
  permissionDescKey,
  wsDescKey,
  workModeOptions
} = ctx;

const permissionItems = computed(() =>
  permissionModeOptions.map((option: any) => ({
    value: option.id,
    label: t(option.labelKey),
    description: t(permissionDescKey(option))
  }))
);
const workModeItems = computed(() =>
  workModeOptions.map((option: any) => ({
    value: option.id,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const backupModeItems = computed(() => [
  {
    value: 'shallow',
    label: t('personalization.shallowBackup'),
    description: t('personalization.shallowBackupDesc')
  },
  {
    value: 'full',
    label: t('personalization.fullBackup'),
    description: t(wsDescKey('personalization.fullBackupDesc'))
  }
]);
</script>

<template>
  <section class="stg-page" data-tutorial="personal-page-workspace">
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.defaultPermissionTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.defaultPermissionDesc') }}</span></span
        >
        <UiSelect
          :model-value="form.default_permission_mode"
          :options="permissionItems"
          align-end
          @update:model-value="selectDefaultPermissionMode"
        />
      </div>
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.defaultWorkModeTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.workRunModeDesc') }}</span></span
        >
        <UiSelect
          :model-value="form.default_work_mode"
          :options="workModeItems"
          align-end
          @update:model-value="selectDefaultWorkMode"
        />
      </div>
    </div>

    <div class="stg-card">
      <div class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('sandbox.sectionTitle') }}</span>
          <span class="stg-row-desc" :class="{ 'is-warning': sandboxShowWizard }">{{
            sandboxStatusText
          }}</span>
        </span>
        <button
          v-if="sandboxShowWizard"
          type="button"
          class="stg-primary-button"
          @click="sandboxSetup.openWizard()"
        >
          {{ $t('sandbox.openWizard') }}
        </button>
        <button
          v-else
          type="button"
          class="stg-secondary-button"
          :disabled="sandboxSetup.checking"
          @click="sandboxSetup.recheck()"
        >
          {{ sandboxSetup.checking ? $t('common.refreshing') : $t('sandbox.recheck') }}
        </button>
      </div>
      <div v-if="sandboxSetup.neverAsk" class="stg-action-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('sandbox.neverAgainTitle') }}</span>
          <span class="stg-row-desc">{{ $t('sandbox.neverAgainSet') }}</span>
        </span>
        <button type="button" class="stg-secondary-button" @click="sandboxSetup.resetNeverAsk()">
          {{ $t('sandbox.resetNeverAgain') }}
        </button>
      </div>
    </div>

    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.agentsMdInjectTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.agentsMdInjectDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.agents_md_auto_inject"
          @change="
            personalization.updateField({
              key: 'agents_md_auto_inject',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.agents_md_auto_inject"
      /></label>

      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.claudeMdInjectTitle') }}</span
          ><span class="stg-row-desc">{{
            $t(wsDescKey('personalization.claudeMdInjectDesc'))
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.claude_md_auto_inject"
          @change="
            personalization.updateField({
              key: 'claude_md_auto_inject',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.claude_md_auto_inject"
      /></label>

      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.agentsSkillsScanTitle') }}</span
          ><span class="stg-row-desc">{{
            $t(wsDescKey('personalization.agentsSkillsScanDesc'))
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.agents_skills_scan_enabled"
          @change="
            personalization.updateField({
              key: 'agents_skills_scan_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.agents_skills_scan_enabled"
      /></label>

      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.modifyHistoryTitle') }}</span
          ><span class="stg-row-desc">{{
            $t(wsDescKey('personalization.modifyHistoryDesc'))
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.modify_history_enabled"
          @change="
            personalization.updateField({
              key: 'modify_history_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.modify_history_enabled"
      /></label>
    </div>

    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{
        $t('personalization.versionControlDivider')
      }}</span>
    </div>

    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.versioningByDefaultTitle') }}</span
          ><span class="stg-row-desc">{{
            $t('personalization.versioningByDefaultDesc')
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.versioning_enabled_by_default"
          @change="
            personalization.updateField({
              key: 'versioning_enabled_by_default',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.versioning_enabled_by_default"
      /></label>

      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.backupModeTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.backupModeDesc') }}</span></span
        >
        <UiSelect
          :model-value="form.versioning_backup_mode"
          :options="backupModeItems"
          align-end
          @update:model-value="selectVersioningBackupMode"
        />
      </div>

      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.gitWorktreeTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.gitWorktreeDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.git_worktree_enabled"
          @change="
            personalization.updateField({
              key: 'git_worktree_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.git_worktree_enabled"
      /></label>
    </div>
  </section>
</template>

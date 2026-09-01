<script setup lang="ts">
import { computed, inject, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import FancyCheck from '@/components/common/FancyCheck.vue';
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
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  activeDropdown,
  floatingMenuStyle,
  form,
  permissionModeLabel,
  personalization,
  selectDefaultPermissionMode,
  selectDefaultWorkMode,
  selectVersioningBackupMode,
  toggleDropdown,
  versioningBackupModeLabel,
  workModeLabel,
  permissionModeOptions,
  workModeOptions,
  activeTheme
} = ctx;
</script>

<template>
  <section class="settings-page" data-tutorial="personal-page-workspace">
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.defaultPermissionTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.defaultPermissionDesc') }}</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'permission' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('permission')"
                          >
                            {{ permissionModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in permissionModeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.default_permission_mode === option.id }"
                              @click="selectDefaultPermissionMode(option.id)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.defaultWorkModeTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.workRunModeDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'work-mode' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('work-mode')"
                          >
                            {{ workModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in workModeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.default_work_mode === option.id }"
                              @click="selectDefaultWorkMode(option.id)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-action-row sandbox-env-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('sandbox.sectionTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('sandbox.sectionDesc') }}</span>
                        </span>
                        <div class="settings-inline-actions">
                          <span class="settings-mini-status" :class="{ warning: sandboxShowWizard }">{{
                            sandboxStatusText
                          }}</span>
                          <span v-if="sandboxSetup.neverAsk" class="settings-mini-status">{{
                            $t('sandbox.neverAgainSet')
                          }}</span>
                          <button
                            v-if="sandboxSetup.neverAsk"
                            type="button"
                            class="settings-secondary-button"
                            @click="sandboxSetup.resetNeverAsk()"
                          >
                            {{ $t('sandbox.resetNeverAgain') }}
                          </button>
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="sandboxSetup.checking"
                            @click="sandboxSetup.recheck()"
                          >
                            {{ sandboxSetup.checking ? $t('common.refreshing') : $t('sandbox.recheck') }}
                          </button>
                          <button
                            v-if="sandboxShowWizard"
                            type="button"
                            class="settings-primary-button"
                            @click="sandboxSetup.openWizard()"
                          >
                            {{ $t('sandbox.openWizard') }}
                          </button>
                        </div>
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.agentsMdInjectTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.agentsMdInjectDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.agents_md_auto_inject"
                          @change="
                            personalization.updateField({
                              key: 'agents_md_auto_inject',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.agents_md_auto_inject" /></label>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.claudeMdInjectTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.claudeMdInjectDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.claude_md_auto_inject"
                          @change="
                            personalization.updateField({
                              key: 'claude_md_auto_inject',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.claude_md_auto_inject" /></label>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.agentsSkillsScanTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.agentsSkillsScanDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.agents_skills_scan_enabled"
                          @change="
                            personalization.updateField({
                              key: 'agents_skills_scan_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.agents_skills_scan_enabled" /></label>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.modifyHistoryTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.modifyHistoryDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.modify_history_enabled"
                          @change="
                            personalization.updateField({
                              key: 'modify_history_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.modify_history_enabled" /></label>

                      <div class="settings-section-divider">
                        <span class="settings-section-divider__label">{{ $t('personalization.versionControlDivider') }}</span>
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.versioningByDefaultTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.versioningByDefaultDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.versioning_enabled_by_default"
                          @change="
                            personalization.updateField({
                              key: 'versioning_enabled_by_default',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.versioning_enabled_by_default" /></label>

                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.backupModeTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.backupModeDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'versioning-backup-mode' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('versioning-backup-mode')"
                          >
                            {{ versioningBackupModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.versioning_backup_mode === 'shallow' }"
                              @click="selectVersioningBackupMode('shallow')"
                            >
                              <strong>{{ $t('personalization.shallowBackup') }}</strong><span>{{ $t('personalization.shallowBackupDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.versioning_backup_mode === 'full' }"
                              @click="selectVersioningBackupMode('full')"
                            >
                              <strong>{{ $t('personalization.fullBackup') }}</strong><span>{{ $t('personalization.fullBackupDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>

  </section>
</template>

<style scoped>
/* 沙箱环境区块：上下结构（标题描述在上，状态徽标与按钮组整行在下），
   避免默认左右结构中右侧按钮组挤压左侧文案空间 */
.sandbox-env-row {
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.sandbox-env-row .settings-inline-actions {
  justify-content: flex-start;
}
</style>

<script setup lang="ts">
import { inject } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';

defineOptions({ name: 'WorkspaceTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  activeDropdown,
  floatingMenuStyle,
  form,
  goalTokenLimitEnabled,
  permissionModeLabel,
  personalization,
  selectDefaultPermissionMode,
  selectDefaultWorkMode,
  selectVersioningBackupMode,
  toggleDropdown,
  toggleGoalTokenLimit,
  versioningBackupModeLabel,
  workModeLabel,
  permissionModeOptions,
  workModeOptions,
  // 清单外顶层绑定（模板引用）：activeTheme 已在 drawerContext 中提供；
  // clampGoalMaxTurns / clampGoalMaxTokens 为顶层 const 但当前不在 drawerContext 内（详见 report.md）
  activeTheme,
  clampGoalMaxTurns,
  clampGoalMaxTokens
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
                          ><span class="settings-row-title">{{ $t('personalization.defaultRunModeTitle') }}</span
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

                      <div class="settings-section-divider">
                        <span class="settings-section-divider__label">{{ $t('personalization.goalModeDivider') }}</span>
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.goalReviewActiveTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.goalReviewActiveDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.goal_review_mode === 'active'"
                          @change="
                            personalization.updateField({
                              key: 'goal_review_mode',
                              value: $event.target.checked ? 'active' : 'readonly'
                            })
                          " /><FancyCheck :checked="form.goal_review_mode === 'active'" /></label>

                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.goalMaxTurnsTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.goalMaxTurnsDesc') }}</span
                          ></span
                        >
                        <input
                          type="number"
                          class="settings-number-input"
                          min="1"
                          max="100"
                          :value="form.goal_max_turns"
                          @change="
                            personalization.updateField({
                              key: 'goal_max_turns',
                              value: clampGoalMaxTurns($event.target.value)
                            })
                          "
                        />
                      </div>

                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.goalTokenLimitTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.goalTokenLimitDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="goalTokenLimitEnabled"
                          @change="toggleGoalTokenLimit($event.target.checked)" /><FancyCheck :checked="goalTokenLimitEnabled" /></label>

                      <div v-if="goalTokenLimitEnabled" class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.goalTokenLimitValueTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.goalTokenLimitValueDesc') }}</span></span
                        >
                        <input
                          type="number"
                          class="settings-number-input"
                          min="1000"
                          step="1000"
                          :value="form.goal_max_tokens || 100000"
                          @change="
                            personalization.updateField({
                              key: 'goal_max_tokens',
                              value: clampGoalMaxTokens($event.target.value)
                            })
                          "
                        />
                      </div>
  </section>
</template>

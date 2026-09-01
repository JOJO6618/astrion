<script setup lang="ts">
import { inject } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';

defineOptions({ name: 'AppearanceTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  activeDropdown,
  activeTheme,
  blockDisplayLabel,
  blockDisplayOptions,
  compactMessageDisplayLabel,
  compactMessageDisplayOptions,
  currentBlockDisplayMode,
  currentCompactMessageDisplay,
  floatingMenuStyle,
  form,
  handleMinimalExpandHeightLimitedChange,
  handleStackedHideBordersChange,
  locale,
  localeLabel,
  localeOptions,
  minimalExpandHeightLimited,
  personalization,
  selectBlockDisplayMode,
  selectCompactMessageDisplay,
  selectLocaleOption,
  selectThemeOption,
  stackedHideBorders,
  themeLabel,
  themeOptions,
  toggleDropdown
} = ctx;
</script>

<template>
  <section class="settings-page">
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.themeTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.themeDesc') }}</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'theme' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('theme')"
                          >
                            {{ themeLabel }} <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in themeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: activeTheme === option.id }"
                              @click="selectThemeOption(option.id)"
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
                          ><span class="settings-row-title">{{ $t('common.settingsLanguage') }}</span
                          ><span class="settings-row-desc">{{
                            $t('common.settingsLanguageDesc')
                          }}</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'language' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('language')"
                          >
                            {{ localeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in localeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: locale === option.id }"
                              @click="selectLocaleOption(option.id)"
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
                          ><span class="settings-row-title">{{ $t('personalization.groupByWorkspaceTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.groupByWorkspaceDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.group_sidebar_by_workspace"
                          @change="
                            personalization.updateField({
                              key: 'group_sidebar_by_workspace',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.group_sidebar_by_workspace" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.newChatBlankTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.newChatBlankDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.new_chat_button_behavior === 'route'"
                          @change="
                            personalization.updateField({
                              key: 'new_chat_button_behavior',
                              value: $event.target.checked ? 'route' : 'blank'
                            })
                          " /><FancyCheck :checked="form.new_chat_button_behavior === 'route'" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.enhancedToolDisplayTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.enhancedToolDisplayDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.enhanced_tool_display"
                          @change="
                            personalization.updateField({
                              key: 'enhanced_tool_display',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.enhanced_tool_display" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.showStatusAvatarTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.showStatusAvatarDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.show_status_avatar"
                          @change="
                            personalization.updateField({
                              key: 'show_status_avatar',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.show_status_avatar" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.showGitStatusBarTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.showGitStatusBarDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.show_git_status_bar"
                          @change="
                            personalization.updateField({
                              key: 'show_git_status_bar',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.show_git_status_bar" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.autoOpenTerminalTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.autoOpenTerminalDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.auto_open_terminal_panel"
                          @change="
                            personalization.updateField({
                              key: 'auto_open_terminal_panel',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.auto_open_terminal_panel" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.quickDockAutoExpandTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.quickDockAutoExpandDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.quick_dock_auto_expand"
                          @change="
                            personalization.updateField({
                              key: 'quick_dock_auto_expand',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.quick_dock_auto_expand" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.editSummaryLiveTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.editSummaryLiveDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.edit_summary_live_display"
                          @change="
                            personalization.updateField({
                              key: 'edit_summary_live_display',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.edit_summary_live_display" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.filePreviewWrapTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.filePreviewWrapDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.file_preview_auto_wrap"
                          @change="
                            personalization.updateField({
                              key: 'file_preview_auto_wrap',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.file_preview_auto_wrap" /></label>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.blockDisplayModeTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.blockDisplayModeDesc') }}</span></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'block-display' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('block-display')"
                          >
                            {{ blockDisplayLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in blockDisplayOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: currentBlockDisplayMode === option.value }"
                              @click="selectBlockDisplayMode(option.value)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <label
                        v-if="currentBlockDisplayMode === 'stacked'"
                        class="settings-toggle-row"
                      >
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.hideBlockBordersTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.hideBlockBordersDesc') }}</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="stackedHideBorders"
                          @change="handleStackedHideBordersChange($event)"
                        />
                        <FancyCheck :checked="stackedHideBorders" />
                      </label>
                      <label
                        v-if="currentBlockDisplayMode === 'minimal'"
                        class="settings-toggle-row"
                      >
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.minimalExpandHeightTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.minimalExpandHeightDesc') }}</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="minimalExpandHeightLimited"
                          @change="handleMinimalExpandHeightLimitedChange($event)"
                        />
                        <FancyCheck :checked="minimalExpandHeightLimited" />
                      </label>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.compactMessageTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.compactMessageDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'compact-message' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('compact-message')"
                          >
                            {{ compactMessageDisplayLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in compactMessageDisplayOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: currentCompactMessageDisplay === option.value }"
                              @click="selectCompactMessageDisplay(option.value)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
  </section>
</template>
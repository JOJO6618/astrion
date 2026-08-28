<template>
  <transition name="personal-page-fade" appear>
    <div
      class="personal-page-overlay"
      v-if="visible"
      @click="closeDropdown"
      @mousedown="personalization.handleOverlayPressStart($event)"
      @mouseup="personalization.handleOverlayPressEnd($event)"
      @mouseleave.self="personalization.handleOverlayPressCancel"
      @touchstart.self.prevent="personalization.handleOverlayPressStart($event)"
      @touchend="personalization.handleOverlayPressEnd($event)"
      @touchcancel.self="personalization.handleOverlayPressCancel"
    >
      <div class="personal-page-card settings-redesign-card" data-tutorial="personal-card">
        <button
          type="button"
          class="settings-close-button"
          data-tutorial="personal-close"
          :aria-label="$t('personalization.closePersonalSpaceAriaLabel')"
          @click="personalization.closeDrawer()"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>

        <div class="personalization-body settings-redesign-body" v-if="!loading">
          <form class="personal-form settings-redesign-form">
            <div class="settings-redesign-layout">
              <nav class="settings-redesign-tabs" :aria-label="$t('personalization.tabAriaLabel')">
                <button
                  v-for="tab in personalTabs"
                  :key="tab.id"
                  type="button"
                  class="settings-redesign-tab"
                  :data-tutorial="`personal-tab-${tab.id}`"
                  :class="{ active: activeTab === tab.id }"
                  :aria-pressed="activeTab === tab.id"
                  @click.prevent="setActiveTab(tab.id)"
                >
                  <span
                    v-if="tab.id === 'context'"
                    class="settings-tab-icon settings-tab-icon--chat"
                    aria-hidden="true"
                  >
                    <svg viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <path
                        d="M5 6.5c0-1.38 1.12-2.5 2.5-2.5h13c1.38 0 2.5 1.12 2.5 2.5v8.5c0 1.38-1.12 2.5-2.5 2.5h-5.6l-3.4 3.2.6-3.2H7.5c-1.38 0-2.5-1.12-2.5-2.5V6.5z"
                        stroke="currentColor"
                        stroke-width="1.7"
                        stroke-linejoin="round"
                      />
                      <path
                        d="M9 9.5h10"
                        stroke="currentColor"
                        stroke-width="1.7"
                        stroke-linecap="round"
                      />
                      <path
                        d="M9 13h6"
                        stroke="currentColor"
                        stroke-width="1.7"
                        stroke-linecap="round"
                      />
                    </svg>
                  </span>
                  <span
                    v-else
                    class="icon settings-tab-icon"
                    :style="settingsTabIconStyle(tab.icon)"
                    aria-hidden="true"
                  ></span>
                  <span>{{ $t(tab.labelKey) }}</span>
                </button>
              </nav>

              <section class="settings-redesign-content" data-tutorial="personal-content-shell">
                <header class="settings-redesign-content-header">
                  <h2>{{ activeTabLabel }}</h2>
                </header>
                <div class="settings-redesign-title-line" aria-hidden="true"></div>

                <div class="settings-redesign-scroll">
                  <transition name="personal-page-vertical" mode="out-in">
                    <section v-if="activeTab === 'general'" key="general" class="settings-page">
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
                              value: $event.target.checked
                            })
                          "
                        />
                        <FancyCheck :checked="form.auto_generate_title" />
                      </label>
                      <div class="settings-action-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.tutorialTitle') }}</span>
                          <span class="settings-row-desc"
                            >{{ $t('personalization.tutorialDesc') }}</span
                          >
                        </span>
                        <button
                          type="button"
                          class="settings-secondary-button"
                          @click="startTutorial"
                        >
                          {{ $t('personalization.tutorialButton') }}
                        </button>
                      </div>
                      <div class="settings-action-row" v-if="isAppShell">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.appUpdateTitle') }}</span>
                          <span class="settings-row-desc"
                            >{{ $t('personalization.appUpdateDesc', { version: appCurrentVersionText, checked: appUpdateCheckedText }) }}</span
                          >
                        </span>
                        <div class="settings-inline-actions">
                          <span class="settings-mini-status" :class="{ warning: appHasUpdate }">{{
                            appUpdateStateText
                          }}</span>
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="appUpdateChecking"
                            @click="checkAppUpdate"
                          >
                            {{ appUpdateChecking ? $t('personalization.appChecking') : $t('personalization.appCheckUpdate') }}
                          </button>
                          <button
                            v-if="appHasUpdate"
                            type="button"
                            class="settings-primary-button"
                            @click="downloadLatestApp"
                          >
                            {{ $t('common.download') }}
                          </button>
                        </div>
                      </div>
                      <div class="settings-action-row danger-zone">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.logoutTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.logoutDesc') }}</span>
                        </span>
                        <button
                          type="button"
                          class="settings-secondary-button danger"
                          @click="personalization.logout()"
                        >
                          {{ $t('personalization.logoutTitle') }}
                        </button>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'preferences'"
                      key="preferences"
                      class="settings-page"
                      data-tutorial="personal-page-preferences"
                    >
                      <label class="settings-toggle-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.preferencesTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.preferencesDesc') }}</span>
                        </span>
                        <input
                          type="checkbox"
                          :checked="form.enabled"
                          :disabled="toggleUpdating"
                          @change="personalization.toggleEnabled()"
                        />
                        <FancyCheck :checked="form.enabled" />
                      </label>
                      <label class="settings-input-row">
                        <span class="settings-row-title">{{ $t('personalization.selfIdentifyTitle') }}</span>
                        <input
                          type="text"
                          :value="form.self_identify"
                          maxlength="20"
                          :placeholder="$t('personalization.selfIdentifyPlaceholder')"
                          @input="
                            personalization.updateField({
                              key: 'self_identify',
                              value: $event.target.value
                            })
                          "
                          @focus="personalization.clearFeedback()"
                        />
                      </label>
                      <label class="settings-input-row">
                        <span class="settings-row-title">{{ $t('personalization.userNameTitle') }}</span>
                        <input
                          type="text"
                          :value="form.user_name"
                          maxlength="20"
                          :placeholder="$t('personalization.userNamePlaceholder')"
                          @input="
                            personalization.updateField({
                              key: 'user_name',
                              value: $event.target.value
                            })
                          "
                          @focus="personalization.clearFeedback()"
                        />
                      </label>
                      <label class="settings-input-row">
                        <span class="settings-row-title">{{ $t('personalization.professionTitle') }}</span>
                        <input
                          type="text"
                          :value="form.profession"
                          maxlength="20"
                          :placeholder="$t('personalization.professionPlaceholder')"
                          @input="
                            personalization.updateField({
                              key: 'profession',
                              value: $event.target.value
                            })
                          "
                          @focus="personalization.clearFeedback()"
                        />
                      </label>
                      <div class="settings-input-row stackable">
                        <span class="settings-row-title">{{ $t('personalization.toneTitle') }}</span>
                        <div class="settings-input-stack">
                          <input
                            type="text"
                            :value="form.tone"
                            maxlength="20"
                            :placeholder="$t('personalization.tonePlaceholder')"
                            @input="
                              personalization.updateField({
                                key: 'tone',
                                value: $event.target.value
                              })
                            "
                            @focus="personalization.clearFeedback()"
                          />
                          <div class="settings-chip-row">
                            <button
                              v-for="preset in tonePresets"
                              :key="preset"
                              type="button"
                              @click.prevent="personalization.applyTonePreset(preset)"
                            >
                              {{ preset }}
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-textarea-row">
                        <div class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.considerationsTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.considerationsDesc') }}</span>
                        </div>
                        <textarea
                          :value="form.considerations"
                          rows="6"
                          maxlength="2000"
                          :placeholder="$t('personalization.considerationsPlaceholder')"
                          @input="personalization.updateConsiderations($event.target.value)"
                          @focus="personalization.clearFeedback()"
                        ></textarea>
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
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.communicationStyleTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.communicationStyleDesc') }}</span>
                        </span>
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'communication' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('communication')"
                          >
                            {{ communicationStyleLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.communication_style === 'default' }"
                              @click="selectCommunicationStyle('default')"
                            >
                              <strong>{{ $t('personalization.communicationDefault') }}</strong><span>{{ $t('personalization.communicationDefaultDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.communication_style === 'human_like' }"
                              @click="selectCommunicationStyle('human_like')"
                            >
                              <strong>{{ $t('personalization.communicationHumanLike') }}</strong><span>{{ $t('personalization.communicationHumanLikeDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.communication_style === 'auto' }"
                              @click="selectCommunicationStyle('auto')"
                            >
                              <strong>{{ $t('personalization.communicationAuto') }}</strong><span>{{ $t('personalization.communicationAutoDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.conversationContinuityTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.conversationContinuityDesc') }}</span>
                        </span>
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'conversation-continuity' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('conversation-continuity')"
                          >
                            {{ conversationContinuityLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.conversation_continuity === 'high' }"
                              @click="selectConversationContinuity('high')"
                            >
                              <strong>{{ $t('personalization.continuityHigh') }}</strong><span>{{ $t('personalization.continuityHighDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.conversation_continuity === 'medium' }"
                              @click="selectConversationContinuity('medium')"
                            >
                              <strong>{{ $t('personalization.continuityMedium') }}</strong><span>{{ $t('personalization.continuityMediumDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                            <button
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.conversation_continuity === 'low' }"
                              @click="selectConversationContinuity('low')"
                            >
                              <strong>{{ $t('personalization.continuityLow') }}</strong><span>{{ $t('personalization.continuityLowDesc') }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'model'"
                      key="model"
                      class="settings-page"
                      data-tutorial="personal-page-model"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.defaultModelTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.defaultModelDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'model' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('model')"
                          >
                            {{ defaultModelLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in filteredModelOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{
                                selected: form.default_model === option.value,
                                disabled: option.disabled
                              }"
                              :disabled="option.disabled"
                              @click="selectDefaultModel(option.value)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ option.disabled ? $t('personalization.modelDisabled') : option.desc }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.defaultRunModeTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.defaultRunModeDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'run-mode' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('run-mode')"
                          >
                            {{ runModeLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in runModeOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: isRunModeActive(option.value) }"
                              @click="selectDefaultRunMode(option.value)"
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
                          ><span class="settings-row-title">{{ $t('personalization.reasoningEffortTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.reasoningEffortDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'reasoning-effort' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('reasoning-effort')"
                          >
                            {{ reasoningEffortLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in reasoningEffortOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: isEffortActive(option.value) }"
                              @click="selectDefaultReasoningEffort(option.value)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'appearance'"
                      key="appearance"
                      class="settings-page"
                    >
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
                          ><span class="settings-row-title">{{ $t('personalization.useCustomNamesTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.useCustomNamesDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.use_custom_names"
                          @change="
                            personalization.updateField({
                              key: 'use_custom_names',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.use_custom_names" /></label>
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

                    <section
                      v-else-if="activeTab === 'workspace'"
                      key="workspace"
                      class="settings-page"
                      data-tutorial="personal-page-workspace"
                    >
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

                    <section
                      v-else-if="activeTab === 'context'"
                      key="context"
                      class="settings-page"
                    >
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.recentConversationsTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.recentConversationsDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.recent_conversations_prompt_enabled"
                          @change="
                            personalization.updateField({
                              key: 'recent_conversations_prompt_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.recent_conversations_prompt_enabled" /></label>
                      <div
                        class="settings-input-row"
                        v-if="form.recent_conversations_prompt_enabled"
                      >
                        <span class="settings-row-title">{{ $t('personalization.recentConversationsCountTitle') }}</span>
                        <div class="settings-number-row">
                          <input
                            type="number"
                            :min="recentConversationsPromptLimitRange.min"
                            :max="recentConversationsPromptLimitRange.max"
                            :value="form.recent_conversations_prompt_limit"
                            @input="handleRecentConversationsPromptLimitInput"
                            @blur="commitRecentConversationsPromptLimitInput"
                          /><button type="button" @click="restoreRecentConversationsPromptLimit">{{ $t('personalization.restoreDefault') }}
                          </button>
                        </div>
                      </div>
                      <div class="settings-input-row">
                        <span class="settings-row-title">{{ $t('personalization.maxMemoryInjectTitle') }}</span>
                        <div class="settings-number-row">
                          <input
                            type="number"
                            :min="projectMemoryInjectLimitMin"
                            :value="form.project_memory_inject_limit ?? ''"
                            ::placeholder="$t('personalization.noLimitPlaceholder')"
                            @input="handleProjectMemoryInjectLimitInput"
                            @blur="commitProjectMemoryInjectLimitInput"
                          /><button type="button" @click="restoreProjectMemoryInjectLimit">{{ $t('personalization.restoreDefault') }}
                          </button>
                        </div>
                      </div>
                      <div class="settings-group-block">
                        <div class="settings-group-title">
                          <span class="settings-row-title">{{ $t('personalization.compressionStrategyTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.compressionStrategyDesc') }}</span
                          >
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-copy"
                            ><span class="settings-row-title">{{ $t('personalization.autoShallowCompressTitle') }}</span></span
                          ><input
                            type="checkbox"
                            :checked="form.auto_shallow_compress_enabled"
                            @change="
                              personalization.updateField({
                                key: 'auto_shallow_compress_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.auto_shallow_compress_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-copy"
                            ><span class="settings-row-title">{{ $t('personalization.autoDeepCompressTitle') }}</span></span
                          ><input
                            type="checkbox"
                            :checked="form.auto_deep_compress_enabled"
                            @change="
                              personalization.updateField({
                                key: 'auto_deep_compress_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.auto_deep_compress_enabled" /></label>
                        <div class="settings-compression-grid">
                          <label
                            ><span>{{ $t('personalization.shallowTriggerTokens') }}</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_trigger_tokens ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_trigger_tokens',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>{{ $t('personalization.shallowKeepRecentTools') }}</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_keep_recent_tools ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_keep_recent_tools',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>{{ $t('personalization.shallowKeepTurnTools') }}</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_keep_user_turn_tools ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_keep_user_turn_tools',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>{{ $t('personalization.shallowMaxReplace') }}</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_max_replace_per_round ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_max_replace_per_round',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>{{ $t('personalization.shallowToolInterval') }}</span
                            ><input
                              type="number"
                              :value="form.shallow_compress_trigger_tool_calls_interval ?? ''"
                              @input="
                                handleCompressionNumberInput(
                                  'shallow_compress_trigger_tool_calls_interval',
                                  $event
                                )
                              "
                          /></label>
                          <label
                            ><span>{{ $t('personalization.deepTriggerTokens') }}</span
                            ><input
                              type="number"
                              :value="form.deep_compress_trigger_tokens ?? ''"
                              @input="
                                handleCompressionNumberInput('deep_compress_trigger_tokens', $event)
                              "
                          /></label>
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-copy"
                            ><span class="settings-row-title">{{ $t('personalization.deepCompressInjectTitle') }}</span
                            ><span class="settings-row-desc"
                              >{{ $t('personalization.deepCompressInjectDesc') }}</span
                            ></span
                          ><input
                            type="checkbox"
                            :checked="form.deep_compress_form === 'inject'"
                            @change="
                              personalization.updateField({
                                key: 'deep_compress_form',
                                value: $event.target.checked ? 'inject' : 'file'
                              })
                            " /><FancyCheck :checked="form.deep_compress_form === 'inject'" /></label>
                        <div class="settings-inline-actions right">
                          <button
                            type="button"
                            class="settings-secondary-button"
                            @click="restoreCompressionDefaults"
                          >{{ $t('personalization.restoreCompressionDefaults') }}
                          </button>
                        </div>
                      </div>
                    </section>

                    <section v-else-if="activeTab === 'tools'" key="tools" class="settings-page">
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.silentToolDisableTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.silentToolDisableDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.silent_tool_disable"
                          @change="
                            personalization.updateField({
                              key: 'silent_tool_disable',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.silent_tool_disable" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.hideToolApprovalTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.hideToolApprovalDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.hide_tool_approval_panel"
                          @change="
                            personalization.updateField({
                              key: 'hide_tool_approval_panel',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.hide_tool_approval_panel" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.toolIntentTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.toolIntentDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.tool_intent_enabled"
                          @change="
                            personalization.updateField({
                              key: 'tool_intent_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.tool_intent_enabled" /></label>
                      <label class="settings-toggle-row"
                        ><span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.skillHintsTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.skillHintsDesc') }}</span
                          ></span
                        ><input
                          type="checkbox"
                          :checked="form.skill_hints_enabled"
                          @change="
                            personalization.updateField({
                              key: 'skill_hints_enabled',
                              value: $event.target.checked
                            })
                          " /><FancyCheck :checked="form.skill_hints_enabled" /></label>
                      <div class="settings-group-block">
                        <div class="settings-group-title">
                          <span class="settings-row-title">{{ $t('personalization.strictSkillTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.strictSkillDesc') }}</span>
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">{{ $t('personalization.terminalToolsTitle') }}</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_terminal_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_terminal_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_terminal_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">{{ $t('personalization.subAgentToolsTitle') }}</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_sub_agent_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_sub_agent_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_sub_agent_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">{{ $t('personalization.runCommandFgTitle') }}</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_run_command_foreground_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_run_command_foreground_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_run_command_foreground_enabled" /></label>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">{{ $t('personalization.runCommandBgTitle') }}</span
                          ><input
                            type="checkbox"
                            :checked="form.skill_strict_run_command_background_enabled"
                            @change="
                              personalization.updateField({
                                key: 'skill_strict_run_command_background_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.skill_strict_run_command_background_enabled" /></label>
                      </div>
                      <div class="settings-group-block" v-if="skillsCatalog.length">
                        <div class="settings-group-title">
                          <span class="settings-row-title">{{ $t('personalization.availableSkillsTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.availableSkillsDesc') }}</span
                          >
                        </div>
                        <div class="settings-check-grid">
                          <label
                            v-for="skill in skillsCatalog"
                            :key="skill.id"
                            class="settings-toggle-row inner"
                            ><span class="settings-row-title">{{ skill.label }}</span
                            ><input
                              type="checkbox"
                              :checked="form.enabled_skills.includes(skill.id)"
                              @change="personalization.toggleSkill(skill.id)" /><FancyCheck :checked="form.enabled_skills.includes(skill.id)" /></label>
                        </div>
                      </div>
                      <div class="settings-group-block" v-if="toolCategories.length">
                        <div class="settings-group-title">
                          <span class="settings-row-title">{{ $t('personalization.disabledToolCategoriesTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.disabledToolCategoriesDesc') }}</span>
                        </div>
                        <div class="settings-check-grid">
                          <label
                            v-for="category in toolCategories"
                            :key="category.id"
                            class="settings-toggle-row inner"
                            ><span class="settings-row-title">{{ category.label }}</span
                            ><input
                              type="checkbox"
                              :checked="form.disabled_tool_categories.includes(category.id)"
                              @change="toggleCategory(category.id)" /><FancyCheck :checked="form.disabled_tool_categories.includes(category.id)" /></label>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'files'"
                      key="files"
                      class="settings-page"
                      data-tutorial="personal-page-image"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.imageCompressionTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.imageCompressionDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'image' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('image')"
                          >
                            {{ imageCompressionLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in imageCompressionOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.image_compression === option.id }"
                              @click="selectImageCompression(option.id)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'data'"
                      key="data"
                      class="settings-page usage-summary-page"
                      data-tutorial="personal-page-usage"
                    >
                      <div class="usage-summary-card settings-stats-card">
                        <div class="usage-summary-header">
                          <div>
                            <p class="usage-summary-eyebrow">{{ $t('personalization.usageEyebrow') }}</p>
                            <h3>{{ $t('personalization.usageTitle') }}</h3>
                            <p class="usage-summary-desc">{{ $t('personalization.usageDesc') }}
                            </p>
                          </div>
                        </div>
                        <div class="usage-summary-grid usage-summary-grid--tokens">
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageInputLabel') }}</div>
                            <div class="value value--success">
                              {{ formatTokenCount(usageSummary.total_input_tokens) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageOutputLabel') }}</div>
                            <div class="value value--warning">
                              {{ formatTokenCount(usageSummary.total_output_tokens) }}
                            </div>
                          </div>
                        </div>
                        <div class="usage-summary-grid usage-summary-grid--counts">
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageConversationsLabel') }}</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_conversations) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageMessagesLabel') }}</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_user_messages) }}
                            </div>
                          </div>
                          <div class="usage-summary-item">
                            <div class="label">{{ $t('personalization.usageToolsLabel') }}</div>
                            <div class="value">
                              {{ formatTokenCount(usageSummary.total_tools) }}
                            </div>
                          </div>
                        </div>
                        <div class="usage-summary-meta">
                          <span v-if="usageError" class="usage-summary-error">{{ usageError }}</span
                          ><span v-else-if="usageLoading">{{ $t('personalization.usageSyncing') }}</span
                          ><span v-else>{{ $t('personalization.usageUpdated', { time: usageUpdatedText }) }}</span
                          ><button
                            type="button"
                            class="usage-summary-refresh"
                            @click="fetchUsageSummary"
                            :disabled="usageLoading"
                          >
                            {{ usageLoading ? $t('personalization.usageRefreshing') : $t('personalization.usageRefresh') }}
                          </button>
                        </div>
                      </div>
                    </section>

                    <section v-else-if="activeTab === 'voice'" key="voice" class="settings-page">
                      <div class="settings-section" style="margin-bottom: 16px">
                        <p
                          class="settings-section-desc"
                          style="
                            margin: 0;
                            color: var(--text-secondary);
                            font-size: 13px;
                            line-height: 1.6;
                          "
                        >{{ $t('personalization.voiceModelIntro') }}
                        </p>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.voiceModelTitle') }}</span>
                          <span class="settings-row-desc">
                            <template v-if="voiceModelReady">{{ $t('personalization.voiceModelReady') }}</template>
                            <template v-else-if="voiceDownloading"
                              >{{ $t('personalization.voiceModelDownloading', { percent: voiceDownloadPercent, msg: voiceDownloadMsg }) }}</template
                            >
                            <template v-else-if="voiceModelPartial"
                              >{{ $t('personalization.voiceModelPartial') }}</template
                            >
                            <template v-else>{{ $t('personalization.voiceModelNotDownloaded') }}</template>
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px">
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="voiceDownloading"
                            @click="downloadVoiceModel"
                          >
                            {{
                              voiceDownloading
                                ? $t('personalization.voiceDownloadingBtn')
                                : voiceModelReady
                                  ? $t('personalization.voiceRedownload')
                                  : $t('personalization.voiceDownloadModel')
                            }}
                          </button>
                          <button
                            v-if="voiceModelReady || voiceModelPartial"
                            type="button"
                            class="settings-secondary-button"
                            style="color: var(--state-error)"
                            :disabled="voiceDownloading"
                            @click="deleteVoiceModel"
                          >
                            {{ $t('common.delete') }}
                          </button>
                        </div>
                      </div>
                      <div
                        v-if="voiceDownloading"
                        class="voice-download-bar"
                        style="margin: 8px 0 0"
                      >
                        <div class="voice-download-track">
                          <div
                            class="voice-download-fill"
                            :style="{ width: voiceDownloadPercent + '%' }"
                          ></div>
                        </div>
                        <span style="font-size: 11px; color: var(--text-secondary); margin-top: 4px"
                          >{{ voiceDownloadPercent }}%</span
                        >
                      </div>
                    </section>

                    <section
                      v-else-if="activeTab === 'sub-agents'"
                      key="sub-agents"
                      class="settings-page"
                    >
                      <div class="settings-section-desc" style="margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.6">{{ $t('personalization.subAgentsIntro') }}
                      </div>

                      <!-- 压缩阈值配置 -->
                      <div class="settings-action-row" style="margin-bottom: 16px">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.compressThresholdTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.compressThresholdDesc') }}
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px; align-items: center">
                          <input
                            type="number"
                            class="settings-number-input"
                            v-model.number="subAgentCompressThreshold"
                            :min="10000"
                            :step="10000"
                            style="width: 120px"
                          />
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="subAgentSettingsSaving"
                            @click="saveSubAgentSettings"
                          >
                            {{ subAgentSettingsSaving ? $t('personalization.saving') : $t('common.save') }}
                          </button>
                        </div>
                      </div>

                      <!-- 最大执行轮次配置 -->
                      <div class="settings-action-row" style="margin-bottom: 16px">
                        <span class="settings-row-copy">
                          <span class="settings-row-title">{{ $t('personalization.maxTurnsTitle') }}</span>
                          <span class="settings-row-desc">{{ $t('personalization.maxTurnsDesc') }}
                          </span>
                        </span>
                        <div style="display: flex; gap: 6px; align-items: center">
                          <input
                            type="number"
                            class="settings-number-input"
                            v-model.number="subAgentMaxTurns"
                            :min="0"
                            :step="10"
                            placeholder="50"
                            style="width: 120px"
                          />
                          <button
                            type="button"
                            class="settings-secondary-button"
                            :disabled="subAgentSettingsSaving"
                            @click="saveSubAgentSettings"
                          >
                            {{ subAgentSettingsSaving ? $t('personalization.saving') : $t('common.save') }}
                          </button>
                        </div>
                      </div>

                      <!-- 角色列表 -->
                      <div class="settings-section-header" style="margin-bottom: 8px">
                        <span class="settings-section-title">{{ $t('personalization.roleListTitle') }}</span>
                        <button
                          type="button"
                          class="settings-secondary-button"
                          @click="openRoleEditor()"
                        >
                          {{ $t('personalization.newRole') }}
                        </button>
                      </div>

                      <div v-if="subAgentRolesLoading" class="settings-empty-hint">{{ $t('common.loading') }}</div>
                      <div v-else-if="subAgentRoles.length === 0" class="settings-empty-hint">{{ $t('personalization.noRoles') }}
                      </div>

                      <div v-else class="sub-agent-role-list">
                        <div
                          v-for="role in subAgentRoles"
                          :key="role.role_id"
                          class="sub-agent-role-card"
                        >
                          <div class="sub-agent-role-info">
                            <div class="sub-agent-role-name">
                              {{ role.name }}
                              <span
                                class="sub-agent-role-tag"
                                :class="role.is_custom ? 'tag-custom' : 'tag-preset'"
                              >
                                {{ role.is_custom ? $t('personalization.customTag') : $t('personalization.presetTag') }}
                              </span>
                            </div>
                            <div class="sub-agent-role-desc">{{ role.description || $t('personalization.noDescription') }}</div>
                            <div class="sub-agent-role-meta">
                              {{ $t('personalization.roleMetaId', { id: role.role_id, mode: role.thinking_mode }) }}<template v-if="role.model_key">{{ $t('personalization.roleMetaModel', { model: role.model_key }) }}</template>
                            </div>
                          </div>
                          <div class="sub-agent-role-actions">
                            <button
                              type="button"
                              class="settings-secondary-button"
                              @click="openRoleEditor(role)"
                            >{{ $t('personalization.edit') }}</button>
                            <button
                              v-if="role.is_custom"
                              type="button"
                              class="settings-secondary-button danger"
                              @click="deleteRole(role)"
                            >{{ $t('common.delete') }}</button>
                          </div>
                        </div>
                      </div>

                      <!-- 角色编辑弹窗（与个人空间同尺寸） -->
                      <transition name="personal-page-fade" appear>
                        <div
                          v-if="roleEditorOpen"
                          class="role-editor-drawer-overlay"
                          @click="closeDropdown"
                          @mousedown="handleRoleEditorOverlayPressStart"
                          @mouseup="handleRoleEditorOverlayPressEnd"
                        >
                          <div class="role-editor-drawer-card settings-redesign-card" style="padding: 30px 42px 24px; border-radius: 32px;" @click.stop>
                            <div class="role-editor-header">
                              <h2>{{ editingRole ? $t('personalization.roleEditorEditTitle') : $t('personalization.roleEditorCreateTitle') }}</h2>
                              <button type="button" class="settings-secondary-button" @click="closeRoleEditor">{{ $t('common.close') }}</button>
                            </div>
                            <div class="role-editor-body" @click="closeDropdown">
                              <label class="settings-input-row">
                                <span class="settings-row-title">{{ $t('personalization.roleIdTitle') }}</span>
                                <input
                                  v-if="!editingRole"
                                  type="text"
                                  :value="roleForm.role_id"
                                  maxlength="40"
                                  ::placeholder="$t('personalization.roleIdPlaceholder')"
                                  @input="roleForm.role_id = ($event.target as HTMLInputElement).value"
                                />
                                <span v-else class="settings-row-desc" style="text-align: right; font-size: 14px;">{{ roleForm.role_id }}</span>
                              </label>
                              <label class="settings-input-row">
                                <span class="settings-row-title">{{ $t('personalization.roleNameTitle') }}</span>
                                <input
                                  type="text"
                                  :value="roleForm.name"
                                  maxlength="40"
                                  ::placeholder="$t('personalization.roleNamePlaceholder')"
                                  @input="roleForm.name = ($event.target as HTMLInputElement).value"
                                />
                              </label>
                              <label class="settings-input-row">
                                <span class="settings-row-title">{{ $t('personalization.roleDescTitle') }}</span>
                                <input
                                  type="text"
                                  :value="roleForm.description"
                                  maxlength="100"
                                  ::placeholder="$t('personalization.roleDescPlaceholder')"
                                  @input="roleForm.description = ($event.target as HTMLInputElement).value"
                                />
                              </label>
                              <div class="settings-select-row">
                                <span class="settings-row-copy">
                                  <span class="settings-row-title">{{ $t('personalization.thinkingModeTitle') }}</span>
                                  <span class="settings-row-desc">{{ $t('personalization.roleThinkingDesc') }}</span>
                                </span>
                                <div
                                  class="settings-select-wrap"
                                  :class="{ open: activeDropdown === 'role-thinking' }"
                                  @click.stop
                                >
                                  <button
                                    type="button"
                                    class="settings-select-button"
                                    @click="toggleDropdown('role-thinking')"
                                  >
                                    {{ roleForm.thinking_mode === 'thinking' ? 'thinking' : 'fast' }}
                                    <span class="select-chevron" aria-hidden="true"></span>
                                  </button>
                                  <div
                                    :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                                    :style="activeDropdown === 'role-thinking' ? floatingMenuStyle : undefined"
                                  >
                                    <button
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: roleForm.thinking_mode === 'fast' }"
                                      @click="roleForm.thinking_mode = 'fast'; closeDropdown()"
                                    >
                                      <strong>fast</strong><span>{{ $t('personalization.fastResponseMode') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                    <button
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: roleForm.thinking_mode === 'thinking' }"
                                      @click="roleForm.thinking_mode = 'thinking'; closeDropdown()"
                                    >
                                      <strong>thinking</strong><span>{{ $t('personalization.thinkingReasoningMode') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                  </div>
                                </div>
                              </div>
                              <div class="settings-select-row">
                                <span class="settings-row-copy">
                                  <span class="settings-row-title">{{ $t('personalization.modelTitle') }}</span>>
                                  <span class="settings-row-desc">{{ $t('personalization.roleModelEmptyDesc') }}</span>
                                </span>
                                <div
                                  class="settings-select-wrap"
                                  :class="{ open: activeDropdown === 'role-model' }"
                                  @click.stop
                                >
                                  <button
                                    type="button"
                                    class="settings-select-button"
                                    @click="toggleDropdown('role-model')"
                                  >
                                    {{ roleForm.model_key || $t('personalization.defaultModelOption') }}
                                    <span class="select-chevron" aria-hidden="true"></span>
                                  </button>
                                  <div
                                    :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                                    :style="activeDropdown === 'role-model' ? floatingMenuStyle : undefined"
                                  >
                                    <button
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: !roleForm.model_key }"
                                      @click="roleForm.model_key = ''; closeDropdown()"
                                    >
                                      <strong>{{ $t('personalization.defaultModelOption') }}</strong><span>{{ $t('personalization.roleDefaultModelDesc') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                    <button
                                      v-for="m in subAgentModels"
                                      :key="m.name"
                                      type="button"
                                      class="settings-menu-option"
                                      :class="{ selected: roleForm.model_key === m.name }"
                                      @click="roleForm.model_key = m.name; closeDropdown()"
                                    >
                                      <strong>{{ m.name }}</strong><span>{{ m.modes }} · {{ m.multimodal || $t('personalization.textOnly') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                                    </button>
                                  </div>
                                </div>
                              </div>
                              <div class="settings-textarea-row">
                                <span class="settings-row-title">{{ $t('personalization.promptBodyTitle') }}</span>
                                <textarea
                                  :value="roleForm.body_prompt"
                                  ::placeholder="$t('personalization.promptBodyPlaceholder')"
                                  @input="roleForm.body_prompt = ($event.target as HTMLTextAreaElement).value"
                                ></textarea>
                              </div>
                            </div>
                            <div class="role-editor-footer">
                              <button type="button" class="settings-secondary-button" @click="closeRoleEditor">{{ $t('common.cancel') }}</button>
                              <button
                                type="button"
                                class="settings-primary-button"
                                :disabled="roleSaving || !roleForm.role_id || !roleForm.name || !roleForm.body_prompt"
                                @click="saveRole"
                              >
                                {{ roleSaving ? $t('personalization.saving') : $t('common.save') }}
                              </button>
                            </div>
                          </div>
                        </div>
                      </transition>
                    </section>

                    <section
                      v-else-if="activeTab === 'review-agents'"
                      key="review-agents"
                      class="settings-page"
                    >
                      <div class="settings-section-desc" style="margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.6">{{ $t('personalization.reviewIntro') }}
                      </div>

                      <template v-for="agent in reviewAgentDefs" :key="agent.key">
                        <div class="settings-section-divider">
                          <span class="settings-section-divider__label">{{ $t(agent.nameKey) }}</span>
                        </div>
                        <div style="margin: -4px 0 12px; color: var(--text-secondary); font-size: 12px; line-height: 1.5">
                          {{ $t(agent.descKey) }}
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">{{ $t('personalization.modelTitle') }}</span>
                            <span class="settings-row-desc">{{ $t('personalization.reviewModelEmptyDesc') }}</span>
                          </span>
                          <div
                            class="settings-select-wrap"
                            :class="{ open: activeDropdown === `review-model-${agent.key}` }"
                            @click.stop
                          >
                            <button
                              type="button"
                              class="settings-select-button"
                              @click="toggleDropdown(`review-model-${agent.key}`)"
                            >
                              {{ reviewAgentOf(agent.key).model || $t('personalization.defaultModelOption') }}
                              <span class="select-chevron" aria-hidden="true"></span>
                            </button>
                            <div
                              :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                              :style="activeDropdown === `review-model-${agent.key}` ? floatingMenuStyle : undefined"
                            >
                              <button
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: !reviewAgentOf(agent.key).model }"
                                @click="updateReviewAgent(agent.key, { model: '' }); closeDropdown()"
                              >
                                <strong>{{ $t('personalization.defaultModelOption') }}</strong><span>{{ $t('personalization.reviewDefaultModelDesc') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                              <button
                                v-for="m in subAgentModels"
                                :key="m.name"
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: reviewAgentOf(agent.key).model === m.name }"
                                @click="updateReviewAgent(agent.key, { model: m.name }); closeDropdown()"
                              >
                                <strong>{{ m.name }}</strong><span>{{ m.modes }} · {{ m.multimodal || $t('personalization.textOnly') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                            </div>
                          </div>
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">{{ $t('personalization.thinkingModeTitle') }}</span>
                            <span class="settings-row-desc">{{ $t('personalization.reviewThinkingDesc') }}</span>
                          </span>
                          <div
                            class="settings-select-wrap"
                            :class="{ open: activeDropdown === `review-thinking-${agent.key}` }"
                            @click.stop
                          >
                            <button
                              type="button"
                              class="settings-select-button"
                              @click="toggleDropdown(`review-thinking-${agent.key}`)"
                            >
                              {{ reviewAgentOf(agent.key).thinking ? 'thinking' : 'fast' }}
                              <span class="select-chevron" aria-hidden="true"></span>
                            </button>
                            <div
                              :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                              :style="activeDropdown === `review-thinking-${agent.key}` ? floatingMenuStyle : undefined"
                            >
                              <button
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: !reviewAgentOf(agent.key).thinking }"
                                @click="updateReviewAgent(agent.key, { thinking: false }); closeDropdown()"
                              >
                                <strong>fast</strong><span>{{ $t('personalization.fastResponseMode') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                              <button
                                type="button"
                                class="settings-menu-option"
                                :class="{ selected: reviewAgentOf(agent.key).thinking }"
                                @click="updateReviewAgent(agent.key, { thinking: true }); closeDropdown()"
                              >
                                <strong>thinking</strong><span>{{ $t('personalization.thinkingReasoningMode') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                              </button>
                            </div>
                          </div>
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">{{ $t('personalization.timeoutTitle') }}</span>
                            <span class="settings-row-desc">{{ $t('personalization.timeoutDesc') }}</span>
                          </span>
                          <input
                            type="number"
                            class="settings-number-input"
                            min="5"
                            max="3600"
                            :value="reviewAgentOf(agent.key).timeout_seconds"
                            @change="updateReviewAgentInt(agent.key, 'timeout_seconds', ($event.target as HTMLInputElement).value, 5, 3600)"
                          />
                        </div>

                        <div class="settings-select-row">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">{{ $t('personalization.maxRoundsTitle') }}</span>
                            <span class="settings-row-desc">{{ $t('personalization.maxRoundsDesc') }}</span>
                          </span>
                          <input
                            type="number"
                            class="settings-number-input"
                            min="1"
                            max="50"
                            :value="reviewAgentOf(agent.key).max_rounds"
                            @change="updateReviewAgentInt(agent.key, 'max_rounds', ($event.target as HTMLInputElement).value, 1, 50)"
                          />
                        </div>

                        <div class="settings-select-row" style="margin-bottom: 8px">
                          <span class="settings-row-copy">
                            <span class="settings-row-title">{{ $t('personalization.commandTimeoutTitle') }}</span>
                            <span class="settings-row-desc">{{ $t('personalization.commandTimeoutDesc') }}</span>
                          </span>
                          <input
                            type="number"
                            class="settings-number-input"
                            min="1"
                            max="600"
                            :value="reviewAgentOf(agent.key).max_command_timeout"
                            @change="updateReviewAgentInt(agent.key, 'max_command_timeout', ($event.target as HTMLInputElement).value, 1, 600)"
                          />
                        </div>
                      </template>
                    </section>

                    <section
                      key="admin"
                      class="settings-page admin-monitor-page"
                    >
                      <div class="settings-action-row" v-if="showMcpConfigEntry">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.mcpConfigTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.mcpConfigDesc') }}</span
                          ></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openMcpConfig"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.adminMonitorTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.adminMonitorDesc') }}</span
                          ></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openAdminPanel"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.apiAdminTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.apiAdminDesc') }}</span></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openApiAdmin"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                      <div class="settings-action-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.customToolsAdminTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.customToolsAdminDesc') }}</span></span
                        ><button
                          type="button"
                          class="settings-secondary-button"
                          @click="openCustomTools"
                        >
                          {{ $t('personalization.open') }}
                        </button>
                      </div>
                    </section>
                  </transition>
                </div>

                <div class="settings-save-bar">
                  <div class="personal-status-group">
                    <transition name="personal-status-fade"
                      ><span class="status success" v-if="status">{{ status }}</span></transition
                    ><transition name="personal-status-fade"
                      ><span class="status error" v-if="error">{{ error }}</span></transition
                    >
                  </div>
                </div>
              </section>
            </div>
          </form>
        </div>
        <div class="personalization-loading" v-else>{{ $t('personalization.loadingPersonalization') }}</div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick, onBeforeUnmount } from 'vue';
import { storeToRefs } from 'pinia';
import FancyCheck from '@/components/common/FancyCheck.vue';
import { usePersonalizationStore } from '@/stores/personalization';
import type { ReviewAgentKey, ReviewAgentSetting } from '@/stores/personalization';
import { useResourceStore } from '@/stores/resource';
import { useUiStore } from '@/stores/ui';
import { usePolicyStore } from '@/stores/policy';
import { useTutorialStore } from '@/stores/tutorial';
import { useModelStore } from '@/stores/model';
import { formatTokenCount } from '@/utils/formatters';
import { ICONS } from '@/utils/icons';
import { useTheme } from '@/utils/theme';
import type { ThemeKey } from '@/utils/theme';
import { t, currentLocale, useLocale } from '@/locales';
import type { LocaleKey } from '@/locales';

defineOptions({ name: 'PersonalizationDrawer' });

const personalization = usePersonalizationStore();
const resourceStore = useResourceStore();
const uiStore = useUiStore();
const tutorialStore = useTutorialStore();
const {
  visible,
  loading,
  form,
  tonePresets,
  status,
  error,
  saving,
  toggleUpdating,
  toolCategories,
  skillsCatalog,
  recentConversationsPromptLimitRange,
  projectMemoryInjectLimitMin,
  experiments
} = storeToRefs(personalization);

// ---- 目标模式设置辅助 ----
const goalTokenLimitEnabled = computed(
  () => typeof form.value.goal_max_tokens === 'number' && form.value.goal_max_tokens > 0
);

const clampGoalMaxTurns = (raw: any): number => {
  const n = Math.round(Number(raw));
  if (!Number.isFinite(n)) return 5;
  return Math.min(100, Math.max(1, n));
};

const clampGoalMaxTokens = (raw: any): number => {
  const n = Math.round(Number(raw));
  if (!Number.isFinite(n)) return 100000;
  return Math.min(100000000, Math.max(1000, n));
};

const toggleGoalTokenLimit = (enabled: boolean) => {
  personalization.updateField({
    key: 'goal_max_tokens',
    value: enabled ? form.value.goal_max_tokens || 100000 : null
  });
};

type IconKey = keyof typeof ICONS;

type PersonalTab =
  | 'general'
  | 'preferences'
  | 'model'
  | 'appearance'
  | 'workspace'
  | 'context'
  | 'tools'
  | 'files'
  | 'data'
  | 'voice'
  | 'sub-agents'
  | 'review-agents'
  | 'admin';

const baseTabs = [
  { id: 'general', labelKey: 'personalization.tabGeneral', icon: 'settings' },
  { id: 'preferences', labelKey: 'personalization.tabPreferences', icon: 'userPen' },
  { id: 'model', labelKey: 'personalization.tabModel', icon: 'brainCog' },
  { id: 'appearance', labelKey: 'personalization.tabAppearance', icon: 'monitor' },
  { id: 'workspace', labelKey: 'personalization.tabWorkspace', icon: 'folder' },
  { id: 'context', labelKey: 'personalization.tabContext', icon: 'chatBubble' },
  { id: 'tools', labelKey: 'personalization.tabTools', icon: 'wrench' },
  { id: 'files', labelKey: 'personalization.tabFiles', icon: 'file' },
  { id: 'data', labelKey: 'personalization.tabData', icon: 'layers' },
  { id: 'voice', labelKey: 'personalization.tabVoice', icon: 'mic' },
  { id: 'sub-agents', labelKey: 'personalization.tabSubAgents', icon: 'bot' },
  { id: 'review-agents', labelKey: 'personalization.tabReviewAgents', icon: 'checkbox' }
] as const satisfies ReadonlyArray<{ id: PersonalTab; labelKey: string; icon: IconKey }>;

const sessionRole = ref('');
const sessionHostMode = ref(false);

const isAdmin = computed(() => {
  const quotaRole = (resourceStore.usageQuota.role || '').toLowerCase();
  const loginRole = (sessionRole.value || '').toLowerCase();
  return quotaRole === 'admin' || loginRole === 'admin';
});
const isHostMode = computed(() => {
  const mode = (resourceStore.containerStatus?.mode || '').toLowerCase();
  if (mode) {
    return mode === 'host';
  }
  return sessionHostMode.value;
});
const showMcpConfigEntry = computed(() => isAdmin.value && isHostMode.value);
const isAppShell = computed(() => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has('app_shell')) return true;
  return Boolean((window as any)?.AndroidThemeBridge);
});

const personalTabs = computed(() => {
  const tabs: Array<{ id: PersonalTab; labelKey: string; icon: IconKey }> = [...baseTabs];
  if (isAdmin.value) {
    tabs.push({ id: 'admin', labelKey: 'personalization.tabAdmin', icon: 'wrench' });
  }
  return tabs;
});

const activeTab = ref<PersonalTab>('general');
const activeDropdown = ref<string | null>(null);
const floatingMenuStyle = ref<Record<string, string>>({});

const activeTabLabel = computed(() => {
  void currentLocale.value;
  const tab = personalTabs.value.find((tab) => tab.id === activeTab.value);
  return tab ? t(tab.labelKey) : t('personalization.tabGeneral');
});

// ── 语音模型下载 ──
const voiceModelReady = ref(false);
const voiceModelPartial = ref(false); // 文件存在但不完整
const voiceDownloading = ref(false);
const voiceDownloadPercent = ref(0);
const voiceDownloadMsg = ref('');

const checkVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (bridge) {
    try {
      if (typeof bridge.isModelReady === 'function') {
        voiceModelReady.value = bridge.isModelReady();
      }
      if (!voiceModelReady.value && typeof bridge.isModelPartial === 'function') {
        voiceModelPartial.value = bridge.isModelPartial();
      }
    } catch (_) {
      /* ignore */
    }
  }
};

const downloadVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (!bridge) {
    alert(t('personalization.voiceAppOnlyAlert'));
    return;
  }
  voiceDownloading.value = true;
  voiceDownloadPercent.value = 0;
  voiceDownloadMsg.value = t('personalization.voicePreparing');
  voiceModelPartial.value = false;

  (window as any).__onVoiceDownloadProgress = (pct: number, msg: string) => {
    voiceDownloadPercent.value = pct;
    voiceDownloadMsg.value = msg;
    if (pct >= 100) {
      voiceDownloading.value = false;
      voiceModelReady.value = true;
      voiceModelPartial.value = false;
    }
  };
  (window as any).__onVoiceModelReady = () => {
    voiceModelReady.value = true;
    voiceDownloading.value = false;
    voiceModelPartial.value = false;
  };

  bridge.downloadModel();
};

const deleteVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (!bridge) return;
  if (typeof bridge.deleteModel === 'function') {
    bridge.deleteModel();
  }
  voiceModelReady.value = false;
  voiceModelPartial.value = false;
  voiceDownloadPercent.value = 0;
  voiceDownloadMsg.value = '';
};

// 打开个人空间时检查模型状态
checkVoiceModel();

const updateFloatingMenuPosition = async () => {
  if (!activeDropdown.value || typeof window === 'undefined') {
    floatingMenuStyle.value = {};
    return;
  }
  await nextTick();
  const button = document.querySelector<HTMLElement>(
    '.settings-select-wrap.open .settings-select-button'
  );
  const menu = document.querySelector<HTMLElement>(
    '.settings-select-wrap.open .settings-floating-menu'
  );
  if (!button) {
    return;
  }
  const rect = button.getBoundingClientRect();
  const menuWidth = Math.min(300, Math.max(240, window.innerWidth - 32));
  const left = Math.max(16, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 16));

  const padding = 16;
  const gap = 10;

  // 测量单个选项高度，计算 4 选项固定 max-height
  const optionEl = menu?.querySelector<HTMLElement>('.settings-menu-option');
  const optionHeight = optionEl ? optionEl.getBoundingClientRect().height : 44;
  const menuPadding = 12; // menu padding 6px top + 6px bottom
  const fixedMaxHeight = Math.round(optionHeight * 4) + menuPadding;

  // 先限制菜单高度为固定值，再次获取实际高度
  if (menu) {
    menu.style.maxHeight = `${fixedMaxHeight}px`;
  }
  // 重新读取受限后的菜单高度
  const menuHeight = menu?.getBoundingClientRect().height || fixedMaxHeight;

  const spaceBelow = window.innerHeight - rect.bottom - padding;
  const spaceAbove = rect.top - padding;

  let top: number;
  let maxHeight: number | undefined;

  if (spaceBelow >= menuHeight) {
    // 下方空间足够完整显示
    top = rect.bottom + gap;
  } else if (spaceAbove >= menuHeight) {
    // 上方空间足够完整显示，翻转到上方
    top = rect.top - menuHeight - gap;
  } else if (spaceBelow >= spaceAbove) {
    // 上下都不够，下方空间相对更大：限制高度到可用空间，贴按钮向下
    const availableHeight = Math.max(80, spaceBelow - gap);
    maxHeight = availableHeight;
    top = rect.bottom + gap;
  } else {
    // 上方空间相对更大：限制高度到可用空间，翻转到上方
    const availableHeight = Math.max(80, spaceAbove - gap);
    maxHeight = availableHeight;
    top = Math.max(padding, rect.top - availableHeight - gap);
  }

  floatingMenuStyle.value = {
    position: 'fixed',
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    right: 'auto',
    width: `${Math.round(menuWidth)}px`,
    zIndex: '2147483647',
    ...(maxHeight !== undefined ? { maxHeight: `${Math.round(maxHeight)}px` } : {})
  };
};

const toggleDropdown = async (key: string) => {
  activeDropdown.value = activeDropdown.value === key ? null : key;
  await updateFloatingMenuPosition();
};

const closeDropdown = () => {
  activeDropdown.value = null;
  floatingMenuStyle.value = {};
};

const settingsTabIconStyle = (icon: IconKey) => ({
  '--icon-src': `url(${ICONS[icon]})`
});

onMounted(() => {
  window.addEventListener('resize', updateFloatingMenuPosition);
  window.addEventListener('scroll', updateFloatingMenuPosition, true);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateFloatingMenuPosition);
  window.removeEventListener('scroll', updateFloatingMenuPosition, true);
});

type RunModeValue = 'fast' | 'thinking' | null;
type ReasoningEffortValue = 'low' | 'medium' | 'high' | 'xhigh' | 'max' | null;
type PermissionModeValue = 'readonly' | 'approval' | 'auto_approval' | 'unrestricted';
type CompressionField =
  | 'shallow_compress_trigger_tokens'
  | 'shallow_compress_keep_recent_tools'
  | 'shallow_compress_keep_user_turn_tools'
  | 'shallow_compress_max_replace_per_round'
  | 'shallow_compress_trigger_tool_calls_interval'
  | 'deep_compress_trigger_tokens';

const runModeOptions: Array<{
  id: string;
  labelKey: string;
  descKey: string;
  value: RunModeValue;
}> = [
  { id: 'fast', labelKey: 'personalization.runModeFast', descKey: 'personalization.runModeFastDesc', value: 'fast' },
  { id: 'thinking', labelKey: 'personalization.runModeThinking', descKey: 'personalization.runModeThinkingDesc', value: 'thinking' }
];

const reasoningEffortOptions: Array<{
  id: string;
  labelKey: string;
  descKey: string;
  value: ReasoningEffortValue;
}> = [
  { id: 'default', labelKey: 'personalization.effortDefault', descKey: 'personalization.effortDefaultDesc', value: null },
  { id: 'low', labelKey: 'personalization.effortLow', descKey: 'personalization.effortLowDesc', value: 'low' },
  { id: 'medium', labelKey: 'personalization.effortMedium', descKey: 'personalization.effortMediumDesc', value: 'medium' },
  { id: 'high', labelKey: 'personalization.effortHigh', descKey: 'personalization.effortHighDesc', value: 'high' },
  { id: 'xhigh', labelKey: 'personalization.effortXhigh', descKey: 'personalization.effortXhighDesc', value: 'xhigh' },
  { id: 'max', labelKey: 'personalization.effortMax', descKey: 'personalization.effortMaxDesc', value: 'max' }
];

const permissionModeOptions: Array<{
  id: PermissionModeValue;
  labelKey: string;
  descKey: string;
}> = [
  { id: 'readonly', labelKey: 'personalization.permissionReadonly', descKey: 'personalization.permissionReadonlyDesc' },
  { id: 'approval', labelKey: 'personalization.permissionApproval', descKey: 'personalization.permissionApprovalDesc' },
  {
    id: 'auto_approval',
    labelKey: 'personalization.permissionAutoApproval',
    descKey: 'personalization.permissionAutoApprovalDesc'
  },
  { id: 'unrestricted', labelKey: 'personalization.permissionUnrestricted', descKey: 'personalization.permissionUnrestrictedDesc' }
];

type WorkModeValue = 'plan' | 'ask' | 'execute';
const workModeOptions: Array<{
  id: WorkModeValue;
  labelKey: string;
  descKey: string;
}> = [
  { id: 'plan', labelKey: 'personalization.workModePlan', descKey: 'personalization.workModePlanDesc' },
  { id: 'ask', labelKey: 'personalization.workModeAsk', descKey: 'personalization.workModeAskDesc' },
  { id: 'execute', labelKey: 'personalization.workModeExecute', descKey: 'personalization.workModeExecuteDesc' }
];

const policyStore = usePolicyStore();
const modelStore = useModelStore();

const filteredModelOptions = computed(() => {
  void currentLocale.value;
  return (modelStore.models || []).map((opt: any) => {
    const multimodal = String(opt.multimodal || 'none');
    return {
      id: opt.key,
      value: opt.key,
      label: opt.label,
      desc: opt.description || '',
      badge: multimodal === 'image,video' ? t('personalization.badgeTextOnly') : opt.thinkingOnly ? t('personalization.badgeThinking') : undefined,
      thinkingOnly: !!opt.thinkingOnly,
      fastOnly: !!opt.fastOnly,
      disabled: policyStore.disabledModelSet.has(opt.key)
    };
  });
});

const imageCompressionOptions = [
  { id: 'original', labelKey: 'personalization.imageOriginal', descKey: 'personalization.imageOriginalDesc' },
  { id: '1080p', labelKey: 'personalization.image1080p', descKey: 'personalization.image1080pDesc' },
  { id: '720p', labelKey: 'personalization.image720p', descKey: 'personalization.image720pDesc' },
  { id: '540p', labelKey: 'personalization.image540p', descKey: 'personalization.image540pDesc' }
] as const;

const defaultModelLabel = computed(() => {
  void currentLocale.value;
  return (
    filteredModelOptions.value.find((option: any) => option.value === form.value.default_model)
      ?.label || t('common.unset')
  );
});

const runModeLabel = computed(() => {
  void currentLocale.value;
  const found = runModeOptions.find((option) => isRunModeActive(option.value));
  return found ? t(found.labelKey) : t('common.unset');
});

const isEffortActive = (value: ReasoningEffortValue) => {
  if (value === null) {
    return !form.value.default_reasoning_effort;
  }
  return form.value.default_reasoning_effort === value;
};

const reasoningEffortLabel = computed(() => {
  void currentLocale.value;
  const found = reasoningEffortOptions.find((option) => isEffortActive(option.value));
  return found ? t(found.labelKey) : t('personalization.effortDefault');
});

const selectDefaultReasoningEffort = (value: ReasoningEffortValue) => {
  personalization.setDefaultReasoningEffort(value);
  closeDropdown();
};

const permissionModeLabel = computed(() => {
  void currentLocale.value;
  const found = permissionModeOptions.find((option) => option.id === form.value.default_permission_mode);
  return found ? t(found.labelKey) : t('common.unset');
});

const workModeLabel = computed(() => {
  void currentLocale.value;
  const found = workModeOptions.find((option) => option.id === form.value.default_work_mode);
  return found ? t(found.labelKey) : t('common.unset');
});

const versioningBackupModeLabel = computed(() => {
  void currentLocale.value;
  if (form.value.versioning_backup_mode === 'full') return t('personalization.fullBackup');
  return t('personalization.shallowBackup');
});

const imageCompressionLabel = computed(() => {
  void currentLocale.value;
  const found = imageCompressionOptions.find((option) => option.id === form.value.image_compression);
  return found ? t(found.labelKey) : t('common.unset');
});

const communicationStyleLabel = computed(() => {
  void currentLocale.value;
  if (form.value.communication_style === 'human_like') return t('personalization.communicationHumanLike');
  if (form.value.communication_style === 'auto') return t('personalization.communicationAuto');
  return t('personalization.communicationDefault');
});

const conversationContinuityLabel = computed(() => {
  void currentLocale.value;
  if (form.value.conversation_continuity === 'high') return t('personalization.continuityHigh');
  if (form.value.conversation_continuity === 'low') return t('personalization.continuityLow');
  return t('personalization.continuityMedium');
});

const currentBlockDisplayMode = computed(() => experiments.value.blockDisplayMode);

const stackedHideBorders = computed(() => form.value.stacked_hide_borders);
const minimalExpandHeightLimited = computed(() => form.value.minimal_expand_height_limited);

const blockDisplayLabel = computed(() => {
  void currentLocale.value;
  const found = blockDisplayOptions.find((option) => option.value === currentBlockDisplayMode.value);
  return found ? t(found.labelKey) : t('personalization.blockDisplayStacked');
});

const currentCompactMessageDisplay = computed(() => form.value.compact_message_display || 'full');

const compactMessageDisplayLabel = computed(() => {
  void currentLocale.value;
  const found = compactMessageDisplayOptions.find(
    (option) => option.value === currentCompactMessageDisplay.value
  );
  return found ? t(found.labelKey) : t('personalization.compactMessageFull');
});

const usageSummary = ref({
  total_input_tokens: 0,
  total_output_tokens: 0,
  total_conversations: 0,
  total_user_messages: 0,
  total_tools: 0
});
const usageLoading = ref(false);
const usageError = ref('');
const usageUpdatedAt = ref<string | null>(null);

const usageUpdatedText = computed(() => {
  void currentLocale.value;
  if (!usageUpdatedAt.value) {
    return t('personalization.usageNeverRefreshed');
  }
  const date = new Date(usageUpdatedAt.value);
  if (Number.isNaN(date.getTime())) {
    return t('personalization.usageUnknownTime');
  }
  return date.toLocaleString(currentLocale.value === 'en-US' ? 'en-US' : 'zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
});

const appCurrentVersionCode = ref<number | null>(null);
const appCurrentVersionName = ref<string>('');
const appUpdateChecking = ref(false);
const appUpdateError = ref('');
const appUpdateCheckedAt = ref<string | null>(null);
const appUpdateInfo = ref<{
  latestVersionCode: number;
  latestVersionName: string;
  apkUrl: string;
  fileSizeBytes: number;
  changelog?: string;
  publishedAt?: string | null;
  hasUpdate?: boolean | null;
} | null>(null);

if (typeof window !== 'undefined') {
  const params = new URLSearchParams(window.location.search);
  const vcRaw = params.get('app_vc') || '';
  const vnRaw = params.get('app_vn') || '';
  const bridge = (window as any)?.AndroidThemeBridge;
  const bridgeVcRaw =
    bridge && typeof bridge.getAppVersionCode === 'function'
      ? String(bridge.getAppVersionCode() || '')
      : '';
  const bridgeVnRaw =
    bridge && typeof bridge.getAppVersionName === 'function'
      ? String(bridge.getAppVersionName() || '')
      : '';
  // App 端优先使用 Bridge 实时版本，避免 WebView 恢复旧 URL 参数导致显示过期版本号
  const finalVcRaw = bridgeVcRaw || vcRaw;
  const finalVnRaw = bridgeVnRaw || vnRaw;
  appCurrentVersionCode.value = /^\d+$/.test(finalVcRaw) ? Number(finalVcRaw) : null;
  appCurrentVersionName.value = finalVnRaw || '';
}

const hydrateAppVersionFromBridge = () => {
  if (typeof window === 'undefined') return;
  const bridge = (window as any)?.AndroidThemeBridge;
  if (!bridge) return;
  try {
    const vcRaw =
      typeof bridge.getAppVersionCode === 'function'
        ? String(bridge.getAppVersionCode() || '')
        : '';
    const vnRaw =
      typeof bridge.getAppVersionName === 'function'
        ? String(bridge.getAppVersionName() || '')
        : '';
    if (vcRaw && /^\d+$/.test(vcRaw)) {
      appCurrentVersionCode.value = Number(vcRaw);
    }
    if (vnRaw) {
      appCurrentVersionName.value = vnRaw;
    }
  } catch {
    // ignore bridge read failures
  }
};

const appCurrentVersionText = computed(() => {
  void currentLocale.value;
  return appCurrentVersionName.value || t('personalization.appVersionUnknown');
});

const appHasUpdate = computed(() => {
  if (!appUpdateInfo.value) return false;
  if (typeof appUpdateInfo.value.hasUpdate === 'boolean') return appUpdateInfo.value.hasUpdate;
  if (appCurrentVersionCode.value == null) return false;
  return Number(appUpdateInfo.value.latestVersionCode || 0) > appCurrentVersionCode.value;
});

const appDownloadUrl = computed(() => appUpdateInfo.value?.apkUrl || '');

const appUpdateStateText = computed(() => {
  void currentLocale.value;
  if (!appUpdateInfo.value) return t('personalization.appUpdateNotChecked');
  return appHasUpdate.value ? t('personalization.appUpdateFound') : t('personalization.appUpdateLatest');
});

const appUpdateCheckedText = computed(() => {
  void currentLocale.value;
  if (!appUpdateCheckedAt.value) return t('personalization.appUpdateNeverChecked');
  const d = new Date(appUpdateCheckedAt.value);
  if (Number.isNaN(d.getTime())) return t('personalization.appUpdateJustChecked');
  const formatted = d.toLocaleString(currentLocale.value === 'en-US' ? 'en-US' : 'zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
  return t('personalization.appUpdateCheckedAt', { time: formatted });
});

const fetchUsageSummary = async () => {
  if (usageLoading.value) {
    return;
  }
  usageLoading.value = true;
  usageError.value = '';
  try {
    const response = await fetch('/api/conversations/statistics');
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || payload.message || t('personalization.usageFetchFailed'));
    }
    const data = payload.data || {};
    const tokenStats = data.token_statistics || {};
    usageSummary.value = {
      total_input_tokens: Number(tokenStats.total_input_tokens || 0),
      total_output_tokens: Number(tokenStats.total_output_tokens || 0),
      total_conversations: Number(data.total_conversations || 0),
      total_user_messages: Number(data.total_user_messages || 0),
      total_tools: Number(data.total_tools || 0)
    };
    usageUpdatedAt.value = new Date().toISOString();
  } catch (error: any) {
    usageError.value = error?.message || t('personalization.usageFetchFailed');
  } finally {
    usageLoading.value = false;
  }
};

const checkAppUpdate = async () => {
  if (appUpdateChecking.value) return;
  hydrateAppVersionFromBridge();
  appUpdateChecking.value = true;
  appUpdateError.value = '';
  try {
    const params = new URLSearchParams();
    if (appCurrentVersionCode.value != null) {
      params.set('currentVersionCode', String(appCurrentVersionCode.value));
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    const resp = await fetch(`/api/app/version${suffix}`);
    const payload = await resp.json();
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || t('personalization.appUpdateFailed'));
    }
    const data = payload?.data || {};
    appUpdateInfo.value = {
      latestVersionCode: Number(data.latestVersionCode || 0),
      latestVersionName: String(data.latestVersionName || ''),
      apkUrl: String(data.apkUrl || ''),
      fileSizeBytes: Number(data.fileSizeBytes || 0),
      changelog: String(data.changelog || ''),
      publishedAt: data.publishedAt || null,
      hasUpdate: typeof data.hasUpdate === 'boolean' ? data.hasUpdate : null
    };
    appUpdateCheckedAt.value = new Date().toISOString();
  } catch (error: any) {
    appUpdateError.value = error?.message || t('personalization.appUpdateFailed');
  } finally {
    appUpdateChecking.value = false;
  }
};

const downloadLatestApp = () => {
  const url = appDownloadUrl.value;
  if (!url) {
    appUpdateError.value = t('personalization.appUpdateNoUrl');
    return;
  }
  window.location.href = url;
};

onMounted(async () => {
  hydrateAppVersionFromBridge();
  try {
    const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const payload = await resp.json();
    const snapshot = payload?.session || {};
    sessionRole.value = String(snapshot.role || '');
    sessionHostMode.value = !!snapshot.host_mode;
  } catch (_err) {
    sessionRole.value = '';
  }
});

watch(
  () => [activeTab.value, visible.value],
  ([tab, isVisible]) => {
    if (isVisible && tab === 'data') {
      fetchUsageSummary();
    }
    if (isVisible && tab === 'sub-agents') {
      loadSubAgentRoles();
      loadSubAgentSettings();
      loadSubAgentModels();
    }
    if (isVisible && tab === 'review-agents') {
      loadSubAgentModels();
    }
    if (
      isVisible &&
      tab === 'general' &&
      isAppShell.value &&
      !appUpdateInfo.value &&
      !appUpdateChecking.value
    ) {
      checkAppUpdate();
    }
  }
);

// ----- 子智能体管理 -----
const subAgentRoles = ref<any[]>([]);
const subAgentRolesLoading = ref(false);
const subAgentCompressThreshold = ref(150000);
/** 子智能体最大执行轮次（仅传统后台子智能体；多智能体成员不受限）：null/'' = 默认 50；0 = 无上限；正整数 = 该值 */
const subAgentMaxTurns = ref<number | null>(null);
const subAgentSettingsSaving = ref(false);
const subAgentModels = ref<any[]>([]);
const roleEditorOpen = ref(false);
const editingRole = ref<any>(null);
const roleSaving = ref(false);
const roleForm = ref({
  role_id: '',
  name: '',
  description: '',
  body_prompt: '',
  thinking_mode: 'fast',
  model_key: ''
});

const loadSubAgentRoles = async () => {
  subAgentRolesLoading.value = true;
  try {
    const resp = await fetch('/api/multiagent/roles', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success) {
      subAgentRoles.value = data.roles || [];
    }
  } catch (e) {
    // 静默处理
  } finally {
    subAgentRolesLoading.value = false;
  }
};

const loadSubAgentModels = async () => {
  try {
    const resp = await fetch('/api/multiagent/models', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success) {
      subAgentModels.value = data.models || [];
    }
  } catch (e) {
    // 静默处理
  }
};

// ----- 审核智能体（模型与运行参数统一配置，模型库复用子智能体模型列表） -----
const reviewAgentDefs: Array<{ key: ReviewAgentKey; nameKey: string; descKey: string }> = [
  { key: 'auto_approval', nameKey: 'personalization.reviewAgentAutoApproval', descKey: 'personalization.reviewAgentAutoApprovalDesc' },
  { key: 'goal_review', nameKey: 'personalization.reviewAgentGoalReview', descKey: 'personalization.reviewAgentGoalReviewDesc' },
  { key: 'workflow_review', nameKey: 'personalization.reviewAgentWorkflowReview', descKey: 'personalization.reviewAgentWorkflowReviewDesc' }
];

const reviewAgentOf = (key: ReviewAgentKey): ReviewAgentSetting => {
  const agents = (form.value as any).review_agents;
  return agents?.[key] || { model: '', thinking: false, timeout_seconds: 60, max_rounds: 3, max_command_timeout: 60 };
};

const updateReviewAgent = (key: ReviewAgentKey, patch: Partial<ReviewAgentSetting>) => {
  const agents = { ...((form.value as any).review_agents || {}) };
  agents[key] = { ...reviewAgentOf(key), ...patch };
  personalization.updateField({ key: 'review_agents', value: agents });
};

const updateReviewAgentInt = (key: ReviewAgentKey, field: 'timeout_seconds' | 'max_rounds' | 'max_command_timeout', raw: string, lo: number, hi: number) => {
  const parsed = parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return;
  updateReviewAgent(key, { [field]: Math.max(lo, Math.min(parsed, hi)) });
};

const loadSubAgentSettings = async () => {
  try {
    const resp = await fetch('/api/multiagent/settings', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success && data.settings) {
      subAgentCompressThreshold.value = data.settings.sub_agent_compress_threshold_tokens || 150000;
      // 未设置时为 null，输入框留空（placeholder 提示默认 50）
      subAgentMaxTurns.value = data.settings.sub_agent_max_turns ?? null;
    }
  } catch (e) {
    // 静默处理
  }
};

const saveSubAgentSettings = async () => {
  subAgentSettingsSaving.value = true;
  try {
    await fetch('/api/multiagent/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        settings: {
          sub_agent_compress_threshold_tokens: subAgentCompressThreshold.value,
          // 留空（null/''）提交 null → 服务端删除该键恢复默认 50；0 → 无上限
          sub_agent_max_turns:
            subAgentMaxTurns.value === null || (subAgentMaxTurns.value as unknown) === ''
              ? null
              : Number(subAgentMaxTurns.value)
        }
      })
    });
  } finally {
    subAgentSettingsSaving.value = false;
  }
};

const openRoleEditor = (role?: any) => {
  if (role) {
    editingRole.value = role;
    roleForm.value = {
      role_id: role.role_id || '',
      name: role.name || '',
      description: role.description || '',
      body_prompt: role.body_prompt || '',
      thinking_mode: role.thinking_mode || 'fast',
      model_key: role.model_key || ''
    };
  } else {
    editingRole.value = null;
    roleForm.value = { role_id: '', name: '', description: '', body_prompt: '', thinking_mode: 'fast', model_key: '' };
  }
  closeDropdown();
  roleEditorOpen.value = true;
};

const closeRoleEditor = () => {
  roleEditorOpen.value = false;
  editingRole.value = null;
  closeDropdown();
};

// 角色编辑弹窗遮罩：按下与松开都发生在遮罩上才视为「点击遮罩关闭」。
// 修复：在输入框内拖选文字、松开落在遮罩上时，click 事件 target 为遮罩，
// 会被 @click.self 误判为点击遮罩，导致整个编辑器关闭、表单内容丢失。
const roleEditorOverlayPressActive = ref(false);

const handleRoleEditorOverlayPressStart = (event: MouseEvent) => {
  roleEditorOverlayPressActive.value =
    event.target === event.currentTarget && event.button === 0;
};

const handleRoleEditorOverlayPressEnd = (event: MouseEvent) => {
  const shouldClose =
    roleEditorOverlayPressActive.value && event.target === event.currentTarget;
  roleEditorOverlayPressActive.value = false;
  if (shouldClose) {
    closeRoleEditor();
  }
};

const saveRole = async () => {
  roleSaving.value = true;
  try {
    const isEdit = !!editingRole.value;
    const url = isEdit
      ? `/api/multiagent/roles/${roleForm.value.role_id}`
      : '/api/multiagent/roles';
    const method = isEdit ? 'PUT' : 'POST';
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        ...roleForm.value,
        model_key: roleForm.value.model_key || null
      })
    });
    const data = await resp.json();
    if (data.success) {
      closeRoleEditor();
      await loadSubAgentRoles();
    }
  } finally {
    roleSaving.value = false;
  }
};

const deleteRole = async (role: any) => {
  if (!confirm(t('personalization.deleteRoleConfirm', { name: role.name }))) return;
  try {
    const resp = await fetch(`/api/multiagent/roles/${role.role_id}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    const data = await resp.json();
    if (data.success) {
      await loadSubAgentRoles();
    }
  } catch (e) {
    // 静默处理
  }
};

const setActiveTab = (tab: PersonalTab) => {
  activeTab.value = tab;
  closeDropdown();
};

const startTutorial = () => {
  fetch('/api/tutorial-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ tutorial_completed: true })
  }).catch(() => {});
  tutorialStore.markCompleted();
  personalization.closeDrawer();
  window.setTimeout(() => {
    tutorialStore.startTutorial();
  }, 320);
};

const isRunModeActive = (value: RunModeValue) => {
  if (value === null) {
    return !form.value.default_run_mode;
  }
  return form.value.default_run_mode === value;
};

const setDefaultRunMode = (value: RunModeValue) => {
  if (checkModeModelConflict(value, form.value.default_model)) {
    return;
  }
  personalization.setDefaultRunMode(value);
};

const setDefaultModel = (value: string) => {
  if (policyStore.disabledModelSet.has(value)) {
    uiStore.pushToast({
      title: t('personalization.modelDisabledTitle'),
      message: t('personalization.modelDisabledMessage'),
      type: 'warning'
    });
    return;
  }
  if (checkModeModelConflict(form.value.default_run_mode, value)) {
    return;
  }
  personalization.setDefaultModel(value);
};

const setDefaultPermissionMode = (value: PermissionModeValue) => {
  personalization.setDefaultPermissionMode(value);
};

const setCommunicationStyle = (value: 'default' | 'human_like' | 'auto') => {
  personalization.setCommunicationStyle(value);
};

const setConversationContinuity = (value: 'low' | 'medium' | 'high') => {
  personalization.setConversationContinuity(value);
};

const selectDefaultModel = (value: string) => {
  setDefaultModel(value);
  closeDropdown();
};

const selectDefaultRunMode = (value: RunModeValue) => {
  setDefaultRunMode(value);
  closeDropdown();
};

const selectDefaultPermissionMode = (value: PermissionModeValue) => {
  setDefaultPermissionMode(value);
  closeDropdown();
};

const selectDefaultWorkMode = (value: WorkModeValue) => {
  personalization.setDefaultWorkMode(value);
  closeDropdown();
};

const selectVersioningBackupMode = (value: 'shallow' | 'full') => {
  personalization.setVersioningBackupMode(value);
  closeDropdown();
};

const selectCommunicationStyle = (value: 'default' | 'human_like' | 'auto') => {
  setCommunicationStyle(value);
  closeDropdown();
};

const selectConversationContinuity = (value: 'low' | 'medium' | 'high') => {
  setConversationContinuity(value);
  closeDropdown();
};

const selectImageCompression = (value: (typeof imageCompressionOptions)[number]['id']) => {
  personalization.setImageCompression(value);
  closeDropdown();
};

const selectBlockDisplayMode = (mode: 'traditional' | 'stacked' | 'minimal') => {
  handleBlockDisplayModeChange(mode);
  closeDropdown();
};

const checkModeModelConflict = (mode: RunModeValue, model: string | null): boolean => {
  const found = (filteredModelOptions.value || []).find((item: any) => item.value === model);
  const warnings: string[] = [];
  if (found?.thinkingOnly && mode && mode !== 'thinking') {
    warnings.push(t('personalization.modelThinkingOnlyWarning', { label: found.label }));
  }
  if (found?.fastOnly && mode && mode !== 'fast') {
    warnings.push(t('personalization.modelFastOnlyWarning', { label: found.label }));
  }
  if (warnings.length) {
    uiStore.pushToast({
      title: t('personalization.modelModeConflictTitle'),
      message: warnings.join(' '),
      type: 'warning',
      duration: 6000
    });
    return true;
  }
  return false;
};

const handleRecentConversationsPromptLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target) {
    return;
  }
  personalization.updateField({
    key: 'recent_conversations_prompt_limit',
    value: target.value
  });
};

const commitRecentConversationsPromptLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    personalization.setRecentConversationsPromptLimit(null);
    return;
  }
  const parsed = Number(target.value);
  personalization.setRecentConversationsPromptLimit(Number.isNaN(parsed) ? null : parsed);
};

const restoreRecentConversationsPromptLimit = () => {
  personalization.setRecentConversationsPromptLimit(null);
};

const handleProjectMemoryInjectLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target) {
    return;
  }
  personalization.updateField({
    key: 'project_memory_inject_limit',
    value: target.value
  });
};

const commitProjectMemoryInjectLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    // 留空 = 无上限
    personalization.setProjectMemoryInjectLimit(null);
    return;
  }
  const parsed = Number(target.value);
  personalization.setProjectMemoryInjectLimit(Number.isNaN(parsed) ? null : parsed);
};

const restoreProjectMemoryInjectLimit = () => {
  personalization.restoreProjectMemoryInjectLimit();
};

const handleCompressionNumberInput = (key: CompressionField, event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    personalization.updateField({ key, value: null });
    return;
  }
  const parsed = Number(target.value);
  if (!Number.isFinite(parsed)) {
    personalization.updateField({ key, value: null });
    return;
  }
  personalization.updateField({ key, value: Math.round(parsed) });
};

const restoreCompressionDefaults = () => {
  personalization.updateField({ key: 'shallow_compress_trigger_tokens', value: null });
  personalization.updateField({ key: 'shallow_compress_keep_recent_tools', value: null });
  personalization.updateField({ key: 'shallow_compress_max_replace_per_round', value: null });
  personalization.updateField({ key: 'shallow_compress_trigger_tool_calls_interval', value: null });
  personalization.updateField({ key: 'deep_compress_trigger_tokens', value: null });
};

const toggleCategory = (categoryId: string) => {
  personalization.toggleDefaultToolCategory(categoryId);
};

const blockDisplayOptions = [
  {
    id: 'traditional',
    labelKey: 'personalization.blockDisplayTraditional',
    descKey: 'personalization.blockDisplayTraditionalDesc',
    value: 'traditional' as const
  },
  {
    id: 'stacked',
    labelKey: 'personalization.blockDisplayStacked',
    descKey: 'personalization.blockDisplayStackedDesc',
    value: 'stacked' as const,
    badgeKey: 'personalization.badgeRecommended'
  },
  {
    id: 'minimal',
    labelKey: 'personalization.blockDisplayMinimal',
    descKey: 'personalization.blockDisplayMinimalDesc',
    value: 'minimal' as const,
    badgeKey: 'personalization.badgeNew'
  }
];

const handleBlockDisplayModeChange = (mode: 'traditional' | 'stacked' | 'minimal') => {
  personalization.setBlockDisplayMode(mode);
};

const handleStackedHideBordersChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  personalization.updateField({
    key: 'stacked_hide_borders',
    value: target.checked
  });
};

const handleMinimalExpandHeightLimitedChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  personalization.updateField({
    key: 'minimal_expand_height_limited',
    value: target.checked
  });
};

const compactMessageDisplayOptions = [
  {
    id: 'full',
    labelKey: 'personalization.compactMessageFull',
    descKey: 'personalization.compactMessageFullDesc',
    value: 'full' as const
  },
  {
    id: 'brief',
    labelKey: 'personalization.compactMessageBrief',
    descKey: 'personalization.compactMessageBriefDesc',
    value: 'brief' as const
  }
];

const selectCompactMessageDisplay = (mode: 'full' | 'brief') => {
  personalization.setCompactMessageDisplay(mode);
  closeDropdown();
};

const openAdminPanel = () => {
  window.open('/admin/monitor', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openCustomTools = () => {
  window.open('/admin/custom-tools', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openApiAdmin = () => {
  window.open('/admin/api', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openMcpConfig = () => {
  window.open('/admin/policy', '_blank', 'noopener');
  personalization.closeDrawer();
};

// ===== 主题切换 =====
const { setTheme, loadTheme } = useTheme();
const themeOptions: Array<{ id: ThemeKey; labelKey: string; descKey: string; swatches: string[] }> = [
  {
    id: 'classic',
    labelKey: 'personalization.themeClassic',
    descKey: 'personalization.themeClassicDesc',
    swatches: ['#eeece2', '#f7f3ea', '#da7756']
  },
  {
    id: 'light',
    labelKey: 'personalization.themeLight',
    descKey: 'personalization.themeLightDesc',
    swatches: ['#ffffff', '#f7f7f8', '#6b7280']
  },
  {
    id: 'dark',
    labelKey: 'personalization.themeDark',
    descKey: 'personalization.themeDarkDesc',
    swatches: ['#1a1a1a', '#2a2a2a', '#3a3a3a']
  }
];

const activeTheme = ref<ThemeKey>(loadTheme());

const themeLabel = computed(() => {
  void currentLocale.value;
  const found = themeOptions.find((option) => option.id === activeTheme.value);
  return found ? t(found.labelKey) : t('personalization.themeClassic');
});

// 监听store中的theme变化，同步到activeTheme（用于从后端加载主题后更新UI）
watch(
  () => personalization.form.theme,
  (newTheme) => {
    if (newTheme && newTheme !== activeTheme.value) {
      activeTheme.value = newTheme as ThemeKey;
      setTheme(newTheme as ThemeKey);
    }
  },
  { immediate: true }
);

const selectThemeOption = (theme: ThemeKey) => {
  applyThemeOption(theme);
  closeDropdown();
};

// ===== 界面语言切换 =====
const { locale, setLocale } = useLocale();
const localeOptions: Array<{ id: LocaleKey; labelKey: string; descKey: string }> = [
  { id: 'zh-CN', labelKey: 'personalization.localeZhCN', descKey: 'personalization.localeChineseDesc' },
  { id: 'en-US', labelKey: 'personalization.localeEnglish', descKey: 'personalization.localeEnglishDesc' }
];

const localeLabel = computed(() => {
  void currentLocale.value;
  const found = localeOptions.find((option) => option.id === locale.value);
  return found ? t(found.labelKey) : t('personalization.localeZhCN');
});

const selectLocaleOption = (nextLocale: LocaleKey) => {
  setLocale(nextLocale);
  closeDropdown();
};

const applyThemeOption = async (theme: ThemeKey) => {
  activeTheme.value = theme;
  setTheme(theme);

  // 同步更新到store并保存到后端配置文件
  personalization.updateField({ key: 'theme', value: theme });

  try {
    const resp = await fetch('/api/personalization', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme })
    });
    const result = await resp.json();
    if (!resp.ok || !result.success) {
      console.warn('保存主题到后端失败:', result.error);
    }
  } catch (error) {
    console.warn('保存主题到后端失败:', error);
  }
};

</script>

<style scoped>
.sub-agent-role-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sub-agent-role-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 11px 0;
  border-bottom: 1px solid var(--theme-control-border);
}
.sub-agent-role-info {
  flex: 1;
  min-width: 0;
}
.sub-agent-role-name {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sub-agent-role-tag {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 400;
}
.sub-agent-role-tag.tag-preset {
  background: color-mix(in srgb, var(--text-secondary) 15%, transparent);
  color: var(--text-secondary);
}
.sub-agent-role-tag.tag-custom {
  background: color-mix(in srgb, var(--accent) 15%, transparent);
  color: var(--accent);
}
.sub-agent-role-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin-top: 3px;
  line-height: 1.38;
}
.sub-agent-role-meta {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.7;
  margin-top: 2px;
}
.sub-agent-role-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

/* 角色编辑弹窗 — 复用 settings-redesign-card */
.role-editor-drawer-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2147483647;
}
.role-editor-drawer-card {
  display: flex;
  flex-direction: column;
  overflow: visible;
}
.role-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-shrink: 0;
}
.role-editor-header h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.role-editor-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: visible;
  min-height: 0;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.role-editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  flex-shrink: 0;
}
.settings-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.settings-section-title {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-primary);
}
.settings-empty-hint {
  color: var(--text-secondary);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}

.settings-redesign-card {
  --settings-floating-menu-bg: color-mix(in srgb, var(--surface-soft) 90%, var(--surface-raised));
  --settings-floating-menu-hover: var(--hover-bg);
  --settings-floating-menu-shadow: none;
  --settings-tab-hover-bg: var(--theme-tab-active);
  --settings-tab-active-bg: var(--theme-tab-active);
  position: relative;
  width: min(70vw, calc(100vw - 24px));
  height: min(80vh, calc(100vh - 24px));
  padding: 30px 42px 24px;
  border-radius: 32px;
  overflow: visible;
  background: var(--theme-surface-soft);
  border: 1px solid var(--theme-control-border);
  box-shadow: none;
}

:global(html[data-theme='dark']) .settings-redesign-card,
:global(body[data-theme='dark']) .settings-redesign-card {
  --settings-floating-menu-bg: var(--surface-panel);
  --settings-floating-menu-hover: var(--hover-bg);
  --settings-floating-menu-shadow: none;
  --settings-tab-hover-bg: var(--hover-bg);
  --settings-tab-active-bg: var(--hover-bg);
}

.settings-close-button {
  position: absolute;
  top: 27px;
  left: 45px;
  width: 42px;
  height: 42px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-primary);
  display: grid;
  place-items: center;
  cursor: pointer;
  z-index: 3;
}

.settings-close-button:hover {
  background: var(--hover-bg);
}

.settings-close-button svg {
  width: 25px;
  height: 25px;
  stroke-width: 2.15;
}

.settings-redesign-body,
.settings-redesign-form {
  height: 100%;
  min-height: 0;
}

.settings-redesign-layout {
  height: 100%;
  display: grid;
  grid-template-columns: minmax(190px, 2fr) minmax(0, 8fr);
  column-gap: 34px;
  min-height: 0;
}

.settings-redesign-tabs {
  margin-top: 68px;
  height: calc(100% - 68px);
  padding-right: 28px;
  border-right: 1px solid var(--theme-control-border);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-redesign-tab {
  width: 100%;
  height: 47px;
  flex: 0 0 auto;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 0 13px;
  display: flex;
  align-items: center;
  gap: 14px;
  cursor: pointer;
  text-align: left;
  font-size: 16px;
  letter-spacing: -0.01em;
}

.settings-redesign-tab:hover {
  background: var(--settings-tab-hover-bg);
}

.settings-redesign-tab.active {
  background: var(--settings-tab-active-bg);
  font-weight: 640;
}

.settings-tab-icon {
  --icon-size: 20px;
  flex: 0 0 auto;
}

.settings-tab-icon--chat {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: -5px;
  margin-right: -5px;
}

.settings-tab-icon--chat svg {
  width: 30px;
  height: 30px;
}

.settings-redesign-content {
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: visible;
  display: flex;
  flex-direction: column;
  position: relative;
}

.settings-redesign-content-header {
  height: 46px;
  flex: 0 0 46px;
  display: flex;
  align-items: center;
}

.settings-redesign-content-header h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 28px;
  line-height: 1;
  letter-spacing: -0.045em;
  font-weight: 730;
}

.settings-redesign-title-line {
  height: 1px;
  background: var(--theme-control-border);
  margin: 22px 0 8px;
  flex: 0 0 1px;
}

.settings-redesign-scroll {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
  overflow-x: visible;
  padding-right: 18px;
  padding-bottom: 86px;
  scroll-padding-bottom: 86px;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-redesign-tabs::-webkit-scrollbar,
.settings-redesign-scroll::-webkit-scrollbar,
.settings-floating-menu::-webkit-scrollbar,
.app-update-changelog-content::-webkit-scrollbar,
.role-editor-body::-webkit-scrollbar {
  display: none;
}

.settings-floating-menu,
.app-update-changelog-content {
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-page {
  max-width: 900px;
  min-height: 0;
}

.settings-select-row,
.settings-input-row,
.settings-list-row,
.settings-action-row,
.settings-toggle-row {
  min-height: 64px;
  border-bottom: 1px solid var(--theme-control-border);
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  padding: 11px 0;
  color: var(--text-primary);
}

.settings-list-row.tall,
.settings-input-row.stackable {
  align-items: flex-start;
}

.settings-list-row.tall {
  grid-template-columns: minmax(180px, 1fr) minmax(0, 360px);
}

.settings-row-copy {
  min-width: 0;
  display: block;
}

.settings-row-title {
  display: block;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 550;
  letter-spacing: -0.01em;
  line-height: 1.32;
}

.settings-row-desc {
  display: block;
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 12.5px;
  line-height: 1.38;
}

.settings-select-wrap {
  position: relative;
  display: inline-flex;
  justify-self: end;
}

.settings-select-button {
  width: max-content;
  min-width: 0;
  height: 36px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 8px 0 10px;
  cursor: pointer;
  font-size: 15px;
  white-space: nowrap;
}

.settings-select-button:hover {
  background: var(--hover-bg);
}

.settings-select-button .select-chevron {
  width: 12px;
  height: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease;
}

.settings-select-button .select-chevron::before {
  content: '';
  width: 7px;
  height: 7px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: translateY(-1.5px) rotate(45deg);
}

.settings-select-wrap.open .settings-select-button .select-chevron {
  transform: rotate(180deg);
}

.settings-floating-menu {
  position: fixed;
  right: auto;
  top: auto;
  width: 300px;
  max-height: calc(44px * 4 + 12px);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 6px;
  border-radius: 20px;
  background: var(--settings-floating-menu-bg);
  border: 1px solid var(--theme-control-border);
  box-shadow: var(--settings-floating-menu-shadow);
  opacity: 1;
  backdrop-filter: none;
  z-index: 2147483647;
  display: none;
}

.settings-select-wrap.open .settings-floating-menu {
  display: block;
}

.settings-floating-menu.dark {
  background: var(--surface-panel) !important;
  box-shadow: none !important;
}

.settings-floating-menu.dark .settings-menu-option:hover {
  background: var(--hover-bg) !important;
}

:global(html[data-theme='dark']) :global(.settings-floating-menu),
:global(body[data-theme='dark']) :global(.settings-floating-menu) {
  background: var(--surface-panel) !important;
  box-shadow: none !important;
}

:global(html[data-theme='dark']) :global(.settings-menu-option:hover),
:global(body[data-theme='dark']) :global(.settings-menu-option:hover) {
  background: var(--hover-bg) !important;
}

.settings-menu-option {
  width: 100%;
  min-height: 38px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 22px;
  row-gap: 0;
  column-gap: 8px;
  align-items: center;
  padding: 7px 10px;
  text-align: left;
  cursor: pointer;
}

.settings-menu-option:hover {
  background: var(--settings-floating-menu-hover);
}

.settings-menu-option:disabled {
  opacity: 0.48;
  cursor: not-allowed;
}

.settings-menu-option strong {
  grid-column: 1;
  grid-row: 1;
  display: block;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 550;
  letter-spacing: -0.01em;
  min-width: 0;
}

.settings-menu-option span {
  grid-column: 1;
  grid-row: 2;
  display: block;
  margin-top: 0;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.15;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-menu-option svg {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: center;
  width: 21px;
  height: 21px;
  fill: none;
  stroke: var(--text-primary);
  stroke-width: 2.25;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0;
}

.settings-menu-option.selected svg {
  opacity: 1;
}

.settings-input-row input,
.settings-add-row input,
.settings-number-row input,
.settings-number-input,
.settings-compression-grid input {
  height: 38px;
  border: 1px solid var(--theme-control-border);
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 0 12px;
  outline: none;
  font-size: 13px;
}

.settings-number-input {
  width: 120px;
  text-align: right;
}

.settings-section-divider {
  display: flex;
  align-items: center;
  margin: 18px 0 6px;
}
.settings-section-divider__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.04em;
}
.settings-section-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  margin-left: 10px;
  background: var(--theme-control-border);
}

.settings-input-row > input {
  width: min(360px, 40vw);
  text-align: right;
}

.settings-input-row input:focus,
.settings-add-row input:focus,
.settings-number-row input:focus,
.settings-number-input:focus,
.settings-compression-grid input:focus {
  border-color: var(--text-secondary);
  box-shadow: none;
}

.settings-input-stack {
  width: min(430px, 46vw);
  display: grid;
  gap: 8px;
  justify-self: end;
}

.settings-input-stack.wide {
  width: min(360px, 42vw);
}

.settings-input-stack input {
  width: 100%;
}

.settings-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.settings-chip-row.right {
  justify-content: flex-end;
}

.settings-chip-row button,
.settings-number-row button,
.settings-add-row button,
.settings-secondary-button,
.settings-primary-button {
  border: 1px solid var(--theme-control-border);
  border-radius: 999px;
  background: transparent;
  color: var(--text-primary);
  min-height: 32px;
  padding: 0 12px;
  font-size: 12px;
  cursor: pointer;
}

.settings-chip-row button:hover,
.settings-number-row button:hover,
.settings-add-row button:hover,
.settings-secondary-button:hover {
  background: var(--hover-bg);
}

.settings-chip-row button.active,
.settings-primary-button {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}

.settings-secondary-button.danger {
  color: var(--state-warning);
}

.settings-inline-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.settings-inline-actions.right {
  margin-top: 10px;
}

.settings-mini-status {
  color: var(--text-secondary);
  font-size: 12px;
}

.settings-mini-status.warning {
  color: var(--state-warning);
}

.settings-add-row,
.settings-number-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.settings-add-row input,
.settings-number-row input {
  min-width: 0;
  flex: 1 1 auto;
}

.settings-textarea-row {
  min-height: 0;
  border-bottom: 1px solid var(--theme-control-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 0;
  color: var(--text-primary);
}

.settings-textarea-row textarea {
  width: 100%;
  min-height: 120px;
  max-height: 320px;
  border: 1px solid var(--theme-control-border);
  border-radius: 12px;
  background: transparent;
  color: var(--text-primary);
  padding: 12px;
  outline: none;
  font-size: 13px;
  line-height: 1.55;
  resize: vertical;
  overflow-y: auto;
  font-family: inherit;
}

.settings-textarea-row textarea:focus {
  border-color: var(--text-secondary);
  box-shadow: none;
}

.settings-textarea-row textarea::placeholder {
  color: var(--text-secondary);
  opacity: 0.6;
}

.settings-toggle-row {
  cursor: pointer;
  position: relative;
}

.settings-toggle-row input[type='checkbox'] {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.settings-group-block {
  border-bottom: 1px solid var(--theme-control-border);
  padding: 14px 0 16px;
}

.settings-group-title {
  margin-bottom: 6px;
}

.settings-toggle-row.inner {
  min-height: 48px;
  padding: 7px 0 7px 18px;
  border-bottom: 0;
}

.settings-check-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 26px;
}

.settings-compression-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
  margin-top: 10px;
}

.settings-compression-grid label {
  display: grid;
  gap: 5px;
  color: var(--text-secondary);
  font-size: 12px;
}

.settings-save-bar {
  position: absolute;
  right: 18px;
  bottom: 0;
  left: 0;
  min-height: 54px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  background: transparent;
  pointer-events: none;
}

.settings-save-bar > * {
  pointer-events: auto;
}

.personal-status-group {
  min-height: 20px;
  font-size: 12px;
}

.status.success {
  color: var(--state-success);
}

.status.error,
.usage-summary-error {
  color: var(--state-warning);
}

.usage-summary-page {
  display: block;
  padding: 0;
}

.settings-stats-card,
.usage-summary-card {
  width: 100%;
  max-width: 720px;
  padding: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.usage-summary-header h3 {
  margin: 4px 0 6px;
  font-size: 20px;
}

.usage-summary-eyebrow {
  margin: 0;
  font-size: 11px;
  color: var(--accent-strong);
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.usage-summary-desc,
.usage-summary-note,
.usage-summary-meta {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.usage-summary-grid {
  display: grid;
  gap: 10px;
}

.usage-summary-grid--tokens {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.usage-summary-grid--counts {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.usage-summary-item {
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid var(--theme-control-border);
  background: transparent;
}

.usage-summary-item .label {
  font-size: 12px;
  color: var(--text-secondary);
}

.usage-summary-item .value {
  margin-top: 4px;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.usage-summary-item .value--success {
  color: var(--state-success);
}

.usage-summary-item .value--warning {
  color: var(--state-warning);
}

.usage-summary-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.usage-summary-refresh {
  border: 1px solid var(--theme-control-border);
  border-radius: 999px;
  padding: 7px 14px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}

.admin-monitor-page {
  display: block;
  padding: 0;
}

@media (max-width: 900px) {
  .settings-redesign-card {
    width: 100vw;
    height: 100vh;
    border-radius: 0;
    padding: 30px 24px;
  }

  .settings-close-button {
    left: 27px;
    top: 20px;
  }

  .settings-redesign-layout {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .settings-redesign-tabs {
    margin-top: 68px;
    height: auto;
    padding-right: 0;
    padding-bottom: 12px;
    border-right: 0;
    border-bottom: 1px solid var(--theme-control-border);
    flex-direction: row;
    overflow: auto;
    scrollbar-width: none;
  }

  .settings-redesign-tabs::-webkit-scrollbar {
    display: none;
  }

  .settings-redesign-tab {
    width: auto;
    white-space: nowrap;
  }

  .settings-redesign-content {
    padding-top: 18px;
  }

  .settings-select-row,
  .settings-input-row,
  .settings-list-row,
  .settings-action-row,
  .settings-toggle-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  .settings-select-wrap,
  .settings-input-stack,
  .settings-input-row > input {
    justify-self: stretch;
    width: 100%;
  }

  .settings-select-button {
    margin-left: auto;
  }

  .settings-floating-menu {
    width: min(340px, calc(100vw - 48px));
  }

  .settings-check-grid,
  .settings-compression-grid,
  .usage-summary-grid--tokens,
  .usage-summary-grid--counts {
    grid-template-columns: 1fr;
  }
}

/* ── 语音模型下载进度条 ── */
.voice-download-bar {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.voice-download-track {
  width: 100%;
  height: 6px;
  background: var(--surface-muted);
  border-radius: 3px;
  overflow: hidden;
}
.voice-download-fill {
  height: 100%;
  background: var(--accent-primary);
  border-radius: 3px;
  transition: width 0.3s ease;
}
</style>

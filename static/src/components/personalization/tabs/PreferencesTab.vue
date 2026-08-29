<script setup lang="ts">
import { inject } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';

defineOptions({ name: 'PreferencesTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  tonePresets,
  toggleUpdating,
  activeDropdown,
  toggleDropdown,
  floatingMenuStyle,
  activeTheme,
  communicationStyleLabel,
  conversationContinuityLabel,
  currentBlockDisplayMode,
  stackedHideBorders,
  handleStackedHideBordersChange,
  minimalExpandHeightLimited,
  handleMinimalExpandHeightLimitedChange,
  selectCommunicationStyle,
  selectConversationContinuity
} = ctx;
</script>

<template>
  <section
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
</template>

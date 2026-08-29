<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'ReviewAgentsTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  activeDropdown,
  activeTheme,
  closeDropdown,
  floatingMenuStyle,
  reviewAgentDefs,
  reviewAgentOf,
  subAgentModels,
  toggleDropdown,
  updateReviewAgent,
  updateReviewAgentInt
} = ctx;
</script>

<template>
                    <section class="settings-page">
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
</template>

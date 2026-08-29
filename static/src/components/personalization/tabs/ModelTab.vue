<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'ModelTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  activeDropdown,
  defaultModelLabel,
  filteredModelOptions,
  floatingMenuStyle,
  isEffortActive,
  isRunModeActive,
  reasoningEffortLabel,
  runModeLabel,
  selectDefaultModel,
  selectDefaultReasoningEffort,
  selectDefaultRunMode,
  toggleDropdown,
  runModeOptions,
  reasoningEffortOptions,
  activeTheme
} = ctx;
</script>

<template>
                    <section
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
                              <strong>{{ option.label }}</strong
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
</template>

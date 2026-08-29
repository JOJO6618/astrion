<script setup lang="ts">
import { inject } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';

defineOptions({ name: 'ContextTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  commitProjectMemoryInjectLimitInput,
  commitRecentConversationsPromptLimitInput,
  form,
  handleCompressionNumberInput,
  handleProjectMemoryInjectLimitInput,
  handleRecentConversationsPromptLimitInput,
  personalization,
  projectMemoryInjectLimitMin,
  recentConversationsPromptLimitRange,
  restoreCompressionDefaults,
  restoreProjectMemoryInjectLimit,
  restoreRecentConversationsPromptLimit
} = ctx;
</script>

<template>
  <section class="settings-page">
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
</template>

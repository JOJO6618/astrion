<script setup lang="ts">
import { inject } from 'vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';

defineOptions({ name: 'ContextTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
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
  wsDescKey
} = ctx;
</script>

<template>
  <section class="stg-page">
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.contextTitle') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.recentConversationsTitle') }}</span
          ><span class="stg-row-desc">{{
            $t(wsDescKey('personalization.recentConversationsDesc'))
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.recent_conversations_prompt_enabled"
          @change="
            personalization.updateField({
              key: 'recent_conversations_prompt_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.recent_conversations_prompt_enabled"
      /></label>
      <div class="stg-input-row" v-if="form.recent_conversations_prompt_enabled">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{
            $t('personalization.recentConversationsCountTitle')
          }}</span>
          <span class="stg-row-desc">{{ $t('personalization.recentConversationsCountDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.recentConversationsCountTitle')"
          :min="recentConversationsPromptLimitRange.min"
          :max="recentConversationsPromptLimitRange.max"
          :value="form.recent_conversations_prompt_limit"
          @input="handleRecentConversationsPromptLimitInput"
          @blur="commitRecentConversationsPromptLimitInput"
        />
      </div>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.maxMemoryInjectTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.maxMemoryInjectDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.maxMemoryInjectTitle')"
          :min="projectMemoryInjectLimitMin"
          :value="form.project_memory_inject_limit ?? ''"
          :placeholder="$t('personalization.noLimitPlaceholder')"
          @input="handleProjectMemoryInjectLimitInput"
          @blur="commitProjectMemoryInjectLimitInput"
        />
      </div>
    </div>

    <div class="stg-section-divider">
      <span class="stg-section-divider__label">
        {{ $t('personalization.compressionStrategyTitle') }}
      </span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.autoShallowCompressTitle') }}</span
          ><span class="stg-row-desc">{{
            $t('personalization.autoShallowCompressDesc')
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.auto_shallow_compress_enabled"
          @change="
            personalization.updateField({
              key: 'auto_shallow_compress_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.auto_shallow_compress_enabled"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.autoDeepCompressTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.autoDeepCompressDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.auto_deep_compress_enabled"
          @change="
            personalization.updateField({
              key: 'auto_deep_compress_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.auto_deep_compress_enabled"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.adaptiveCompressTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.adaptiveCompressDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.adaptive_compress_enabled"
          @change="
            personalization.updateField({
              key: 'adaptive_compress_enabled',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.adaptive_compress_enabled"
      /></label>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.shallowTriggerTokens') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.shallowTriggerTokensDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.shallowTriggerTokens')"
          :value="form.shallow_compress_trigger_tokens ?? ''"
          @input="handleCompressionNumberInput('shallow_compress_trigger_tokens', $event)"
        />
      </div>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.shallowKeepRecentTools') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.shallowKeepRecentToolsDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.shallowKeepRecentTools')"
          :value="form.shallow_compress_keep_recent_tools ?? ''"
          @input="handleCompressionNumberInput('shallow_compress_keep_recent_tools', $event)"
        />
      </div>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.shallowKeepTurnTools') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.shallowKeepTurnToolsDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.shallowKeepTurnTools')"
          :value="form.shallow_compress_keep_user_turn_tools ?? ''"
          @input="handleCompressionNumberInput('shallow_compress_keep_user_turn_tools', $event)"
        />
      </div>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.shallowMaxReplace') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.shallowMaxReplaceDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.shallowMaxReplace')"
          :value="form.shallow_compress_max_replace_per_round ?? ''"
          @input="handleCompressionNumberInput('shallow_compress_max_replace_per_round', $event)"
        />
      </div>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.shallowToolInterval') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.shallowToolIntervalDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.shallowToolInterval')"
          :value="form.shallow_compress_trigger_tool_calls_interval ?? ''"
          @input="
            handleCompressionNumberInput('shallow_compress_trigger_tool_calls_interval', $event)
          "
        />
      </div>
      <div class="stg-input-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.deepTriggerTokens') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.deepTriggerTokensDesc') }}</span>
        </span>
        <input
          type="number"
          class="stg-number-input"
          :aria-label="$t('personalization.deepTriggerTokens')"
          :value="form.deep_compress_trigger_tokens ?? ''"
          @input="handleCompressionNumberInput('deep_compress_trigger_tokens', $event)"
        />
      </div>
      <div class="stg-action-row inner">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.restoreCompressionDefaults') }}</span>
          <span class="stg-row-desc">{{
            $t('personalization.restoreCompressionDefaultsDesc')
          }}</span>
        </span>
        <button type="button" class="stg-secondary-button" @click="restoreCompressionDefaults">
          {{ $t('personalization.restoreDefault') }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { inject } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import ModelSelectDropdown from '@/components/personalization/ModelSelectDropdown.vue';

defineOptions({ name: 'ModelPrefTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  activeDropdown,
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
  <section class="settings-page" data-tutorial="personal-page-model">
    <div class="settings-select-row">
      <span class="settings-row-copy"
        ><span class="settings-row-title">{{ $t('personalization.defaultModelTitle') }}</span
        ><span class="settings-row-desc">{{ $t('personalization.defaultModelDesc') }}</span></span
      >
      <ModelSelectDropdown
        :model-value="form.default_model"
        :options="filteredModelOptions"
        @select="selectDefaultModel"
      />
    </div>
    <div class="settings-select-row">
      <span class="settings-row-copy"
        ><span class="settings-row-title">{{ $t('personalization.defaultThinkingModeTitle') }}</span
        ><span class="settings-row-desc">{{
          $t('personalization.defaultThinkingModeDesc')
        }}</span></span
      >
      <div
        class="settings-select-wrap"
        :class="{ open: activeDropdown === 'run-mode' }"
        @click.stop
      >
        <button type="button" class="settings-select-button" @click="toggleDropdown('run-mode')">
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
        ><span class="settings-row-desc">{{
          $t('personalization.reasoningEffortDesc')
        }}</span></span
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
    <!-- 标题生成模型选择器已挪到「常规」页 autoTitle 开关下方 -->
    <!-- 外部会话标识：opt-in，开启后向 opencode.ai 端点发送每对话稳定的 session 头 -->
    <label class="settings-toggle-row"
      ><span class="settings-row-copy"
        ><span class="settings-row-title">{{
          $t('personalization.externalSessionHeaderTitle')
        }}</span
        ><span class="settings-row-desc">{{
          $t('personalization.externalSessionHeaderDesc')
        }}</span></span
      ><input
        type="checkbox"
        :checked="form.external_session_header"
        @change="
          personalization.updateField({
            key: 'external_session_header',
            value: $event.target.checked
          })
        " /><FancyCheck :checked="form.external_session_header"
    /></label>
  </section>
</template>

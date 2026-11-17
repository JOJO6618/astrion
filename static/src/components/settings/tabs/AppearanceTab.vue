<script setup lang="ts">
import { computed, inject } from 'vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { t } from '@/locales';

defineOptions({ name: 'AppearanceTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  activeTheme,
  blockDisplayOptions,
  compactMessageDisplayOptions,
  currentBlockDisplayMode,
  currentCompactMessageDisplay,
  form,
  handleMinimalExpandHeightLimitedChange,
  handleStackedHideBordersChange,
  locale,
  localeOptions,
  minimalExpandHeightLimited,
  personalization,
  selectBlockDisplayMode,
  selectCompactMessageDisplay,
  selectLocaleOption,
  selectThemeOption,
  stackedHideBorders,
  themeOptions,
  wsDescKey
} = ctx;

const themeItems = computed(() =>
  themeOptions.map((option: any) => ({
    value: option.id,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const localeItems = computed(() =>
  localeOptions.map((option: any) => ({
    value: option.id,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const currencyItems = computed(() => [
  { value: 'USD', label: t('personalization.currencyUSD') },
  { value: 'CNY', label: t('personalization.currencyCNY') }
]);
const blockDisplayItems = computed(() =>
  blockDisplayOptions.map((option: any) => ({
    value: option.value,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const compactMessageItems = computed(() =>
  compactMessageDisplayOptions.map((option: any) => ({
    value: option.value,
    label: t(option.labelKey),
    description: t(option.descKey)
  }))
);
const updateTheme = (value: string) => selectThemeOption(value as any);
const updateLocale = (value: string) => selectLocaleOption(value as any);
const updateBlockDisplay = (value: string) => selectBlockDisplayMode(value as any);
const updateCompactMessage = (value: string) => selectCompactMessageDisplay(value as any);
const selectCurrency = (value: string) => {
  personalization.updateField({ key: 'display_currency', value });
};
</script>

<template>
  <section class="stg-page">
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardAppearanceTheme') }}</span>
    </div>
    <div class="stg-card">
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.themeTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.themeDesc') }}</span></span
        >
        <UiSelect
          :model-value="activeTheme"
          :options="themeItems"
          align-end
          @update:model-value="updateTheme"
        />
      </div>
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('common.settingsLanguage') }}</span
          ><span class="stg-row-desc">{{ $t('common.settingsLanguageDesc') }}</span></span
        >
        <UiSelect
          :model-value="locale"
          :options="localeItems"
          align-end
          @update:model-value="updateLocale"
        />
      </div>
      <div class="stg-select-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.currencyTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.currencyDesc') }}</span>
        </span>
        <UiSelect
          :model-value="form.display_currency"
          :options="currencyItems"
          align-end
          @update:model-value="selectCurrency"
        />
      </div>
    </div>
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardAppearanceWorkspace') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.groupByWorkspaceTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.groupByWorkspaceDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.group_sidebar_by_workspace"
          @change="
            personalization.updateField({
              key: 'group_sidebar_by_workspace',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.group_sidebar_by_workspace"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.enhancedToolDisplayTitle') }}</span
          ><span class="stg-row-desc">{{
            $t('personalization.enhancedToolDisplayDesc')
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.enhanced_tool_display"
          @change="
            personalization.updateField({
              key: 'enhanced_tool_display',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.enhanced_tool_display"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.showStatusAvatarTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.showStatusAvatarDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.show_status_avatar"
          @change="
            personalization.updateField({
              key: 'show_status_avatar',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.show_status_avatar"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.showGitStatusBarTitle') }}</span
          ><span class="stg-row-desc">{{
            $t(wsDescKey('personalization.showGitStatusBarDesc'))
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.show_git_status_bar"
          @change="
            personalization.updateField({
              key: 'show_git_status_bar',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.show_git_status_bar"
      /></label>
    </div>
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardAppearancePanels') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.autoOpenTerminalTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.autoOpenTerminalDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.auto_open_terminal_panel"
          @change="
            personalization.updateField({
              key: 'auto_open_terminal_panel',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.auto_open_terminal_panel"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.quickDockAutoExpandTitle') }}</span
          ><span class="stg-row-desc">{{
            $t('personalization.quickDockAutoExpandDesc')
          }}</span></span
        ><input
          type="checkbox"
          :checked="form.quick_dock_auto_expand"
          @change="
            personalization.updateField({
              key: 'quick_dock_auto_expand',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.quick_dock_auto_expand"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.previewAutoOpenTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.previewAutoOpenDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.preview_auto_open"
          @change="
            personalization.updateField({
              key: 'preview_auto_open',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.preview_auto_open"
      /></label>
    </div>
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('settings.cardAppearanceMessages') }}</span>
    </div>
    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.editSummaryLiveTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.editSummaryLiveDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.edit_summary_live_display"
          @change="
            personalization.updateField({
              key: 'edit_summary_live_display',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.edit_summary_live_display"
      /></label>
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.filePreviewWrapTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.filePreviewWrapDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.file_preview_auto_wrap"
          @change="
            personalization.updateField({
              key: 'file_preview_auto_wrap',
              value: $event.target.checked
            })
          " /><UiSwitch decorative :checked="form.file_preview_auto_wrap"
      /></label>
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.blockDisplayModeTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.blockDisplayModeDesc') }}</span></span
        >
        <UiSelect
          :model-value="currentBlockDisplayMode"
          :options="blockDisplayItems"
          align-end
          @update:model-value="updateBlockDisplay"
        />
      </div>
      <label v-if="currentBlockDisplayMode === 'stacked'" class="stg-toggle-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.hideBlockBordersTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.hideBlockBordersDesc') }}</span>
        </span>
        <input
          type="checkbox"
          :checked="stackedHideBorders"
          @change="handleStackedHideBordersChange($event)"
        />
        <UiSwitch decorative :checked="stackedHideBorders" />
      </label>
      <label v-if="currentBlockDisplayMode === 'minimal'" class="stg-toggle-row">
        <span class="stg-row-copy">
          <span class="stg-row-title">{{ $t('personalization.minimalExpandHeightTitle') }}</span>
          <span class="stg-row-desc">{{ $t('personalization.minimalExpandHeightDesc') }}</span>
        </span>
        <input
          type="checkbox"
          :checked="minimalExpandHeightLimited"
          @change="handleMinimalExpandHeightLimitedChange($event)"
        />
        <UiSwitch decorative :checked="minimalExpandHeightLimited" />
      </label>
      <div class="stg-select-row">
        <span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.compactMessageTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.compactMessageDesc') }}</span></span
        >
        <UiSelect
          :model-value="currentCompactMessageDisplay"
          :options="compactMessageItems"
          align-end
          @update:model-value="updateCompactMessage"
        />
      </div>
    </div>
  </section>
</template>

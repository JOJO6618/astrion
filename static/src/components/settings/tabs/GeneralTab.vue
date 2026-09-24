<script setup lang="ts">
import { computed, inject } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';
import ModelSelectDropdown from '@/components/personalization/ModelSelectDropdown.vue';
import { t } from '@/locales';

defineOptions({ name: 'GeneralTab' });

/**
 * 设置页「通用」分区：标题生成设置 + App 更新检查。
 * （用量统计 / 新手教程 / 退出登录保留在个人空间「账户」页签 AccountTab.vue。）
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  isAppShell,
  appCurrentVersionText,
  appUpdateCheckedText,
  appHasUpdate,
  appUpdateStateText,
  appUpdateChecking,
  checkAppUpdate,
  downloadLatestApp,
  subAgentModels
} = ctx;

// 辅助模型库 → 统一下拉选项；provider 分组信息由组件内部反查主库补齐
const auxModelOptions = computed(() =>
  (subAgentModels.value || []).map((m: any) => ({ key: m.key, label: m.name || m.key }))
);
const defaultExtraOptions = computed(() => [
  { key: '', label: t('personalization.defaultModelOption') }
]);
</script>

<template>
  <section class="settings-page">
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
            value: ($event.target as HTMLInputElement).checked
          })
        "
      />
      <FancyCheck :checked="form.auto_generate_title" />
    </label>

    <!-- 标题生成模型（仅在开启自动标题后显示；统一模型来源 = 主注册表，留空走自动规则） -->
    <div v-if="form.auto_generate_title" class="settings-select-row">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.titleModelTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.titleModelDesc') }}</span>
      </span>
      <ModelSelectDropdown
        :model-value="form.title_model"
        :options="auxModelOptions"
        :extra-options="defaultExtraOptions"
        @select="(v) => personalization.updateField({ key: 'title_model', value: v })"
      />
    </div>

    <div class="settings-action-row" v-if="isAppShell">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.appUpdateTitle') }}</span>
        <span class="settings-row-desc">{{
          $t('personalization.appUpdateDesc', {
            version: appCurrentVersionText,
            checked: appUpdateCheckedText
          })
        }}</span>
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
          {{
            appUpdateChecking
              ? $t('personalization.appChecking')
              : $t('personalization.appCheckUpdate')
          }}
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
  </section>
</template>

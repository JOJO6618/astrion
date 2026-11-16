<script setup lang="ts">
import { computed, inject } from 'vue';
import UiModelSelect from '@/components/ui/UiModelSelect.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import { t } from '@/locales';

defineOptions({ name: 'ReviewAgentsTab' });

/**
 * 共享上下文由 usePersonalizationContext（PersonalizationDrawer / SettingsShell 各自 provide 同一份）注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  form,
  personalization,
  reviewAgentDefs,
  reviewAgentOf,
  subAgentModels,
  updateReviewAgent,
  updateReviewAgentInt
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
  <section class="stg-page">
    <template v-for="agent in reviewAgentDefs" :key="agent.key">
      <div class="stg-section-divider">
        <span class="stg-section-divider__label">{{ $t(agent.nameKey) }}</span>
      </div>
      <p class="stg-section-desc">{{ $t(agent.descKey) }}</p>

      <div class="stg-card">
        <label v-if="agent.key === 'auto_approval'" class="stg-toggle-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('personalization.allowAutoFullAccessTitle') }}</span>
            <span class="stg-row-desc">{{ $t('personalization.allowAutoFullAccessDesc') }}</span>
          </span>
          <input
            type="checkbox"
            :checked="form.allow_auto_approve_full_access"
            @change="
              personalization.updateField({
                key: 'allow_auto_approve_full_access',
                value: ($event.target as HTMLInputElement).checked
              })
            "
          />
          <UiSwitch decorative :checked="form.allow_auto_approve_full_access" />
        </label>

        <div class="stg-select-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('personalization.modelTitle') }}</span>
            <span class="stg-row-desc">{{ $t('personalization.reviewModelEmptyDesc') }}</span>
          </span>
          <UiModelSelect
            :model-value="reviewAgentOf(agent.key).model"
            :options="auxModelOptions"
            :extra-options="defaultExtraOptions"
            align-end
            @update:model-value="(v) => updateReviewAgent(agent.key, { model: v })"
          />
        </div>

        <div class="stg-select-row">
          <span class="stg-row-copy">
            <span class="stg-row-title">{{ $t('personalization.maxRoundsTitle') }}</span>
            <span class="stg-row-desc">{{ $t('personalization.maxRoundsDesc') }}</span>
          </span>
          <input
            type="number"
            class="stg-number-input"
            min="1"
            max="50"
            :value="reviewAgentOf(agent.key).max_rounds"
            @change="
              updateReviewAgentInt(
                agent.key,
                'max_rounds',
                ($event.target as HTMLInputElement).value,
                1,
                50
              )
            "
          />
        </div>
      </div>
    </template>

    <!-- 目标模式（goal review 机制的运行参数，自「工作区与权限」迁入） -->
    <div class="stg-section-divider">
      <span class="stg-section-divider__label">{{ $t('personalization.goalModeDivider') }}</span>
    </div>

    <div class="stg-card">
      <label class="stg-toggle-row"
        ><span class="stg-row-copy"
          ><span class="stg-row-title">{{ $t('personalization.goalReviewActiveTitle') }}</span
          ><span class="stg-row-desc">{{ $t('personalization.goalReviewActiveDesc') }}</span></span
        ><input
          type="checkbox"
          :checked="form.goal_review_mode === 'active'"
          @change="
            personalization.updateField({
              key: 'goal_review_mode',
              value: $event.target.checked ? 'active' : 'readonly'
            })
          " /><UiSwitch decorative :checked="form.goal_review_mode === 'active'"
      /></label>
    </div>
  </section>
</template>

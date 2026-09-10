<script setup lang="ts">
import { inject, unref } from 'vue';
import FancyCheck from '@/components/common/FancyCheck.vue';

defineOptions({ name: 'ToolsTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  form,
  personalization,
  skillsCatalog,
  toggleCategory,
  toolCategories,
  toolLoadingRegistry
} = ctx;

/**
 * 当前勾选为延迟加载的工具名列表。
 * 注意：form 是 Drawer 经 storeToRefs + provide 传入的 Ref，脚本侧必须 unref 后再取属性
 * （模板里会自动解包，脚本里直接 form.xxx 会得到 undefined）。
 */
const deferredToolNames = (): string[] => {
  const f: any = unref(form);
  return Array.isArray(f?.tool_loading_deferred) ? f.tool_loading_deferred : [];
};

/** 类目内工具是否全部被勾选为延迟加载 */
const isCategoryFullyDeferred = (cat: { tools: string[] }) =>
  Array.isArray(cat.tools) &&
  cat.tools.length > 0 &&
  cat.tools.every((name: string) => deferredToolNames().includes(name));

/** 勾选/取消单个延迟工具 */
const toggleDeferredTool = (name: string, checked: boolean) => {
  const current = [...deferredToolNames()];
  const idx = current.indexOf(name);
  if (checked && idx === -1) {
    current.push(name);
  } else if (!checked && idx !== -1) {
    current.splice(idx, 1);
  }
  personalization.updateField({ key: 'tool_loading_deferred', value: current });
};

/** 勾选/取消整个类目（幂等，不动其他类目） */
const toggleDeferredCategory = (cat: { tools: string[] }, checked: boolean) => {
  const current = new Set<string>(deferredToolNames());
  for (const name of cat.tools || []) {
    if (checked) {
      current.add(name);
    } else {
      current.delete(name);
    }
  }
  personalization.updateField({ key: 'tool_loading_deferred', value: Array.from(current) });
};
</script>

<template>
                    <section class="settings-page">
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
                      <div class="settings-group-block" v-if="toolLoadingRegistry.length">
                        <div class="settings-group-title">
                          <span class="settings-row-title">{{ $t('personalization.toolLoadingTitle') }}</span
                          ><span class="settings-row-desc">{{ $t('personalization.toolLoadingDesc') }}</span>
                        </div>
                        <label class="settings-toggle-row inner"
                          ><span class="settings-row-title">{{ $t('personalization.toolLoadingEnabledTitle') }}</span
                          ><input
                            type="checkbox"
                            :checked="form.tool_loading_enabled"
                            @change="
                              personalization.updateField({
                                key: 'tool_loading_enabled',
                                value: $event.target.checked
                              })
                            " /><FancyCheck :checked="form.tool_loading_enabled" /></label>
                        <template v-if="form.tool_loading_enabled">
                          <div v-for="cat in toolLoadingRegistry" :key="cat.key">
                            <label class="settings-toggle-row inner"
                              ><span class="settings-row-title">{{ $t(`personalization.toolLoadingCat.${cat.key}`) }}</span
                              ><input
                                type="checkbox"
                                :checked="isCategoryFullyDeferred(cat)"
                                @change="toggleDeferredCategory(cat, $event.target.checked)" /><FancyCheck :checked="isCategoryFullyDeferred(cat)" /></label>
                            <div class="settings-check-grid">
                              <label
                                v-for="name in cat.tools"
                                :key="name"
                                class="settings-toggle-row inner"
                                ><span class="settings-row-title">{{ name }}</span
                                ><input
                                  type="checkbox"
                                  :checked="form.tool_loading_deferred.includes(name)"
                                  @change="toggleDeferredTool(name, $event.target.checked)" /><FancyCheck :checked="form.tool_loading_deferred.includes(name)" /></label>
                            </div>
                          </div>
                        </template>
                      </div>
                    </section>
</template>

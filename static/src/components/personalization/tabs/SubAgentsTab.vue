<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'SubAgentsTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  activeDropdown,
  activeTheme,
  closeDropdown,
  closeRoleEditor,
  deleteRole,
  editingRole,
  handleRoleEditorOverlayPressEnd,
  handleRoleEditorOverlayPressStart,
  openRoleEditor,
  personalization,
  roleEditorOpen,
  roleForm,
  roleSaving,
  saveRole,
  saveSubAgentSettings,
  subAgentCompressThreshold,
  subAgentMaxTurns,
  subAgentModels,
  subAgentRoles,
  subAgentRolesLoading,
  subAgentSettingsSaving,
  toggleDropdown
} = ctx;
</script>

<template>
  <section class="settings-page">
    <div class="settings-section-desc" style="margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.6">{{ $t('personalization.subAgentsIntro') }}
    </div>

    <!-- 压缩阈值配置 -->
    <div class="settings-action-row" style="margin-bottom: 16px">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.compressThresholdTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.compressThresholdDesc') }}
        </span>
      </span>
      <div style="display: flex; gap: 6px; align-items: center">
        <input
          type="number"
          class="settings-number-input"
          v-model.number="subAgentCompressThreshold"
          :min="10000"
          :step="10000"
          style="width: 120px"
        />
        <button
          type="button"
          class="settings-secondary-button"
          :disabled="subAgentSettingsSaving"
          @click="saveSubAgentSettings"
        >
          {{ subAgentSettingsSaving ? $t('personalization.saving') : $t('common.save') }}
        </button>
      </div>
    </div>

    <!-- 最大执行轮次配置 -->
    <div class="settings-action-row" style="margin-bottom: 16px">
      <span class="settings-row-copy">
        <span class="settings-row-title">{{ $t('personalization.maxTurnsTitle') }}</span>
        <span class="settings-row-desc">{{ $t('personalization.maxTurnsDesc') }}
        </span>
      </span>
      <div style="display: flex; gap: 6px; align-items: center">
        <input
          type="number"
          class="settings-number-input"
          v-model.number="subAgentMaxTurns"
          :min="0"
          :step="10"
          placeholder="50"
          style="width: 120px"
        />
        <button
          type="button"
          class="settings-secondary-button"
          :disabled="subAgentSettingsSaving"
          @click="saveSubAgentSettings"
        >
          {{ subAgentSettingsSaving ? $t('personalization.saving') : $t('common.save') }}
        </button>
      </div>
    </div>

    <!-- 角色列表 -->
    <div class="settings-section-header" style="margin-bottom: 8px">
      <span class="settings-section-title">{{ $t('personalization.roleListTitle') }}</span>
      <button
        type="button"
        class="settings-secondary-button"
        @click="openRoleEditor()"
      >
        {{ $t('personalization.newRole') }}
      </button>
    </div>

    <div v-if="subAgentRolesLoading" class="settings-empty-hint">{{ $t('common.loading') }}</div>
    <div v-else-if="subAgentRoles.length === 0" class="settings-empty-hint">{{ $t('personalization.noRoles') }}
    </div>

    <div v-else class="sub-agent-role-list">
      <div
        v-for="role in subAgentRoles"
        :key="role.role_id"
        class="sub-agent-role-card"
      >
        <div class="sub-agent-role-info">
          <div class="sub-agent-role-name">
            {{ role.name }}
            <span
              class="sub-agent-role-tag"
              :class="role.is_custom ? 'tag-custom' : 'tag-preset'"
            >
              {{ role.is_custom ? $t('personalization.customTag') : $t('personalization.presetTag') }}
            </span>
          </div>
          <div class="sub-agent-role-desc">{{ role.description || $t('personalization.noDescription') }}</div>
          <div class="sub-agent-role-meta">
            {{ $t('personalization.roleMetaId', { id: role.role_id, mode: role.thinking_mode }) }}<template v-if="role.model_key">{{ $t('personalization.roleMetaModel', { model: role.model_key }) }}</template>
          </div>
        </div>
        <div class="sub-agent-role-actions">
          <button
            type="button"
            class="settings-secondary-button"
            @click="openRoleEditor(role)"
          >{{ $t('personalization.edit') }}</button>
          <button
            v-if="role.is_custom"
            type="button"
            class="settings-secondary-button danger"
            @click="deleteRole(role)"
          >{{ $t('common.delete') }}</button>
        </div>
      </div>
    </div>

    <!-- 角色编辑弹窗（与个人空间同尺寸） -->
    <transition name="personal-page-fade" appear>
      <div
        v-if="roleEditorOpen"
        class="role-editor-drawer-overlay"
        @click="closeDropdown"
        @mousedown="handleRoleEditorOverlayPressStart"
        @mouseup="handleRoleEditorOverlayPressEnd"
      >
        <div class="role-editor-drawer-card settings-redesign-card" @click.stop>
          <div class="role-editor-header">
            <h2>{{ editingRole ? $t('personalization.roleEditorEditTitle') : $t('personalization.roleEditorCreateTitle') }}</h2>
            <button type="button" class="settings-secondary-button" @click="closeRoleEditor">{{ $t('common.close') }}</button>
          </div>
          <div class="role-editor-body" @click="closeDropdown">
            <label class="settings-input-row">
              <span class="settings-row-title">{{ $t('personalization.roleIdTitle') }}</span>
              <input
                v-if="!editingRole"
                type="text"
                :value="roleForm.role_id"
                maxlength="40"
                ::placeholder="$t('personalization.roleIdPlaceholder')"
                @input="roleForm.role_id = ($event.target as HTMLInputElement).value"
              />
              <span v-else class="settings-row-desc" style="text-align: right; font-size: 14px;">{{ roleForm.role_id }}</span>
            </label>
            <label class="settings-input-row">
              <span class="settings-row-title">{{ $t('personalization.roleNameTitle') }}</span>
              <input
                type="text"
                :value="roleForm.name"
                maxlength="40"
                ::placeholder="$t('personalization.roleNamePlaceholder')"
                @input="roleForm.name = ($event.target as HTMLInputElement).value"
              />
            </label>
            <label class="settings-input-row">
              <span class="settings-row-title">{{ $t('personalization.roleDescTitle') }}</span>
              <input
                type="text"
                :value="roleForm.description"
                maxlength="100"
                ::placeholder="$t('personalization.roleDescPlaceholder')"
                @input="roleForm.description = ($event.target as HTMLInputElement).value"
              />
            </label>
            <div class="settings-select-row">
              <span class="settings-row-copy">
                <span class="settings-row-title">{{ $t('personalization.thinkingModeTitle') }}</span>
                <span class="settings-row-desc">{{ $t('personalization.roleThinkingDesc') }}</span>
              </span>
              <div
                class="settings-select-wrap"
                :class="{ open: activeDropdown === 'role-thinking' }"
                @click.stop
              >
                <button
                  type="button"
                  class="settings-select-button"
                  @click="toggleDropdown('role-thinking')"
                >
                  {{ roleForm.thinking_mode === 'thinking' ? 'thinking' : 'fast' }}
                  <span class="select-chevron" aria-hidden="true"></span>
                </button>
                <div
                  :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                  :style="activeDropdown === 'role-thinking' ? floatingMenuStyle : undefined"
                >
                  <button
                    type="button"
                    class="settings-menu-option"
                    :class="{ selected: roleForm.thinking_mode === 'fast' }"
                    @click="roleForm.thinking_mode = 'fast'; closeDropdown()"
                  >
                    <strong>fast</strong><span>{{ $t('personalization.fastResponseMode') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                  </button>
                  <button
                    type="button"
                    class="settings-menu-option"
                    :class="{ selected: roleForm.thinking_mode === 'thinking' }"
                    @click="roleForm.thinking_mode = 'thinking'; closeDropdown()"
                  >
                    <strong>thinking</strong><span>{{ $t('personalization.thinkingReasoningMode') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                  </button>
                </div>
              </div>
            </div>
            <div class="settings-select-row">
              <span class="settings-row-copy">
                <span class="settings-row-title">{{ $t('personalization.modelTitle') }}</span>>
                <span class="settings-row-desc">{{ $t('personalization.roleModelEmptyDesc') }}</span>
              </span>
              <div
                class="settings-select-wrap"
                :class="{ open: activeDropdown === 'role-model' }"
                @click.stop
              >
                <button
                  type="button"
                  class="settings-select-button"
                  @click="toggleDropdown('role-model')"
                >
                  {{ roleForm.model_key || $t('personalization.defaultModelOption') }}
                  <span class="select-chevron" aria-hidden="true"></span>
                </button>
                <div
                  :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                  :style="activeDropdown === 'role-model' ? floatingMenuStyle : undefined"
                >
                  <button
                    type="button"
                    class="settings-menu-option"
                    :class="{ selected: !roleForm.model_key }"
                    @click="roleForm.model_key = ''; closeDropdown()"
                  >
                    <strong>{{ $t('personalization.defaultModelOption') }}</strong><span>{{ $t('personalization.roleDefaultModelDesc') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                  </button>
                  <button
                    v-for="m in subAgentModels"
                    :key="m.name"
                    type="button"
                    class="settings-menu-option"
                    :class="{ selected: roleForm.model_key === m.name }"
                    @click="roleForm.model_key = m.name; closeDropdown()"
                  >
                    <strong>{{ m.name }}</strong><span>{{ m.modes }} · {{ m.multimodal || $t('personalization.textOnly') }}</span><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                  </button>
                </div>
              </div>
            </div>
            <div class="settings-textarea-row">
              <span class="settings-row-title">{{ $t('personalization.promptBodyTitle') }}</span>
              <textarea
                :value="roleForm.body_prompt"
                ::placeholder="$t('personalization.promptBodyPlaceholder')"
                @input="roleForm.body_prompt = ($event.target as HTMLTextAreaElement).value"
              ></textarea>
            </div>
          </div>
          <div class="role-editor-footer">
            <button type="button" class="settings-secondary-button" @click="closeRoleEditor">{{ $t('common.cancel') }}</button>
            <button
              type="button"
              class="settings-primary-button"
              :disabled="roleSaving || !roleForm.role_id || !roleForm.name || !roleForm.body_prompt"
              @click="saveRole"
            >
              {{ roleSaving ? $t('personalization.saving') : $t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  </section>
</template>

<style scoped>
.sub-agent-role-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sub-agent-role-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 11px 0;
  border-bottom: 1px solid var(--theme-control-border);
}
.sub-agent-role-info {
  flex: 1;
  min-width: 0;
}
.sub-agent-role-name {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sub-agent-role-tag {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 400;
}
.sub-agent-role-tag.tag-preset {
  background: color-mix(in srgb, var(--text-secondary) 15%, transparent);
  color: var(--text-secondary);
}
.sub-agent-role-tag.tag-custom {
  background: color-mix(in srgb, var(--accent) 15%, transparent);
  color: var(--accent);
}
.sub-agent-role-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin-top: 3px;
  line-height: 1.38;
}
.sub-agent-role-meta {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.7;
  margin-top: 2px;
}
.sub-agent-role-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

/* 角色编辑弹窗 — 复用 settings-redesign-card */
.role-editor-drawer-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2147483647;
}
.role-editor-drawer-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  width: min(648px, calc(100vw - 40px));
  height: min(646px, calc(100vh - 40px));
  height: min(646px, calc(100dvh - 40px));
  padding: 22px 24px 18px;
  border-radius: 14px;
  background: var(--theme-surface-soft);
  border: 1px solid var(--theme-control-border);
}

@media (max-width: 760px) {
  .role-editor-drawer-card {
    width: 100%;
    height: 100vh;
    height: 100dvh;
    border-radius: 0;
    border: 0;
    padding: calc(14px + env(safe-area-inset-top, 0px)) 16px calc(18px + env(safe-area-inset-bottom, 0px));
  }
}
.role-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-shrink: 0;
}
.role-editor-header h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.role-editor-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: visible;
  min-height: 0;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.role-editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  flex-shrink: 0;
}
.settings-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.settings-section-title {
  font-size: 14px;
  font-weight: 550;
  color: var(--text-primary);
}
.settings-empty-hint {
  color: var(--text-secondary);
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
</style>

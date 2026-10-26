<template>
  <div :class="mobile ? 'mobile-panel-topbar' : 'conversation-header-content'">
    <div
      :class="mobile ? 'mobile-panel-topbar__blur' : 'conversation-ribbon__blur'"
      aria-hidden="true"
    ></div>
    <slot name="leading" />
    <button
      type="button"
      :class="[
        mobile ? 'mobile-topbar-selector' : 'conversation-ribbon__selector',
        { open: headerMenuOpen }
      ]"
      data-tutorial="header-model-selector"
      @click.stop="toggleHeaderMenu"
      :disabled="!isConnected"
    >
      <span :class="mobile ? 'mobile-selector-label' : 'selector-label'">
        <span :class="mobile ? 'mobile-selector-model' : 'selector-model'">{{
          currentModelLabel
        }}</span>
        <span v-if="headerEffortLabel" :class="mobile ? 'mobile-selector-mode' : 'selector-mode'">{{
          headerEffortLabel
        }}</span>
      </span>
      <svg viewBox="0 0 24 24" aria-hidden="true" class="selector-caret">
        <path
          d="M7 10l5 5 5-5"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>

    <span v-if="!mobile" class="conversation-ribbon__text">{{
      titleTypingText || currentConversationTitle
    }}</span>

    <transition name="header-menu">
      <div
        v-if="headerMenuOpen"
        class="model-mode-dropdown"
        :class="{ 'model-mode-dropdown--mobile': mobile }"
        ref="headerMenu"
      >
        <div class="dropdown-column" data-tutorial="header-model-options">
          <div class="dropdown-search">
            <svg
              class="dropdown-search-icon"
              viewBox="0 0 24 24"
              width="13"
              height="13"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="1.8" />
              <line
                x1="16.5"
                y1="16.5"
                x2="21"
                y2="21"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
              />
            </svg>
            <input
              v-model="searchQuery"
              type="text"
              class="dropdown-search-input"
              :placeholder="$t('appCore.searchModel')"
              @click.stop
            />
          </div>
          <div class="dropdown-list dropdown-list--models">
            <div v-for="group in filteredGroupedModelOptions" :key="group.id" class="model-group">
              <button
                type="button"
                class="dropdown-group-header"
                :aria-expanded="!isModelGroupCollapsed(group.id) || !!modelMenuSearchQuery"
                @click.stop="toggleModelGroup(group.id)"
              >
                <span class="group-name">{{ group.name }}</span>
                <svg
                  class="group-chevron"
                  :class="{
                    collapsed: isModelGroupCollapsed(group.id) && !modelMenuSearchQuery
                  }"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path
                    d="M7 10l5 5 5-5"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.8"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
              <div
                class="model-group-body"
                :class="{
                  collapsed: isModelGroupCollapsed(group.id) && !modelMenuSearchQuery
                }"
              >
                <div class="model-group-body__inner">
                  <button
                    v-for="option in group.options"
                    :key="option.key"
                    type="button"
                    class="dropdown-item"
                    :class="{
                      active: option.key === currentModelKey,
                      disabled: option.disabled
                    }"
                    @click.stop="handleHeaderModelSelect(option.key, option.disabled)"
                    :disabled="streamingMessage || !isConnected || option.disabled"
                  >
                    <div class="item-label">{{ option.label }}</div>
                    <span v-if="option.key === currentModelKey" class="item-check">✓</span>
                  </button>
                </div>
              </div>
            </div>
            <div v-if="!filteredGroupedModelOptions.length" class="dropdown-empty-hint">
              <template v-if="hasAnyModels">{{ $t('appCore.noModelsMatched') }}</template>
              <template v-else>
                <div class="empty-hint-text">{{ $t('appCore.noModelsConfigured') }}</div>
                <button type="button" class="empty-hint-action" @click.stop="openSettingsProviders">
                  {{ $t('appCore.goAddProvider') }}
                </button>
              </template>
            </div>
          </div>
          <button
            type="button"
            class="dropdown-item dropdown-item--manage"
            @click.stop="openManageModels"
          >
            <div class="item-label">
              <svg
                class="manage-icon"
                viewBox="0 0 18 18"
                width="13"
                height="13"
                aria-hidden="true"
              >
                <g fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                  <line x1="2" y1="5" x2="16" y2="5" />
                  <circle cx="6.5" cy="5" r="1.8" />
                  <line x1="2" y1="9" x2="16" y2="9" />
                  <circle cx="11.5" cy="9" r="1.8" />
                  <line x1="2" y1="13" x2="16" y2="13" />
                  <circle cx="7.5" cy="13" r="1.8" />
                </g>
              </svg>
              {{ $t('appCore.manageModels') }}
            </div>
          </button>
        </div>

        <div class="dropdown-column" data-tutorial="header-runmode-options">
          <div class="dropdown-title">{{ $t('appCore.runMode') }}</div>
          <button
            v-for="option in headerRunModeOptions"
            :key="option.value"
            type="button"
            class="dropdown-item"
            :class="{ active: option.value === resolvedRunMode }"
            @click.stop="handleHeaderRunModeSelect(option.value)"
            :disabled="streamingMessage || !isConnected"
          >
            <div class="item-label">{{ option.label }}</div>
            <div class="item-desc">{{ option.desc }}</div>
            <span v-if="option.value === resolvedRunMode" class="item-check">✓</span>
          </button>
          <div v-if="showEffortSlider" class="dropdown-effort">
            <EffortSlider :model-value="reasoningEffort" @update:model-value="setReasoningEffort" />
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import EffortSlider from '@/components/input/EffortSlider.vue';
const props = defineProps<{
  mobile?: boolean;
  headerMenuOpen: boolean;
  isConnected: boolean;
  currentModelLabel: string;
  headerEffortLabel: string;
  titleTypingText?: string;
  currentConversationTitle: string;
  modelMenuSearchQuery: string;
  filteredGroupedModelOptions: any[];
  isModelGroupCollapsed: (id: string) => boolean;
  toggleModelGroup: (id: string) => void;
  currentModelKey: string;
  streamingMessage: boolean;
  toggleHeaderMenu: () => void;
  handleHeaderModelSelect: (key: string, disabled?: boolean) => void;
  hasAnyModels: boolean;
  openSettingsProviders: () => void;
  openManageModels: () => void;
  headerRunModeOptions: Array<{ value: string; label: string; desc: string }>;
  resolvedRunMode: string;
  handleHeaderRunModeSelect: (mode: any) => void;
  showEffortSlider: boolean;
  reasoningEffort: any;
  setReasoningEffort: (value: any) => void;
}>();
const emit = defineEmits(['update:modelMenuSearchQuery']);
const searchQuery = computed({
  get: () => props.modelMenuSearchQuery,
  set: (value) => emit('update:modelMenuSearchQuery', value)
});
</script>
<style scoped>
.conversation-header-content {
  display: contents;
}
</style>

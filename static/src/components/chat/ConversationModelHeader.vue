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
      data-model-picker-trigger
      aria-haspopup="dialog"
      :aria-expanded="headerMenuOpen"
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

    <div
      v-if="mobile"
      class="mobile-topbar-title"
      :title="currentConversationTitle || $t('overlay.unnamedConversation')"
    >
      {{ currentConversationTitle || $t('overlay.unnamedConversation') }}
    </div>
    <span v-else class="conversation-ribbon__text">{{
      titleTypingText || currentConversationTitle
    }}</span>

    <transition name="header-menu">
      <div
        v-if="headerMenuOpen"
        class="model-mode-dropdown"
        :class="{ 'model-mode-dropdown--mobile': mobile }"
        role="dialog"
        :aria-label="currentModelLabel"
        ref="headerMenu"
      >
        <ModelEffortPanel
          :mobile="mobile"
          :is-connected="isConnected"
          :streaming-message="streamingMessage"
          v-model:model-menu-search-query="searchQuery"
          :filtered-grouped-model-options="filteredGroupedModelOptions"
          :is-model-group-collapsed="isModelGroupCollapsed"
          :toggle-model-group="toggleModelGroup"
          :current-model-key="currentModelKey"
          :handle-header-model-select="handleHeaderModelSelect"
          :has-any-models="hasAnyModels"
          :open-settings-providers="openSettingsProviders"
          :open-manage-models="openManageModels"
          :reasoning="reasoning"
          :thinking="thinking"
          :reasoning-effort="reasoningEffort"
          :set-reasoning="setReasoning"
          :is-chatgpt-model="isChatgptModel"
          :supports-chatgpt-fast-mode="supportsChatgptFastMode"
          :chatgpt-fast-mode="chatgptFastMode"
          :chatgpt-fast-mode-saving="chatgptFastModeSaving"
          :toggle-chatgpt-fast-mode="toggleChatgptFastMode"
        />
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { ReasoningEffort } from '@/stores/personalization';
import type { ReasoningCapability, ReasoningChange } from '@/utils/reasoningStops';
import ModelEffortPanel from './ModelEffortPanel.vue';
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
  reasoning: ReasoningCapability;
  thinking: boolean;
  reasoningEffort: ReasoningEffort | null;
  setReasoning: (change: ReasoningChange) => unknown;
  isChatgptModel: boolean;
  supportsChatgptFastMode: boolean;
  chatgptFastMode: boolean;
  chatgptFastModeSaving?: boolean;
  toggleChatgptFastMode: () => void;
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

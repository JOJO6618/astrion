<template>
  <AppShell :download-file="unavailableDownload" :download-folder="unavailableDownload">
    <div class="main-container">
      <ConversationSidebar
        v-if="!isMobile"
        chat-mode
        :icon-style="iconStyle"
        :current-conversation-id="chat.cid"
        :current-task-in-progress="chat.running"
        :host-workspace-enabled="false"
        :group-by-workspace="false"
        :workspaces="[]"
        @toggle="isMobile ? (mobileOpen = false) : ui.toggleSidebar()"
        @create="navigate('')"
        @refresh="run(() => chat.list())"
        @search="search"
        @search-submit="search"
        @select-workspace-conversation="navigate($event.conversationId)"
        @personal="openPersonalPage"
        @delete="deleteConversation"
        @duplicate="duplicateConversation"
      />
      <main
        class="chat-container"
        :class="{ 'chat-container--mobile': isMobile, 'has-title-ribbon': !isMobile }"
        :style="containerStyle"
        @dragover.prevent
        @drop.prevent="upload($event.dataTransfer?.files || null)"
      >
        <div
          ref="ribbon"
          :class="[
            isMobile ? 'mobile-panel-trigger' : 'conversation-ribbon',
            { 'is-hidden': isMobile && mobileOpen }
          ]"
        >
          <ConversationModelHeader
            :mobile="isMobile"
            :header-menu-open="headerMenuOpen"
            :is-connected="!!chat.settings"
            :current-model-label="currentModel?.label || $t('chatMode.model')"
            :header-effort-label="
              chat.thinking && currentModel?.supportsThinking
                ? String(chat.effort ?? '')
                    .trim()
                    .toLowerCase() || 'default'
                : ''
            "
            :current-conversation-title="title"
            v-model:model-menu-search-query="modelMenuSearchQuery"
            :filtered-grouped-model-options="groups"
            :is-model-group-collapsed="(id) => collapsedGroups.has(id)"
            :toggle-model-group="
              (id) =>
                collapsedGroups.has(id) ? collapsedGroups.delete(id) : collapsedGroups.add(id)
            "
            :current-model-key="chat.model"
            :streaming-message="chat.busy"
            :toggle-header-menu="() => (headerMenuOpen = !headerMenuOpen)"
            :handle-header-model-select="selectModel"
            :has-any-models="!!modelOptions.length"
            :open-settings-providers="() => openSettings('providers')"
            :open-manage-models="() => openSettings('chat')"
            :header-run-mode-options="modeOptions"
            :resolved-run-mode="chat.thinking ? 'thinking' : 'fast'"
            :handle-header-run-mode-select="
              (mode) => configure({ thinking_mode: mode === 'thinking' })
            "
            :show-effort-slider="chat.thinking && !!currentModel?.supportsThinking"
            :reasoning-effort="chat.effort"
            :set-reasoning-effort="(value) => configure({ reasoning_effort: value })"
            :is-chatgpt-model="!!currentModel?.isChatgptModel"
            :supports-chatgpt-fast-mode="!!currentModel?.supportsChatgptFastMode"
            :chatgpt-fast-mode="chat.fastMode"
            :chatgpt-fast-mode-saving="chat.configuring"
            :toggle-chatgpt-fast-mode="() => configure({ chatgpt_fast_mode: !chat.fastMode })"
          >
            <template v-if="isMobile" #leading>
              <button
                type="button"
                class="mobile-panel-fab"
                :aria-label="$t('chatMode.showSidebar')"
                @click="mobileOpen = !mobileOpen"
              >
                <img :src="ICONS.alignLeft" alt="" aria-hidden="true" />
              </button>
            </template>
          </ConversationModelHeader>
        </div>
        <ChatArea
          ref="area"
          chat-mode
          :messages="messages"
          :editing-message-id="editing?.message_id"
          :icon-style="iconStyle"
          :expanded-blocks="expandedBlocks"
          :streaming-message="chat.running"
          :toggle-block="toggleBlock"
          :handle-thinking-scroll="noop"
          :render-markdown="emptyMarkdown"
          :get-tool-icon="getToolIcon"
          :get-tool-status-text="getToolStatusText"
          :get-tool-animation-class="getToolAnimationClass"
          :get-tool-description="getToolDescription"
          :format-search-topic="formatSearchTopic"
          :format-search-time="formatSearchTime"
          :format-search-domains="formatSearchDomains"
          :history-loading="chat.loading"
        >
          <template #message-editor="{ message }">
            <ChatMessageEditor
              :key="message.messageId"
              :message="message.raw"
              :busy="chat.busy"
              :saving="saving"
              @save="saveEdit"
              @cancel="editing = null"
            />
          </template>
          <template #user-actions="{ message }">
            <ChatBranchActions
              :message="message.raw"
              :busy="chat.busy"
              :versions="versions(message.raw)"
              :index="versionIndex(message.raw)"
              @edit="editing = $event"
              @browse="chat.browse"
              @error="chat.report"
            />
          </template>
          <template #text-editor="{ message, action }">
            <ChatMessageEditor
              v-if="editing && isFirstEditedText(message, action)"
              :key="editing.message_id"
              :message="editing"
              :busy="chat.busy"
              :saving="saving"
              @save="saveEdit"
              @cancel="editing = null"
            />
          </template>
          <template #text-actions="{ action }">
            <ChatBranchActions
              v-if="canEditIntermediate(action)"
              inline
              :message="messageForAction(action)!"
              :busy="chat.busy"
              :versions="[]"
              :index="0"
              @edit="editing = $event"
            />
          </template>
          <template #assistant-actions="{ message, tokenSpeed, copy, copied }">
            <ChatBranchActions
              v-if="chat.finalMessage(message.raw)"
              final
              :message="message.raw"
              :token-speed="tokenSpeed"
              :copy-text="copy"
              :copied="copied"
              :busy="chat.busy"
              :versions="versions(message.raw)"
              :index="versionIndex(message.raw)"
              @edit="editing = $event"
              @regenerate="run(() => chat.regenerate($event))"
              @browse="chat.browse"
              @error="chat.report"
            />
          </template>
          <template #after-messages>
            <template v-if="!chat.viewingLeaf && !chat.running && !chat.loading">
              <HandoffCard
                v-for="suggestion in suggestions"
                :key="`${chat.cid}:${suggestion.id}:${chat.presentationId}`"
                :conversation-id="chat.cid"
                :handoff-id="suggestion.id"
                :presentation-id="chat.presentationId"
                :initial-state="suggestion"
                @changed="chat.reload()"
                @error="chat.report"
              />
            </template>
            <button
              v-if="chat.viewingLeaf"
              type="button"
              class="chat-branch-return"
              @click="chat.viewingLeaf = null"
            >
              {{ $t('chatMode.latest') }}
            </button>
            <button
              v-if="retryTurn && !chat.busy"
              type="button"
              class="chat-branch-return"
              @click="
                run(() => chat.send('', [], { regenerate_turn_id: retryTurn!.id, parent_id: null }))
              "
            >
              {{ $t('chatMode.retry') }}
            </button>
          </template>
        </ChatArea>
        <div v-if="!messages.length && !chat.loading" class="blank-hero-overlay">
          <StatusAvatar
            v-if="personalization.form.show_status_avatar !== false"
            mode="idle"
            :size="58"
            tracking
          />
          <p class="blank-hero-text">
            {{ modelOptions.length ? $t('chatMode.empty') : $t('chatMode.noModels') }}
          </p>
        </div>
        <div class="composer-container" :class="{ 'blank-hero-mode': !messages.length }">
          <InputComposer
            ref="composer"
            :key="draft.identity"
            chat-mode
            :network-enabled="chat.network"
            :media-preview-url="preview"
            :input-message="draft.text"
            :input-is-multiline="draft.text.includes('\n')"
            :input-is-focused="inputFocused"
            :is-connected="!!chat.settings"
            :streaming-message="chat.running"
            :input-locked="chat.loading || !draft.ready || saving"
            :uploading="uploading"
            :media-uploading="uploading"
            :thinking-mode="chat.thinking"
            :run-mode="chat.thinking ? 'thinking' : 'fast'"
            :quick-menu-open="false"
            :tool-menu-open="false"
            :mode-menu-open="false"
            :model-menu-open="false"
            :tool-settings="[]"
            :tool-settings-loading="false"
            :settings-open="false"
            :compressing="false"
            :current-conversation-id="chat.cid"
            :icon-style="iconStyle"
            :tool-category-icon="() => ''"
            :model-options="modelOptions"
            :current-model-key="chat.model"
            :selected-images="images"
            :selected-videos="videos"
            :selected-files="[]"
            :block-tool-toggle="true"
            :block-realtime-terminal="true"
            :block-focus-panel="true"
            :block-compress-conversation="true"
            :block-conversation-review="true"
            current-permission-mode="unrestricted"
            :permission-menu-open="false"
            :permission-options="[]"
            :current-context-tokens="contextTokens"
            :context-token-limit="chat.settings?.settings.chat_deep_compress_trigger_tokens"
            :versioning-enabled="false"
            :workspace-kind="null"
            :main-chat-idle="!chat.running"
            :avatar-status="
              personalization.form.show_status_avatar !== false && messages.length
                ? avatarStatus
                : null
            "
            @update:input-message="draft.text = $event"
            @input-focus="inputFocused = true"
            @input-blur="inputFocused = false"
            @toggle-quick-menu="pickMedia"
            @file-selected="upload"
            @paste-files="upload"
            @remove-image="removeMedia"
            @remove-video="removeMedia"
            @toggle-chat-network="run(chat.toggleNetwork)"
            @send-message="send"
            @send-or-stop="chat.running ? run(chat.stop) : send()"
            @composer-height-change="setComposerHeight"
          />
        </div>
      </main>
    </div>
    <ImageLightbox />
    <PersonalizationDrawer />
    <transition name="mobile-panel-overlay">
      <div
        v-if="isMobile && mobileOpen"
        class="mobile-panel-overlay mobile-panel-overlay--left"
        @click.self="mobileOpen = false"
      >
        <div class="mobile-panel-sheet mobile-panel-sheet--conversation">
          <ConversationSidebar
            class="mobile-overlay-content"
            chat-mode
            :collapsed="false"
            :icon-style="iconStyle"
            :current-conversation-id="chat.cid"
            :current-task-in-progress="chat.running"
            :host-workspace-enabled="false"
            :group-by-workspace="false"
            :workspaces="[]"
            collapse-button-variant="close"
            @toggle="mobileOpen = false"
            @create="navigate('')"
            @search="search"
            @search-submit="search"
            @personal="openPersonalPage"
            @select-workspace-conversation="navigate($event.conversationId)"
            @delete="deleteConversation"
            @duplicate="duplicateConversation"
          />
        </div>
      </div>
    </transition>
  </AppShell>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import AppShell from '@/components/shell/AppShell.vue';
import ConversationSidebar from '@/components/sidebar/ConversationSidebar.vue';
import ChatArea from '@/components/chat/ChatArea.vue';
import InputComposer from '@/components/input/InputComposer.vue';
import ConversationModelHeader from '@/components/chat/ConversationModelHeader.vue';
import StatusAvatar from '@/components/avatar/StatusAvatar.vue';
import ImageLightbox from '@/components/overlay/ImageLightbox.vue';
import PersonalizationDrawer from '@/components/personalization/PersonalizationDrawer.vue';
import type { PersonalDrawerTab } from '@/stores/personalization';
import {
  setupShowImageObserver,
  teardownShowImageObserver,
  setupImagePreviewDelegation
} from '@/app/bootstrap';
import { systemMethods } from '@/app/methods/ui/system';
import ChatBranchActions from './ChatBranchActions.vue';
import ChatMessageEditor from './ChatMessageEditor.vue';
import HandoffCard from './HandoffCard.vue';
import { useChatPage } from '@/chat/useChatPage';
import { ICONS } from '@/utils/icons';
import {
  getToolIcon,
  getToolStatusText as baseGetToolStatusText,
  getToolAnimationClass,
  getToolDescription,
  formatSearchTopic,
  formatSearchTime,
  formatSearchDomains
} from '@/utils/chatDisplay';
const page = reactive(useChatPage());
const { chat, draft, ui, personalization, collapsedGroups, expandedBlocks } = page;
// Keep refs reactive when exposing the shared component state to the template.
import { toRefs } from 'vue';
const {
  headerMenuOpen,
  modelMenuSearchQuery,
  inputFocused,
  uploading,
  editing,
  saving,
  mobileOpen,
  isMobile,
  messages,
  avatarStatus,
  title,
  modelOptions,
  currentModel,
  groups,
  modeOptions,
  suggestions,
  retryTurn,
  contextTokens,
  images,
  videos
} = toRefs(page);
const {
  preview,
  removeMedia,
  run,
  configure,
  openSettings,
  messageForAction,
  versions,
  versionIndex,
  navigate,
  send,
  upload,
  saveEdit,
  deleteConversation,
  duplicateConversation,
  search
} = page;
const area = ref<InstanceType<typeof ChatArea>>();
const composer = ref<InstanceType<typeof InputComposer>>();
const ribbon = ref<HTMLElement>();
const iconStyle = (key: string) => ({
  '--icon-src': `url(${ICONS[key as keyof typeof ICONS] || ICONS.chatBubble})`
});
const noop = () => {};
const emptyMarkdown = () => '';
const unavailableDownload = async () => {};
function openPersonalPage(tab?: PersonalDrawerTab) {
  mobileOpen.value = false;
  void run(() => personalization.openDrawer(tab));
}
function getToolStatusText(tool: any) {
  return baseGetToolStatusText(tool, {
    intentEnabled: personalization.form.tool_intent_enabled ?? true
  });
}
function canEditIntermediate(action: any) {
  const message = messageForAction(action);
  return (
    !!message &&
    !message.live &&
    !chat.finalMessage(message) &&
    editing.value?.message_id !== message.message_id
  );
}
function isFirstEditedText(message: any, action: any) {
  return (
    message.actions?.find(
      (item: any) => item.type === 'text' && item.responseMessageId === editing.value?.message_id
    )?.id === action.id
  );
}
function toggleBlock(id: string) {
  expandedBlocks.has(id) ? expandedBlocks.delete(id) : expandedBlocks.add(id);
}
function pickMedia() {
  composer.value?.fileUploadInput?.click();
}
const containerStyle = ref<Record<string, string>>({});
function setComposerHeight(metrics: {
  reservedHeight: number;
  visualHeight: number;
  growth: number;
}) {
  const height = Math.max(90, Math.round(Number(metrics.reservedHeight) || 90));
  containerStyle.value = {
    '--composer-base-height': 'calc(90px + var(--app-bottom-inset, 0px))',
    '--composer-reserved-height': `calc(${height}px + var(--app-bottom-inset, 0px))`,
    '--composer-growth-height': `${Math.max(0, height - 90)}px`
  };
}
function selectModel(key: string, disabled?: boolean) {
  if (!disabled) {
    void configure({ model_key: key });
    headerMenuOpen.value = false;
  }
}
function outside(event: MouseEvent) {
  if (!ribbon.value?.contains(event.target as Node)) headerMenuOpen.value = false;
}
const copyCode = systemMethods.handleCopyCodeClick.bind(systemMethods);
onMounted(() => {
  document.addEventListener('click', outside);
  document.addEventListener('click', copyCode);
  setupImagePreviewDelegation();
  setupShowImageObserver({ allowWorkspaceContent: false });
});
onBeforeUnmount(() => {
  document.removeEventListener('click', outside);
  document.removeEventListener('click', copyCode);
  teardownShowImageObserver();
});
</script>
<style lang="scss">
.chat-branch-return {
  display: block;
  margin: 16px auto;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--surface-base);
  color: var(--text-secondary);
  cursor: pointer;
}
</style>

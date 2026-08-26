<template>
  <div class="input-area compact-input-area" ref="inputAreaRoot">
    <div class="stadium-input-wrapper" ref="stadiumShellOuter">
      <div
        class="runtime-queue-list"
        :class="{ 'runtime-queue-list--empty': !runtimeQueuedMessagesForRender.length }"
        ref="runtimeQueueList"
      >
        <div
          v-for="(entry, index) in runtimeQueuedMessagesForRender"
          :key="entry.id"
          class="runtime-queue-item"
          :class="{
            'runtime-queue-item--top': index === 0
          }"
          :data-runtime-queue-id="entry.id || ''"
          :ref="bindRuntimeQueueItemRef(entry.id)"
        >
          <span class="runtime-queue-item__text">{{ entry.text || '' }}</span>
          <button
            type="button"
            class="runtime-queue-item__action"
            @click.stop="$emit('guide-runtime-message', entry.id)"
          >
            引导
          </button>
          <button
            type="button"
            class="runtime-queue-item__action runtime-queue-item__action--danger"
            @click.stop="$emit('delete-runtime-message', entry.id)"
          >
            删除
          </button>
        </div>
      </div>
      <transition
        name="skill-slash-menu-motion"
        @before-enter="handleSlashMenuTransitionStart"
        @before-leave="handleSlashMenuTransitionStart"
        @after-enter="handleSlashMenuAfterEnter"
        @after-leave="handleSlashMenuAfterLeave"
      >
        <div
          v-if="skillSlashMenuOpen"
          :key="slashMenuMode"
          class="skill-slash-menu-wrapper"
          role="listbox"
          :aria-label="slashMenuAriaLabel"
        >
          <div ref="skillSlashList" class="skill-slash-menu" @scroll="onSlashScroll">
            <button
              v-for="(item, index) in activeSlashMenuItems"
              :key="item.id"
              type="button"
              class="skill-slash-item"
              :class="{
                'skill-slash-item--active': index === skillSlashActiveIndex && !slashManualScrolled,
                'skill-slash-item--disabled': item.disabled
              }"
              role="option"
              :aria-selected="index === skillSlashActiveIndex"
              :disabled="item.disabled"
              @mousedown.prevent="selectSlashMenuItem(index)"
            >
              <span class="skill-slash-item__name">{{ item.label }}</span>
              <span class="skill-slash-item__description">{{ item.description }}</span>
            </button>
            <div v-if="!activeSlashMenuItems.length" class="skill-slash-empty">
              {{ slashMenuEmptyText }}
            </div>
          </div>
          <div class="skill-slash-menu__highlight" :style="slashHighlightStyle" />
        </div>
      </transition>
      <FileAtMenu
        ref="fileAtMenuRef"
        :visible="fileAtOpen"
        :items="fileAtItems"
        :active-index="fileAtActiveIndex"
        :loading="fileAtLoading"
        :host-mode="!!props.hostMode"
        :menu-style="fileAtMenuStyle"
        @select="selectFileAtItemByIndex"
        @hover="fileAtActiveIndex = $event"
      />
      <transition name="floating-status-motion">
        <div v-if="showComposerAvatar" class="composer-avatar" @click.stop="handleAvatarPoke">
          <StatusAvatar
            :mode="props.avatarStatus.mode"
            :tool-keys="props.avatarStatus.toolKeys || []"
            :tracking="props.avatarStatus.tracking !== false"
            :size="45"
            hex-background
          />
          <Transition name="composer-avatar-text-fade" mode="out-in">
            <span
              v-if="avatarPokeText"
              :key="`poke-${avatarPokeText}`"
              class="composer-avatar__text"
              :class="{
                'composer-avatar__text--right': !floatingStatusVisible,
                'composer-avatar__text--top': floatingStatusVisible
              }"
            >
              {{ avatarPokeText }}
            </span>
            <span
              v-else-if="composerAvatarText"
              :key="composerAvatarTextKey"
              class="composer-avatar__text"
              :class="{
                'composer-avatar__text--right': !floatingStatusVisible,
                'composer-avatar__text--top': floatingStatusVisible
              }"
            >
              {{ composerAvatarText }}
            </span>
          </Transition>
        </div>
      </transition>
      <transition name="floating-status-motion">
        <div v-if="floatingStatusVisible" class="floating-status-row" @click.stop>
          <div class="floating-project-status" @click.stop>
            <span v-if="projectGitSummaryForRender" class="floating-project-status__left">
              <span class="floating-project-status__menu-wrap">
                <button
                  type="button"
                  class="floating-project-status__button floating-project-status__button--project"
                  :class="{
                    'floating-project-status__button--active': projectGitMenuOpen === 'project'
                  }"
                  @click.stop="toggleProjectGitMenu('project')"
                >
                  {{ projectGitSummaryForRender.projectName }}
                </button>
                <div
                  v-if="projectGitMenuOpen === 'project'"
                  class="floating-project-status__submenu floating-project-status__submenu--project"
                  @click.stop
                >
                  <button type="button" @click.stop="openProjectInFileManager">
                    在文件管理器中打开
                  </button>
                  <button type="button" @click.stop="copyProjectPath">复制地址</button>
                </div>
              </span>
              <span class="floating-project-status__menu-wrap">
                <button
                  type="button"
                  class="floating-project-status__button floating-project-status__button--branch"
                  :class="{
                    'floating-project-status__button--active': projectGitMenuOpen === 'branch'
                  }"
                  @click.stop="toggleProjectGitMenu('branch')"
                >
                  {{ projectGitSummaryForRender.branch }}
                </button>
                <div
                  v-if="projectGitMenuOpen === 'branch'"
                  class="floating-project-status__submenu floating-project-status__submenu--branch"
                  @click.stop
                >
                  <button type="button" @click.stop="copyProjectBranch">复制分支名称</button>
                </div>
              </span>
            </span>
            <span class="floating-project-status__right">
              <span v-if="goalRunning" class="floating-project-status__notice">目标模式运行中</span>
              <span v-else-if="goalModeArmed" class="floating-project-status__notice"
                >目标模式就绪</span
              >
              <button
                v-if="pendingUserQuestionCount > 0"
                type="button"
                class="floating-project-status__notice floating-project-status__notice--button"
                @click.stop="$emit('restore-user-question')"
              >
                等待回答问题
              </button>
              <button
                v-if="(activeSubAgentCount || 0) > 0"
                type="button"
                class="floating-project-status__notice floating-project-status__notice--button"
                @click.stop="$emit('stop-all-sub-agents')"
              >
                {{ activeSubAgentCount }} 个子智能体运行中
              </button>
              <button
                v-if="terminalCount > 0"
                type="button"
                class="floating-project-status__terminal"
                @click.stop="$emit('realtime-terminal')"
              >
                {{ terminalCount }} 个终端
              </button>
              <button
                v-if="projectGitSummaryForRender"
                type="button"
                class="floating-project-status__stats"
                @click.stop="$emit('open-git-changes-panel')"
              >
                <span class="floating-project-status__add"
                  >+<RollingNumber :value="projectGitSummaryForRender.additions"
                /></span>
                <span class="floating-project-status__del"
                  >-<RollingNumber :value="projectGitSummaryForRender.deletions"
                /></span>
              </button>
            </span>
          </div>
        </div>
      </transition>
      <div
        class="stadium-shell"
        ref="compactInputShell"
        :class="{
          'is-multiline': inputIsMultiline,
          'is-focused': inputIsFocused,
          'has-text': (inputMessage || '').trim().length > 0
        }"
      >
        <input
          type="file"
          ref="fileUploadInput"
          class="file-input-hidden"
          multiple
          @change="onFileChange"
        />
        <div class="input-stack">
          <div
            v-if="
              (selectedImages && selectedImages.length) ||
              (selectedFiles && selectedFiles.length)
            "
            class="image-inline-row"
          >
            <div
              class="image-thumbnail-wrapper"
              v-for="img in selectedImages"
              :key="img"
              @click.stop="previewImage(img)"
            >
              <img :src="getPreviewUrl(img)" :alt="formatImageName(img)" class="image-thumbnail" />
              <button
                type="button"
                class="image-remove-btn-hover"
                @click.stop="$emit('remove-image', img)"
              >
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
                  <path
                    d="M2.5 2.5l5 5M7.5 2.5l-5 5"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                  />
                </svg>
              </button>
            </div>
            <FileChips
              v-if="selectedFiles && selectedFiles.length"
              :files="selectedFiles"
              removable
              @remove="$emit('remove-file', $event)"
            />
          </div>
          <div
            v-if="selectedVideos && selectedVideos.length"
            class="image-inline-row video-inline-row"
          >
            <div class="image-thumbnail-wrapper" v-for="video in selectedVideos" :key="video">
              <img
                :src="getPreviewUrl(video)"
                :alt="formatImageName(video)"
                class="image-thumbnail"
              />
              <button
                type="button"
                class="image-remove-btn-hover"
                @click.stop="$emit('remove-video', video)"
              >
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
                  <path
                    d="M2.5 2.5l5 5M7.5 2.5l-5 5"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                  />
                </svg>
              </button>
            </div>
          </div>
          <div class="input-row">
            <div class="stadium-input-rich">
              <EditorContent
                :editor="editor"
                class="stadium-input stadium-input-tiptap"
                :class="{ 'is-disabled': !isConnected || inputLocked }"
                @focusin="$emit('input-focus')"
                @focusout="onInputBlur"
              />
            </div>
          </div>
          <div class="input-actions">
            <div class="input-actions-left">
            <button
              type="button"
              class="stadium-btn add-btn"
              data-tutorial="quick-menu-open"
              @click.stop="$emit('toggle-quick-menu')"
              :disabled="!isConnected"
              aria-label="快捷菜单"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M5 12h14" /><path d="M12 5v14" />
              </svg>
            </button>
            <div class="agent-type-switcher" :class="{ 'is-disabled': agentTypeLocked }">
              <button
                type="button"
                class="agent-type-switcher__btn"
                @click.stop="$emit('toggle-agent-type-menu')"
                :disabled="!isConnected || agentTypeLocked"
                :aria-expanded="agentTypeMenuOpen"
                aria-haspopup="true"
              >
                <span>{{ agentTypeLabel }}</span>
                <span class="agent-type-switcher__caret" :class="{ open: agentTypeMenuOpen }" aria-hidden="true">›</span>
              </button>
              <div v-if="agentTypeMenuOpen && !agentTypeLocked" class="agent-type-switcher__menu" @click.stop>
                <button
                  v-for="option in agentTypeOptions"
                  :key="option.value"
                  type="button"
                  class="agent-type-switcher__menu-item"
                  :class="{ 'is-active': option.value === newConversationType }"
                  @click.stop="$emit('select-new-conversation-type', option.value)"
                >
                  <span class="agent-type-switcher__menu-label">{{ option.label }}</span>
                  <span class="agent-type-switcher__menu-desc">{{ option.description }}</span>
                </button>
              </div>
            </div>
            <div class="agent-type-switcher work-mode-switcher" :class="{ 'is-disabled': workModeLocked }">
              <button
                type="button"
                class="agent-type-switcher__btn"
                @click.stop="$emit('toggle-work-mode-menu')"
                :disabled="!isConnected || workModeLocked"
                :aria-expanded="workModeMenuOpen"
                aria-haspopup="true"
                :title="workModeLocked ? '对话运行中，运行模式只能在空闲时切换' : '运行模式'"
              >
                <span>{{ workModeLabel }}</span>
                <span class="agent-type-switcher__caret" :class="{ open: workModeMenuOpen }" aria-hidden="true">›</span>
              </button>
              <div v-if="workModeMenuOpen && !workModeLocked" class="agent-type-switcher__menu" @click.stop>
                <button
                  v-for="option in workModeOptions"
                  :key="option.value"
                  type="button"
                  class="agent-type-switcher__menu-item"
                  :class="{ 'is-active': option.value === currentWorkMode }"
                  @click.stop="$emit('change-work-mode', option.value)"
                >
                  <span class="agent-type-switcher__menu-label">{{ option.label }}</span>
                  <span class="agent-type-switcher__menu-desc">{{ option.description }}</span>
                </button>
              </div>
            </div>
            </div>
            <div class="input-actions-right">
              <button
                v-if="isVoiceInputSupported"
                type="button"
                class="stadium-btn voice-btn"
                :class="{
                  'voice-btn--recording': isRecording,
                  'voice-btn--disabled':
                    voiceModelStatus === 'downloading' || voiceModelStatus === 'model_not_ready'
                }"
                @click="toggleVoiceRecording"
                :disabled="!isConnected || inputLocked"
                :title="voiceButtonTitle"
              >
                <template v-if="!isRecording">
                  <svg
                    class="mic-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    width="20"
                    height="20"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M12 19v3m7-12v2a7 7 0 0 1-14 0v-2" />
                    <rect width="6" height="13" x="9" y="2" rx="3" />
                  </svg>
                </template>
                <template v-else>
                  <div class="voice-wave">
                    <span class="voice-wave-bar" />
                    <span class="voice-wave-bar" />
                    <span class="voice-wave-bar" />
                    <span class="voice-wave-bar" />
                  </div>
                </template>
              </button>
              <button
                type="button"
                class="stadium-btn send-btn"
                @click="$emit('send-or-stop')"
                :disabled="sendButtonDisabled"
              >
                <span v-if="showStopIcon" class="stop-icon"></span>
                <span v-else class="send-icon"></span>
              </button>
            </div>
          </div>
        </div>
      </div>
      <QuickMenu
        :open="quickMenuOpen"
        :is-connected="isConnected"
        :uploading="uploading"
        :streaming-message="streamingMessage"
        :thinking-mode="thinkingMode"
        :run-mode="runMode"
        :model-menu-open="modelMenuOpen"
        :model-options="modelOptions"
        :current-model-key="currentModelKey"
        :tool-menu-open="toolMenuOpen"
        :tool-settings="toolSettings"
        :tool-settings-loading="toolSettingsLoading"
        :settings-open="settingsOpen"
        :mode-menu-open="modeMenuOpen"
        :compressing="compressing"
        :current-conversation-id="currentConversationId"
        :icon-style="iconStyle"
        :tool-category-icon="toolCategoryIcon"
        :block-upload="blockUpload"
        :block-tool-toggle="blockToolToggle"
        :block-realtime-terminal="blockRealtimeTerminal"
        :block-focus-panel="blockFocusPanel"
        :block-token-panel="blockTokenPanel"
        :block-compress-conversation="blockCompressConversation"
        :block-conversation-review="blockConversationReview"
        :execution-mode-enabled="executionModeEnabled"
        :goal-mode-armed="goalModeArmed"
        :goal-running="goalRunning"
        @quick-upload="triggerQuickUpload"
        @pick-images="$emit('pick-images')"
        @pick-video="$emit('pick-video')"
        @toggle-tool-menu="$emit('toggle-tool-menu')"
        @toggle-settings="$emit('toggle-settings')"
        @toggle-mode-menu="$emit('toggle-mode-menu')"
        @select-run-mode="(mode) => $emit('select-run-mode', mode)"
        @toggle-model-menu="$emit('toggle-model-menu')"
        @select-model="(key) => $emit('select-model', key)"
        @update-tool-category="(id, enabled) => $emit('update-tool-category', id, enabled)"
        @realtime-terminal="$emit('realtime-terminal')"
        @toggle-focus-panel="$emit('toggle-focus-panel')"
        @toggle-token-panel="(val) => $emit('toggle-token-panel', val)"
        @compress-conversation="$emit('compress-conversation')"
        @toggle-approval-panel="$emit('toggle-approval-panel')"
        @open-review="$emit('open-review')"
        @open-path-authorization="$emit('open-path-authorization')"
        @toggle-goal-mode="$emit('toggle-goal-mode')"
      />
      <div class="permission-switcher" @click.stop>
        <button
          type="button"
          class="permission-switcher__btn"
          :disabled="!isConnected || streamingMessage"
          @click="$emit('open-versioning-dialog')"
        >
          <span>版本控制</span>
        </button>
        <div class="permission-switcher__block">
          <button
            type="button"
            class="permission-switcher__btn"
            :disabled="!isConnected"
            :title="permissionLockedByPlan ? '计划模式下权限与执行环境已锁定，网络权限仍可调整' : (executionLockedByReadonly ? '只读模式下执行环境已锁定为沙箱' : '')"
            @click="$emit('toggle-permission-menu')"
          >
            <svg
              v-if="permissionLockedByPlan || executionLockedByReadonly"
              class="permission-switcher__lock"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              width="12"
              height="12"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            <span>{{ currentPermissionLabel }}</span>
            <span
              v-if="executionModeEnabled"
              class="permission-switcher__exec"
              :class="{ 'permission-switcher__exec--warn': currentExecutionMode === 'direct' }"
              >{{ currentExecutionShortLabel }}</span
            >
            <span class="permission-switcher__caret" :class="{ open: permissionMenuOpen }">›</span>
          </button>
          <div
            v-if="permissionMenuOpen"
            class="permission-switcher__menu"
            :class="{ 'permission-switcher__menu--split': executionModeEnabled }"
          >
            <div
              class="permission-switcher__group"
              :class="{ 'permission-switcher__group--disabled': permissionLockedByPlan }"
            >
              <div class="permission-switcher__group-title">
                权限<span v-if="permissionLockedByPlan" class="permission-switcher__group-lock">计划模式锁定</span>
              </div>
              <button
                v-for="option in permissionOptions"
                :key="`permission-${option.value}`"
                type="button"
                class="permission-switcher__item"
                :class="{ active: option.value === currentPermissionMode }"
                :disabled="permissionLockedByPlan"
                @click="$emit('change-permission-mode', option.value)"
              >
                <span class="permission-switcher__item-label">{{ option.label }}</span>
                <span class="permission-switcher__item-desc">{{ option.description }}</span>
              </button>
            </div>
            <div
              v-if="executionModeEnabled"
              class="permission-switcher__group"
              :class="{ 'permission-switcher__group--disabled': executionModeLocked }"
            >
              <div class="permission-switcher__group-title">
                执行环境
                <span v-if="permissionLockedByPlan" class="permission-switcher__group-lock">计划模式锁定</span>
                <span v-else-if="executionLockedByReadonly" class="permission-switcher__group-lock">只读模式锁定</span>
              </div>
              <button
                v-for="option in executionModeOptions"
                :key="`execution-${option.value}`"
                type="button"
                class="permission-switcher__item"
                :class="{ active: option.value === currentExecutionMode }"
                :disabled="executionModeLocked"
                @click="$emit('change-execution-mode', option.value)"
              >
                <span
                  class="permission-switcher__item-label"
                  :class="{ 'permission-switcher__item-label--warn': option.value === 'direct' }"
                  >{{ option.label }}</span
                >
                <span class="permission-switcher__item-desc">{{ option.description }}</span>
              </button>
            </div>
            <div
              v-if="networkPermissionEnabled"
              class="permission-switcher__group"
              :class="{ 'permission-switcher__group--disabled': currentExecutionMode === 'direct' }"
            >
              <div class="permission-switcher__group-title">网络权限</div>
              <button
                v-for="option in networkPermissionOptions"
                :key="`network-${option.value}`"
                type="button"
                class="permission-switcher__item"
                :class="{ active: option.value === currentNetworkPermission }"
                :disabled="currentExecutionMode === 'direct'"
                @click="$emit('change-network-permission', option.value)"
              >
                <span
                  class="permission-switcher__item-label"
                  :class="{ 'permission-switcher__item-label--warn': option.value === 'full' }"
                  >{{ option.label }}</span
                >
                <span class="permission-switcher__item-desc">{{ option.description }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="context-usage-switcher" @click.stop>
        <button
          type="button"
          class="context-usage-switcher__btn"
          :disabled="!isConnected"
          @click="$emit('toggle-token-panel')"
        >
          <span
            class="context-usage-ring"
            :style="{
              '--context-progress': `${contextUsagePercent}%`,
              '--context-ring-color': contextUsageColor
            }"
            aria-hidden="true"
          >
            <span class="context-usage-ring__inner"></span>
          </span>
        </button>
        <div class="context-usage-switcher__tooltip">
          <div>{{ contextUsagePercentLabel }}已用</div>
          <div>{{ contextUsageCompactText }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  onMounted,
  onBeforeUnmount,
  onBeforeUpdate,
  onUpdated,
  ref,
  watch,
  nextTick,
  computed
} from 'vue';
import { EditorContent, useEditor, type JSONContent } from '@tiptap/vue-3';
import StarterKit from '@tiptap/starter-kit';
import Mention from '@tiptap/extension-mention';
import Placeholder from '@tiptap/extension-placeholder';
import { TextSelection } from 'prosemirror-state';
import QuickMenu from '@/components/input/QuickMenu.vue';
import FileAtMenu, { type FileAtItem } from '@/components/input/FileAtMenu.vue';
import FileChips from '@/components/chat/FileChips.vue';
import RollingNumber from '@/components/input/RollingNumber.vue';
import StatusAvatar from '@/components/avatar/StatusAvatar.vue';
import { useInputStore } from '@/stores/input';
import { usePersonalizationStore } from '@/stores/personalization';
import { useUiStore } from '@/stores/ui';
import { useWorkflowStore } from '@/stores/workflow';

defineOptions({ name: 'InputComposer' });

const emit = defineEmits([
  'update:input-message',
  'input-change',
  'input-focus',
  'input-blur',
  'toggle-quick-menu',
  'send-message',
  'send-or-stop',
  'quick-upload',
  'pick-images',
  'pick-video',
  'toggle-tool-menu',
  'toggle-mode-menu',
  'toggle-model-menu',
  'select-run-mode',
  'select-model',
  'toggle-settings',
  'update-tool-category',
  'realtime-terminal',
  'toggle-focus-panel',
  'toggle-token-panel',
  'compress-conversation',
  'toggle-approval-panel',
  'file-selected',
  'paste-files',
  'remove-image',
  'remove-video',
  'remove-file',
  'open-review',
  'open-path-authorization',
  'toggle-permission-menu',
  'change-permission-mode',
  'change-execution-mode',
  'change-network-permission',
  'open-versioning-dialog',
  'guide-runtime-message',
  'delete-runtime-message',
  'composer-height-change',
  'restore-user-question',
  'toggle-goal-mode',
  'open-goal-dialog',
  'workflow-activated',
  'open-git-changes-panel',
  'toggle-agent-type-menu',
  'select-new-conversation-type',
  'toggle-work-mode-menu',
  'change-work-mode',
  'new-conversation',
  'switch-host-workspace',
  'fetch-host-workspaces',
  'stop-all-sub-agents'
]);

const props = defineProps<{
  inputMessage: string;
  inputIsMultiline: boolean;
  inputIsFocused: boolean;
  isConnected: boolean;
  streamingMessage: boolean;
  inputLocked: boolean;
  uploading: boolean;
  thinkingMode: boolean;
  runMode: 'fast' | 'thinking';
  quickMenuOpen: boolean;
  toolMenuOpen: boolean;
  modeMenuOpen: boolean;
  modelMenuOpen: boolean;
  toolSettings: Array<{ id: string; label: string; enabled: boolean }>;
  toolSettingsLoading: boolean;
  settingsOpen: boolean;
  compressing: boolean;
  currentConversationId: string | null;
  iconStyle: (key: string) => Record<string, string>;
  toolCategoryIcon: (categoryId: string) => string;
  modelOptions: Array<{
    key: string;
    label: string;
    description: string;
    disabled?: boolean;
    supportsImage?: boolean;
    supportsVideo?: boolean;
    contextWindow?: number | null;
  }>;
  currentModelKey: string;
  selectedImages?: string[];
  selectedFiles?: string[];
  selectedVideos?: string[];
  mediaUploading?: boolean;
  blockUpload?: boolean;
  blockToolToggle?: boolean;
  blockRealtimeTerminal?: boolean;
  blockFocusPanel?: boolean;
  blockTokenPanel?: boolean;
  blockCompressConversation?: boolean;
  blockConversationReview?: boolean;
  currentPermissionMode: 'readonly' | 'approval' | 'auto_approval' | 'unrestricted';
  permissionMenuOpen: boolean;
  permissionOptions: Array<{ value: string; label: string; description: string }>;
  executionModeEnabled?: boolean;
  currentExecutionMode?: 'sandbox' | 'direct';
  executionModeOptions?: Array<{ value: string; label: string; description: string }>;
  networkPermissionEnabled?: boolean;
  currentNetworkPermission?: 'restricted' | 'full';
  networkPermissionOptions?: Array<{ value: string; label: string; description: string }>;
  currentContextTokens: number;
  versioningEnabled?: boolean;
  runtimeQueuedMessages?: Array<{ id: string; text: string }>;
  projectGitSummary?: {
    has_git?: boolean;
    project_name?: string;
    project_path?: string;
    branch?: string;
    additions?: number;
    deletions?: number;
  } | null;
  userQuestionMinimized?: boolean;
  pendingUserQuestionCount?: number;
  goalModeArmed?: boolean;
  goalRunning?: boolean;
  goalProgress?: Record<string, any> | null;
  /** 当前已打开对话的类型（创建时确定、不可变；null = 空对话） */
  currentConversationType?: 'normal' | 'multi_agent' | null;
  /** 空对话状态下待创建对话的类型（用户可在输入栏切换） */
  newConversationType?: 'agent' | 'multi_agent';
  /** 类型选择器菜单是否展开 */
  agentTypeMenuOpen?: boolean;
  /** 当前运行模式（plan/ask/execute） */
  currentWorkMode?: 'plan' | 'ask' | 'execute';
  /** 运行模式菜单是否展开 */
  workModeMenuOpen?: boolean;
  /** 运行模式选项 */
  workModeOptions?: Array<{ value: string; label: string; description: string }>;
  /** host/docker 项目模式下的工作区列表（/菜单「切换工作区」数据源） */
  hostWorkspaces?: Array<{
    workspace_id: string;
    label: string;
    path?: string;
    is_current?: boolean;
    running_task_count?: number;
  }>;
  /** 当前工作区 id */
  currentHostWorkspaceId?: string;
  /** 工作区切换进行中 */
  hostWorkspaceSwitching?: boolean;
  /** 工作区文案类型：workspace=host 工作区；project=docker 项目；null=不显示工作区切换项 */
  workspaceKind?: 'workspace' | 'project' | null;
  /**
   * 主对话是否空闲（composerBusy 可能是 true，但只是因为后台子智能体在跑）。
   * 多智能体模式下，若主对话空闲，输入栏应处于“正常发送”状态，不是“停止”状态。
   */
  mainChatIdle?: boolean;
  /** 非终态子智能体数量，用于状态栏“x 个子智能体运行中”按钮显示 */
  activeSubAgentCount?: number;
  hasPendingRuntimeGuidance?: boolean;
  terminalCount?: number;
  hostMode?: boolean;
  avatarStatus?: {
    mode: string;
    toolKeys?: string[];
    toolTexts?: string[];
    text: string;
    tracking?: boolean;
    /** true 时表示当前文案是「等待 API 响应…」（work 模式下仅此类文案显示在头像旁） */
    apiWaiting?: boolean;
  } | null;
}>();

const inputStore = useInputStore();
const personalizationStore = usePersonalizationStore();
const inputAreaRoot = ref<HTMLElement | null>(null);
const stadiumShellOuter = ref<HTMLElement | null>(null);
const compactInputShell = ref<HTMLElement | null>(null);
const stadiumInput = ref<HTMLTextAreaElement | null>(null);
const fileUploadInput = ref<HTMLInputElement | null>(null);
const baselineComposerVisualHeight = ref<number>(0);
type SkillItem = { name: string; description: string; path: string };
type SlashMenuMode =
  | 'root'
  | 'skills'
  | 'workflows'
  | 'theme'
  | 'permission'
  | 'execution'
  | 'model'
  | 'run-mode'
  | 'network'
  | 'work-mode'
  | 'workspace'
  | 'conversation-type';
type SlashMenuItem = {
  id: string;
  label: string;
  description: string;
  disabled?: boolean;
  action?: () => void;
  mode?: SlashMenuMode;
  skill?: SkillItem;
};
type WorkflowItem = { name: string; description: string; source: string; updatedAt: string; nodeCount: number };
const availableSkills = ref<SkillItem[]>([]);
const selectedSkillRefs = ref<SkillItem[]>([]);
const skillsLoaded = ref(false);
const skillsLoading = ref(false);
const skillSlashOpen = ref(false);
const skillSlashQuery = ref('');
const skillSlashActiveIndex = ref(0);
const skillSlashList = ref<HTMLElement | null>(null);
const slashMenuMode = ref<SlashMenuMode>('root');
const slashMenuParentIndex = ref(0);
const slashMenuTransitioning = ref(false);
const slashHighlightTop = ref(4);
/** 手动滚动后切到鼠标 hover 模式，隐藏 JS 高亮条 */
const slashManualScrolled = ref(false);
/** skills 子菜单进入来源：typed=键入 // 直达（跟随斜杠数退回 root）；menu=root 菜单点入（不跟随） */
const slashSkillsEntrySource = ref<'typed' | 'menu' | null>(null);
/** Escape 从 // 直达的 skills 退回 root 后，抑制本次 token 存续期间的自动重进 */
const slashDoubleSlashSuppressed = ref(false);
const workflowStore = useWorkflowStore();
const availableWorkflows = ref<WorkflowItem[]>([]);
const workflowsLoaded = ref(false);
const workflowsLoading = ref(false);
const workflowActionPending = ref(false);
let slashAnimId: number | null = null;
/** 动画真正进行中（比 animId 更可靠，可覆盖 rAF 结束后的延迟 scroll 事件） */
let slashAnimating = false;
/** 记录最近一次动画的目标 scrollTop 和结束时间，用于过滤动画后的延迟 scroll 事件 */
let lastSlashAnimatedScroll: number | null = null;
let lastSlashAnimationEndTime = 0;

const fileAtMenuRef = ref<InstanceType<typeof FileAtMenu> | null>(null);
const fileAtOpen = ref(false);
const fileAtQuery = ref<string | null>(null);
const fileAtActiveIndex = ref(0);
const fileAtItems = ref<FileAtItem[]>([]);
const fileAtLoading = ref(false);
const fileAtMenuStyle = ref<Record<string, string>>({});
const fileAtPickerActive = ref(false);
let fileAtSearchTimer: number | null = null;

const avatarPokeText = ref('');
let avatarPokeTimer: number | null = null;
const STATUS_AVATAR_POKE_TEXTS = [
  '不要！',
  '停下来！',
  '别戳啦！',
  '哎呀！',
  '你戳到我了！',
  '轻一点嘛！',
  '我不是按钮！',
  '不许乱点！',
  '呜……别戳我了……',
  '六边形也是会痛的！',
  '再戳我要变形了！',
  '我会紧张的……',
  '正在假装镇定……',
  'Token 掉了一地！',
  '上下文被你戳乱了！',
  '防戳模式启动失败',
  '系统提示：AI 被戳了一下',
  '警告：检测到手欠行为',
  '我记住你了！',
  '戳回去！'
];

const slashHighlightStyle = computed(() => {
  if (slashManualScrolled.value) return { display: 'none' };
  const isFirst = skillSlashActiveIndex.value === 0 && skillSlashList.value?.scrollTop === 0;
  return {
    top: `${slashHighlightTop.value}px`,
    ...(isFirst
      ? {
          borderTopLeftRadius: 'calc(var(--skill-slash-radius) - var(--skill-slash-gap))',
          borderTopRightRadius: 'calc(var(--skill-slash-radius) - var(--skill-slash-gap))'
        }
      : {})
  };
});

const animateSlash = (element: HTMLElement, targetScroll: number) => {
  const interrupted = slashAnimId !== null;
  if (interrupted) {
    cancelAnimationFrame(slashAnimId);
  }
  slashAnimating = true;
  lastSlashAnimatedScroll = targetScroll;
  const startScroll = element.scrollTop;
  const startHighlight = slashHighlightTop.value;
  const distScroll = targetScroll - startScroll;
  const targetHighlight = 4 + skillSlashActiveIndex.value * 31 - targetScroll;
  const distHighlight = targetHighlight - startHighlight;
  const duration = interrupted ? 60 : 180;
  const startTime = performance.now();

  const step = (now: number) => {
    // rAF 第一帧的 now 偶尔会早于 startTime，导致 progress 为负、scrollTop 反向抖动
    const elapsed = Math.max(0, now - startTime);
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.scrollTop = startScroll + distScroll * eased;
    slashHighlightTop.value = startHighlight + distHighlight * eased;
    if (progress < 1) {
      slashAnimId = requestAnimationFrame(step);
    } else {
      slashAnimId = null;
      slashAnimating = false;
      lastSlashAnimationEndTime = performance.now();
    }
  };
  slashAnimId = requestAnimationFrame(step);
};

/** 手动滚动时切到鼠标 hover 模式，不再跟踪键盘选中 */
const onSlashScroll = () => {
  if (slashAnimating || slashAnimId !== null) return;
  const list = skillSlashList.value;
  if (!list) return;
  const now = performance.now();
  // 动画结束后的延迟 scroll 事件：scrollTop 与动画目标一致，说明是动画尾声，忽略
  if (
    lastSlashAnimatedScroll !== null &&
    now - lastSlashAnimationEndTime < 150 &&
    Math.abs(list.scrollTop - lastSlashAnimatedScroll) < 1
  ) {
    return;
  }
  slashManualScrolled.value = true;
};

let composerResizeObserver: ResizeObserver | null = null;
let syncingEditorFromProps = false;

const formatImageName = (path: string): string => {
  if (!path) return '';
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
};

const previewImage = (path: string) => {
  const url = getPreviewUrl(path);
  if (!url) return;
  useUiStore().openImagePreview({ url, name: formatImageName(path) });
};

const getPreviewUrl = (path: string): string => {
  if (!path) return '';
  return `/api/gui/files/download?path=${encodeURIComponent(path)}`;
};

const applyLineMetrics = (lines: number, multiline: boolean) => {
  inputStore.setInputLineCount(lines);
  inputStore.setInputMultiline(multiline);
};

const composerInputKey = computed(
  () => `${props.currentConversationId || 'new'}:${props.isConnected ? 'online' : 'offline'}`
);

const runtimeQueuedMessagesForRender = computed(() => {
  const list = Array.isArray(props.runtimeQueuedMessages) ? props.runtimeQueuedMessages : [];
  return list
    .filter((item) => item && item.id && item.text)
    .map((item) => ({
      ...item,
      text: String(item.text || '').replace(/[\r\n\u2028\u2029]+/g, ' ')
    }));
});

const projectGitSummaryForRender = computed(() => {
  if (personalizationStore?.form?.show_git_status_bar === false) {
    return null;
  }
  const summary = props.projectGitSummary;
  if (!summary?.has_git) {
    return null;
  }
  const projectName = String(summary.project_name || '').trim();
  const branch = String(summary.branch || '').trim();
  if (!projectName || !branch) {
    return null;
  }
  const result = {
    projectName,
    projectPath: String(summary.project_path || '').trim(),
    branch,
    additions: Math.max(0, Number(summary.additions || 0)),
    deletions: Math.max(0, Number(summary.deletions || 0))
  };
  return result;
});

const floatingStatusVisible = computed(() => {
  if (skillSlashMenuOpen.value || fileAtOpen.value || props.quickMenuOpen) {
    return false;
  }
  // 消息队列（预输入/引导消息）存在时隐藏git状态栏，与 / 菜单出现时逻辑相同
  if (runtimeQueuedMessagesForRender.value.length > 0 || props.hasPendingRuntimeGuidance) {
    return false;
  }
  const visible =
    !!projectGitSummaryForRender.value ||
    !!props.goalRunning ||
    !!props.goalModeArmed ||
    (Number(props.pendingUserQuestionCount || 0) > 0) ||
    (Number(props.activeSubAgentCount || 0) > 0);
  return visible;
});

const showComposerAvatar = computed(() => {
  if (skillSlashMenuOpen.value || fileAtOpen.value || props.quickMenuOpen) return false;
  if (runtimeQueuedMessagesForRender.value.length > 0 || props.hasPendingRuntimeGuidance)
    return false;
  return !!props.avatarStatus;
});

const composerAvatarText = computed(() => {
  if (!props.avatarStatus) return '';
  // 头像旁边显示 tool 模式的 intent 文案以及 think 模式的提示
  if (props.avatarStatus.mode === 'tool' || props.avatarStatus.mode === 'think') {
    return props.avatarStatus.text || '';
  }
  // work 模式仅显示「等待 API 响应…」（apiWaiting 标记）；
  // 后台计数/随机等待文案不在头像旁重复显示（后者已在消息区等待动画中体现）
  if (props.avatarStatus.mode === 'work' && props.avatarStatus.apiWaiting) {
    return props.avatarStatus.text || '';
  }
  return '';
});

// key 跟随当前显示的工具（第一个 running tool）变化，
// 工具1完成切到工具2时文字有 out-in 过渡，与 SVG 切换动画同步
const composerAvatarTextKey = computed(() => {
  const s = props.avatarStatus;
  if (!s) return 'avatar-none';
  if (s.mode === 'tool') return `avatar-tool-${s.toolKeys?.[0] || ''}`;
  if (s.mode === 'work' && s.apiWaiting) return 'avatar-work-api-waiting';
  return `avatar-${s.mode}`;
});

const projectGitMenuOpen = ref<null | 'project' | 'branch'>(null);

const closeProjectGitMenu = () => {
  projectGitMenuOpen.value = null;
};

const toggleProjectGitMenu = (menu: 'project' | 'branch') => {
  projectGitMenuOpen.value = projectGitMenuOpen.value === menu ? null : menu;
};

const copyTextToClipboard = async (text: string) => {
  const value = String(text || '').trim();
  if (!value || typeof navigator === 'undefined' || !navigator.clipboard?.writeText) {
    return;
  }
  await navigator.clipboard.writeText(value);
  closeProjectGitMenu();
};

const copyProjectPath = () => {
  copyTextToClipboard(projectGitSummaryForRender.value?.projectPath || '');
};

const copyProjectBranch = () => {
  copyTextToClipboard(projectGitSummaryForRender.value?.branch || '');
};

const openProjectInFileManager = async () => {
  try {
    await fetch('/api/project/open-in-file-manager', { method: 'POST' });
  } catch (error) {
    console.warn('打开项目文件管理器失败:', error);
  } finally {
    closeProjectGitMenu();
  }
};

const findSlashToken = () => {
  const instance = editor.value;
  if (!instance) return null;
  const { state } = instance;
  const { $from } = state.selection;
  const lineStart = $from.start();
  const beforeInLine = $from.parent.textBetween(0, $from.parentOffset, '\n', '\n');
  const match = /(^|[ \n])(\/{1,2})([^\s/]*)$/.exec(beforeInLine);
  if (!match) return null;
  const slashes = match[2] || '/';
  const query = match[3] || '';
  const start = $from.pos - query.length - slashes.length;
  const end = $from.pos;
  return {
    start,
    end,
    query,
    doubleSlash: slashes.length === 2
  };
};

const findAtToken = () => {
  const instance = editor.value;
  if (!instance) return null;
  const { state } = instance;
  const { $from } = state.selection;
  const beforeInLine = $from.parent.textBetween(0, $from.parentOffset, '\n', '\n');
  const match = /(^|[ \n])@([^\s@]*)$/.exec(beforeInLine);
  if (!match) return null;
  const query = match[2] || '';
  const start = $from.pos - query.length - 1;
  const end = $from.pos;
  return {
    start,
    end,
    query,
    charStart: start
  };
};

const closeSlashMenu = () => {
  if (slashAnimId !== null) {
    cancelAnimationFrame(slashAnimId);
    slashAnimId = null;
  }
  slashAnimating = false;
  lastSlashAnimatedScroll = null;
  lastSlashAnimationEndTime = 0;
  skillSlashOpen.value = false;
  skillSlashQuery.value = '';
  skillSlashActiveIndex.value = 0;
  slashMenuMode.value = 'root';
  slashMenuParentIndex.value = 0;
  slashHighlightTop.value = 4;
  slashManualScrolled.value = false;
  slashSkillsEntrySource.value = null;
  slashDoubleSlashSuppressed.value = false;
};

const closeFileAtMenu = () => {
  fileAtOpen.value = false;
  fileAtQuery.value = null;
  fileAtActiveIndex.value = 0;
  fileAtItems.value = [];
  fileAtLoading.value = false;
  fileAtMenuStyle.value = {};
  if (fileAtSearchTimer !== null) {
    window.clearTimeout(fileAtSearchTimer);
    fileAtSearchTimer = null;
  }
};

const clearAvatarPokeText = () => {
  if (avatarPokeTimer !== null) {
    window.clearTimeout(avatarPokeTimer);
    avatarPokeTimer = null;
  }
  avatarPokeText.value = '';
};

const handleAvatarPoke = () => {
  if (!props.avatarStatus || props.avatarStatus.mode !== 'idle') return;
  const texts = STATUS_AVATAR_POKE_TEXTS;
  avatarPokeText.value = texts[Math.floor(Math.random() * texts.length)] || '不要！';
  if (avatarPokeTimer !== null) window.clearTimeout(avatarPokeTimer);
  avatarPokeTimer = window.setTimeout(() => {
    avatarPokeText.value = '';
    avatarPokeTimer = null;
  }, 1800);
};

const updateFileAtMenuPosition = (tokenEnd: number) => {
  nextTick(() => {
    const instance = editor.value;
    if (!instance) return;
    try {
      const coords = instance.view.coordsAtPos(tokenEnd);
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const menuWidth = 280;
      const menuHeight = 250;
      const gap = 4;

      // 默认：菜单左下角贴着 @ 后面文字的右上角
      let left = coords.right;
      let top = coords.top - menuHeight;

      if (left + menuWidth > viewportWidth - gap) {
        left = Math.max(gap, viewportWidth - menuWidth - gap);
      }

      if (top < gap) {
        top = coords.bottom;
      }
      if (top + menuHeight > viewportHeight - gap) {
        top = Math.max(gap, viewportHeight - menuHeight - gap);
      }

      fileAtMenuStyle.value = {
        left: `${left}px`,
        top: `${top}px`,
        width: `${menuWidth}px`,
        maxHeight: `${menuHeight}px`
      };
    } catch {
      fileAtMenuStyle.value = {};
    }
  });
};

const isImageFile = (path: string): boolean => {
  if (!path) return false;
  const ext = path.split('.').pop()?.toLowerCase() || '';
  // svg 不走图片链路（模型图片输入与 view 工具均不支持），按普通文件处理
  return ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico'].includes(ext);
};

const fetchProjectFileSearch = async (query: string) => {
  fileAtLoading.value = true;
  try {
    const url = `/api/project/files/search?q=${encodeURIComponent(query)}&limit=50`;
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.success) {
      fileAtItems.value = [];
      return;
    }
    fileAtItems.value = Array.isArray(data?.data?.items) ? data.data.items : [];
  } catch (error) {
    console.warn('[FileAt] 搜索文件失败:', error);
    fileAtItems.value = [];
  } finally {
    fileAtLoading.value = false;
  }
};

const scheduleFileAtSearch = (query: string) => {
  if (fileAtSearchTimer !== null) {
    window.clearTimeout(fileAtSearchTimer);
  }
  fileAtSearchTimer = window.setTimeout(() => {
    void fetchProjectFileSearch(query);
  }, 120);
};

const refreshFileAtState = () => {
  const token = findAtToken();
  if (!token || !props.isConnected || props.inputLocked) {
    closeFileAtMenu();
    return;
  }
  closeProjectGitMenu();
  const queryChanged = fileAtQuery.value !== token.query;
  fileAtOpen.value = true;
  if (queryChanged || fileAtItems.value.length === 0) {
    fileAtActiveIndex.value = 0;
    fileAtQuery.value = token.query;
    scheduleFileAtSearch(token.query);
  }
  updateFileAtMenuPosition(token.end);
};

const insertFileAtMention = (item: FileAtItem) => {
  const token = findAtToken();
  const instance = editor.value;
  if (!token || !instance) return;
  const { state, view, schema } = instance;
  const mentionType = schema.nodes.mention;
  if (!mentionType) return;
  const mentionNode = mentionType.create({
    id: `file://${item.path}`,
    label: item.name
  });
  let tr = state.tr.delete(token.start, token.end);
  tr = tr.insert(token.start, mentionNode);
  const afterMention = token.start + mentionNode.nodeSize;
  tr = tr.insertText(' ', afterMention);
  tr = tr.setSelection(TextSelection.near(tr.doc.resolve(afterMention + 1)));
  view.dispatch(tr);
  view.focus();
  closeFileAtMenu();
  nextTick(() => {
    adjustTextareaSize();
  });
};

const openFilePickerAndReference = () => {
  closeFileAtMenu();
  if (!props.hostMode) {
    return;
  }
  fileAtPickerActive.value = true;
  fileUploadInput.value?.click();
};

const selectFileAtItemByIndex = (index: number) => {
  const hasPicker = !!props.hostMode;
  if (hasPicker && index === 0) {
    void openFilePickerAndReference();
    return;
  }
  const fileIndex = hasPicker ? index - 1 : index;
  const item = fileAtItems.value[fileIndex];
  if (!item) return;
  if (item.type === 'file' && isImageFile(item.path)) {
    closeFileAtMenu();
    emit('pick-images');
    return;
  }
  insertFileAtMention(item);
};

const handleFileAtKeydown = (event: KeyboardEvent): boolean => {
  if (!fileAtOpen.value) return false;
  const hasPicker = !!props.hostMode;
  const total = (hasPicker ? 1 : 0) + fileAtItems.value.length;
  if (total <= 0) return false;

  if (event.key === 'ArrowDown') {
    event.preventDefault();
    fileAtMenuRef.value?.setManualScroll(false);
    fileAtActiveIndex.value = (fileAtActiveIndex.value + 1) % total;
    fileAtMenuRef.value?.focusActive();
    return true;
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault();
    fileAtMenuRef.value?.setManualScroll(false);
    fileAtActiveIndex.value = (fileAtActiveIndex.value - 1 + total) % total;
    fileAtMenuRef.value?.focusActive();
    return true;
  }
  if (event.key === 'Enter') {
    event.preventDefault();
    selectFileAtItemByIndex(fileAtActiveIndex.value);
    return true;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    closeFileAtMenu();
    return true;
  }
  return false;
};

const deleteSlashToken = () => {
  const token = findSlashToken();
  const instance = editor.value;
  if (!token || !instance) {
    return;
  }
  const { state, view } = instance;
  let tr = state.tr.delete(token.start, token.end);
  tr = tr.setSelection(TextSelection.near(tr.doc.resolve(Math.max(0, token.start))));
  view.dispatch(tr);
  view.focus();
  nextTick(() => {
    adjustTextareaSize();
  });
};

const scrollSkillSlashSelectionIntoMiddle = () => {
  nextTick(() => {
    const list = skillSlashList.value;
    if (!list) return;
    const styles = getComputedStyle(list);
    const cssRowHeight = parseFloat(styles.getPropertyValue('--skill-slash-row-height'));
    const cssGap = parseFloat(styles.getPropertyValue('--skill-slash-gap'));
    const rowHeight =
      (Number.isFinite(cssRowHeight) ? cssRowHeight : 38) + (Number.isFinite(cssGap) ? cssGap : 5);
    const visibleRows = Math.max(1, Math.floor(list.clientHeight / rowHeight) || 5);
    const middleOffset = Math.floor(visibleRows / 2);
    const maxScroll = Math.max(0, list.scrollHeight - list.clientHeight);
    const target = Math.max(
      0,
      Math.min(maxScroll, (skillSlashActiveIndex.value - middleOffset) * rowHeight)
    );
    animateSlash(list, target);
  });
};

const filteredSkills = computed(() => {
  const query = skillSlashQuery.value.trim().toLowerCase();
  const list = Array.isArray(availableSkills.value) ? availableSkills.value : [];
  const filtered = query
    ? list.filter((skill) =>
        String(skill.name || '')
          .toLowerCase()
          .includes(query)
      )
    : list;
  return filtered.slice(0, 100);
});

const currentModelSupportsImage = computed(() => {
  const found = props.modelOptions?.find((m) => m.key === props.currentModelKey) as any;
  return !!found?.supportsImage;
});

const currentModelSupportsVideo = computed(() => {
  const found = props.modelOptions?.find((m) => m.key === props.currentModelKey) as any;
  return !!found?.supportsVideo;
});

const applySlashTheme = async (theme: 'classic' | 'light' | 'dark') => {
  personalizationStore.applyTheme(theme);
  personalizationStore.updateField({ key: 'theme', value: theme });
  try {
    const resp = await fetch('/api/personalization', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme })
    });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok || !result.success) {
      console.warn('保存主题到后端失败:', result?.error);
    }
  } catch (error) {
    console.warn('保存主题到后端失败:', error);
  }
};

const executeSlashAction = (action: () => void) => {
  deleteSlashToken();
  closeSlashMenu();
  action();
};

const rootSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const items: SlashMenuItem[] = [
    {
      id: 'new',
      label: '新建对话',
      description: '开始一个新的对话（new / clear）',
      disabled: !props.isConnected,
      action: () => emit('new-conversation')
    },
    {
      id: 'upload',
      label: '上传文件',
      description: props.uploading ? '上传中...' : '选择本地文件上传',
      disabled: !props.isConnected || props.uploading || props.blockUpload,
      action: () => emit('quick-upload')
    },
    {
      id: 'image',
      label: '发送图片',
      description: currentModelSupportsImage.value ? '选择图片并附加到消息' : '当前模型不支持图片',
      disabled: !props.isConnected || props.streamingMessage || !currentModelSupportsImage.value,
      action: () => emit('pick-images')
    },
    {
      id: 'video',
      label: '发送视频',
      description: currentModelSupportsVideo.value ? '选择视频并附加到消息' : '当前模型不支持视频',
      disabled: !props.isConnected || props.streamingMessage || !currentModelSupportsVideo.value,
      action: () => emit('pick-video')
    },
    {
      id: 'compress',
      label: '压缩对话',
      description: props.compressing ? '压缩中...' : '手动压缩当前对话上下文',
      disabled:
        !props.isConnected ||
        props.compressing ||
        props.streamingMessage ||
        props.blockCompressConversation,
      action: () => emit('compress-conversation')
    },
    {
      id: 'skills',
      label: '选择 AgentSkill',
      description: '插入一个 AgentSkill 引用（// 快捷直达）',
      mode: 'skills'
    },
    {
      id: 'workflows',
      label: '工作流',
      description: workflowStore.isActive
        ? `进行中：${workflowStore.snapshot.name} · 查看或退出`
        : '激活一个工作流，让智能体按流程推进',
      disabled: !props.isConnected,
      mode: 'workflows'
    },
    {
      id: 'conversation-type',
      label: '对话类型',
      description: 'agent / multi-agent · 智能体 / 多智能体（仅新对话可切换）',
      disabled: !props.isConnected,
      mode: 'conversation-type'
    },
    {
      id: 'work-mode',
      label: '切换工作模式',
      description: 'plan / ask / execute · 计划 / 询问 / 执行',
      disabled: !props.isConnected || props.streamingMessage,
      mode: 'work-mode'
    },
    {
      id: 'theme',
      label: '切换主题',
      description: '经典 / 明亮 / 夜间',
      mode: 'theme'
    },
    {
      id: 'review',
      label: '对话回顾',
      description: '打开当前对话回顾',
      disabled: !props.isConnected || props.streamingMessage || props.blockConversationReview,
      action: () => emit('open-review')
    },
    {
      id: 'tokens',
      label: '用量统计',
      description: '打开上下文与 token 用量面板',
      disabled: !props.currentConversationId || props.blockTokenPanel,
      action: () => emit('toggle-token-panel', true)
    },
    {
      id: 'goal',
      label: '目标模式',
      description: props.goalRunning
        ? '目标模式运行中'
        : props.goalModeArmed
          ? '目标模式已就绪'
          : '切换目标模式',
      disabled: !props.isConnected,
      action: () => emit('toggle-goal-mode')
    },
    {
      id: 'personalization',
      label: '个人设置',
      description: '打开个人空间设置',
      action: () => {
        void personalizationStore.openDrawer();
      }
    },
    {
      id: 'terminal',
      label: '实时终端',
      description: '打开实时终端面板',
      disabled: !props.isConnected || props.blockRealtimeTerminal,
      action: () => emit('realtime-terminal')
    },
    {
      id: 'model',
      label: '切换模型',
      description: '选择对话使用的模型',
      disabled: !props.isConnected || props.streamingMessage,
      mode: 'model'
    },
    {
      id: 'run-mode',
      label: '思考模式',
      description: '切换 fast / thinking',
      disabled: !props.isConnected || props.streamingMessage,
      mode: 'run-mode'
    },
    {
      id: 'permission',
      label: '权限模式',
      description: '切换 readonly / approval / auto / unrestricted',
      disabled: !props.isConnected || props.streamingMessage,
      mode: 'permission'
    },
    {
      id: 'network',
      label: '网络权限',
      description: props.networkPermissionEnabled
        ? 'restricted / full · 受限 / 完整'
        : '当前环境不支持网络权限切换',
      disabled: !props.isConnected || props.streamingMessage || !props.networkPermissionEnabled,
      mode: 'network'
    },
    ...(props.executionModeEnabled
      ? [
          {
            id: 'execution',
            label: '执行环境',
            description: '切换 sandbox / direct',
            disabled: !props.isConnected || props.streamingMessage,
            mode: 'execution'
          } as SlashMenuItem
        ]
      : []),
    ...(props.workspaceKind
      ? [
          {
            id: 'workspace',
            label: props.workspaceKind === 'project' ? '切换项目' : '切换工作区',
            description: 'workspace / project · 工作区 / 项目',
            disabled: !props.isConnected || props.streamingMessage,
            mode: 'workspace'
          } as SlashMenuItem
        ]
      : []),
    {
      id: 'versioning',
      label: '版本控制',
      description: props.versioningEnabled ? '当前：开启' : '当前：关闭',
      disabled: !props.isConnected || props.streamingMessage,
      action: () => emit('open-versioning-dialog')
    },
    {
      id: 'git-bar',
      label: 'Git 状态栏',
      description:
        personalizationStore?.form?.show_git_status_bar !== false ? '当前：显示' : '当前：隐藏',
      action: () => {
        const currentValue = personalizationStore?.form?.show_git_status_bar !== false;
        const newValue = !currentValue;
        if (personalizationStore?.form) {
          personalizationStore.form.show_git_status_bar = newValue;
        }
        fetch('/api/personalization', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ show_git_status_bar: newValue })
        }).catch(() => {});
      }
    },
    {
      id: 'path-auth',
      label: '路径授权',
      description: '查看和管理路径授权',
      disabled: !props.isConnected,
      action: () => emit('open-path-authorization')
    },
    {
      id: 'approval',
      label: '审批面板',
      description: '查看审批记录',
      disabled: !props.isConnected,
      action: () => emit('toggle-approval-panel')
    }
  ];
  const query = skillSlashQuery.value.trim().toLowerCase();
  if (!query) {
    return items;
  }
  return items.filter((item) => {
    const haystack = `${item.label} ${item.description} ${item.id}`.toLowerCase();
    return haystack.includes(query);
  });
});

const themeSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const themes: Array<SlashMenuItem & { theme: 'classic' | 'light' | 'dark' }> = [
    {
      id: 'theme-classic',
      label: '经典',
      description: '米色质感，柔和高对比',
      theme: 'classic'
    },
    {
      id: 'theme-light',
      label: '明亮',
      description: '纯白底色 + 优雅灰，简洁清爽',
      theme: 'light'
    },
    {
      id: 'theme-dark',
      label: '夜间',
      description: '深灰 + 黑，低亮度显示',
      theme: 'dark'
    }
  ];
  return themes.map((item) => ({
    ...item,
    action: () => {
      void applySlashTheme(item.theme);
    }
  }));
});

const permissionSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const options = props.permissionOptions || [];
  const current = String(props.currentPermissionMode || '');
  const lockedByPlan = props.currentWorkMode === 'plan';
  return options.map((opt) => ({
    id: `perm:${opt.value}`,
    label: opt.label,
    description: lockedByPlan
      ? '计划模式下权限锁定为只读'
      : `${opt.value === current ? '当前 · ' : ''}${opt.description || ''}`,
    disabled: lockedByPlan,
    action: () => emit('change-permission-mode', opt.value)
  }));
});

const executionSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const options = props.executionModeOptions || [];
  const current = String(props.currentExecutionMode || '');
  const lockedByPlan = props.currentWorkMode === 'plan';
  return options.map((opt) => ({
    id: `exec:${opt.value}`,
    label: opt.label,
    description: lockedByPlan
      ? '计划模式下执行环境锁定为沙箱'
      : `${opt.value === current ? '当前 · ' : ''}${opt.description || ''}`,
    disabled: lockedByPlan,
    action: () => emit('change-execution-mode', opt.value)
  }));
});

const modelSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const list = Array.isArray(props.modelOptions) ? props.modelOptions : [];
  const current = String(props.currentModelKey || '');
  return list.map((model) => ({
    id: `model:${model.key}`,
    label: model.label,
    description: `${model.key === current ? '当前 · ' : ''}${model.description || ''}`,
    disabled: !!model.disabled,
    action: () => emit('select-model', model.key)
  }));
});

const runModeSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const modes: Array<{ value: string; label: string }> = [
    { value: 'fast', label: '快速' },
    { value: 'thinking', label: '思考' }
  ];
  const current = String(props.runMode || '');
  return modes.map((mode) => ({
    id: `runmode:${mode.value}`,
    label: mode.label,
    description: mode.value === current ? '当前' : '',
    action: () => emit('select-run-mode', mode.value)
  }));
});

const networkSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const options = props.networkPermissionOptions || [];
  const current = String(props.currentNetworkPermission || '');
  return options.map((opt) => ({
    id: `network:${opt.value}`,
    label: opt.label,
    description: `${opt.value === current ? '当前 · ' : ''}${opt.description || ''}`,
    action: () => emit('change-network-permission', opt.value)
  }));
});

const workModeSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const options = props.workModeOptions || [];
  const current = String(props.currentWorkMode || '');
  return options.map((opt) => ({
    id: `workmode:${opt.value}`,
    label: opt.label,
    description: `${opt.value === current ? '当前 · ' : ''}${opt.description || ''}`,
    action: () => emit('change-work-mode', opt.value)
  }));
});

const workspaceSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const list = Array.isArray(props.hostWorkspaces) ? props.hostWorkspaces : [];
  const current = String(props.currentHostWorkspaceId || '');
  const switching = !!props.hostWorkspaceSwitching;
  return list.map((ws) => ({
    id: `workspace:${ws.workspace_id}`,
    label: ws.label,
    description: `${ws.workspace_id === current ? '当前 · ' : ''}${ws.path || ''}`,
    disabled: switching || ws.workspace_id === current,
    action: () => emit('switch-host-workspace', ws.workspace_id)
  }));
});

const conversationTypeSlashMenuItems = computed<SlashMenuItem[]>(() => {
  // 已打开对话时类型不可变（类型是创建时确定的对话属性），子菜单项整体锁定
  const locked = agentTypeLocked.value;
  const current = String(props.newConversationType || 'agent');
  return agentTypeOptions.map((opt) => ({
    id: `convtype:${opt.value}`,
    label: opt.label,
    description: locked
      ? '对话类型创建后不可变，仅新对话可切换'
      : `${opt.value === current ? '当前 · ' : ''}${opt.description || ''}`,
    disabled: locked,
    action: () => emit('select-new-conversation-type', opt.value as 'agent' | 'multi_agent')
  }));
});

const skillSlashMenuItems = computed<SlashMenuItem[]>(() =>
  filteredSkills.value.map((skill) => ({
    id: `skill:${skill.path}`,
    label: skill.name,
    description: skill.description,
    skill
  }))
);

const activeSlashMenuItems = computed<SlashMenuItem[]>(() => {
  if (slashMenuMode.value === 'skills') {
    return skillSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'workflows') {
    return workflowSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'theme') {
    return themeSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'permission') {
    return permissionSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'execution') {
    return executionSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'model') {
    return modelSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'run-mode') {
    return runModeSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'network') {
    return networkSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'work-mode') {
    return workModeSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'workspace') {
    return workspaceSlashMenuItems.value;
  }
  if (slashMenuMode.value === 'conversation-type') {
    return conversationTypeSlashMenuItems.value;
  }
  return rootSlashMenuItems.value;
});

const slashMenuAriaLabel = computed(() => {
  if (slashMenuMode.value === 'skills') return '可用 AgentSkills';
  if (slashMenuMode.value === 'workflows') return '工作流选项';
  if (slashMenuMode.value === 'theme') return '主题选项';
  if (slashMenuMode.value === 'permission') return '权限模式选项';
  if (slashMenuMode.value === 'execution') return '执行环境选项';
  if (slashMenuMode.value === 'model') return '模型选项';
  if (slashMenuMode.value === 'run-mode') return '思考模式选项';
  if (slashMenuMode.value === 'network') return '网络权限选项';
  if (slashMenuMode.value === 'work-mode') return '工作模式选项';
  if (slashMenuMode.value === 'workspace')
    return props.workspaceKind === 'project' ? '项目选项' : '工作区选项';
  if (slashMenuMode.value === 'conversation-type') return '对话类型选项';
  return '快捷指令';
});

const slashMenuEmptyText = computed(() => {
  if (slashMenuMode.value === 'skills')
    return skillsLoading.value ? '正在加载 skills...' : '没有匹配的 skill';
  if (slashMenuMode.value === 'workflows')
    return workflowsLoading.value ? '正在加载工作流...' : '暂无可用工作流（可去 /workflows 创建）';
  if (slashMenuMode.value === 'permission') return '无可用权限模式';
  if (slashMenuMode.value === 'execution') return '无可用执行环境';
  if (slashMenuMode.value === 'model') return '无可用模型';
  if (slashMenuMode.value === 'network') return '无可用网络权限选项';
  if (slashMenuMode.value === 'work-mode') return '无可用工作模式';
  if (slashMenuMode.value === 'workspace')
    return props.workspaceKind === 'project' ? '暂无其他项目' : '暂无其他工作区';
  if (slashMenuMode.value === 'conversation-type') return '无可用对话类型';
  return '没有匹配的指令';
});

const skillSlashMenuOpen = computed(
  () => skillSlashOpen.value && (skillsLoading.value || activeSlashMenuItems.value.length >= 0)
);

const loadSkills = async () => {
  if (skillsLoaded.value || skillsLoading.value) {
    return;
  }
  skillsLoading.value = true;
  try {
    const response = await fetch('/api/skills');
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.success) {
      throw new Error(data?.error || '加载 skills 失败');
    }
    availableSkills.value = Array.isArray(data.skills)
      ? data.skills
          .filter((item: any) => item && item.name && item.path)
          .map((item: any) => ({
            name: String(item.name || ''),
            description: String(item.description || ''),
            path: String(item.path || '')
          }))
      : [];
    skillsLoaded.value = true;
  } catch (error) {
    availableSkills.value = [];
    skillsLoaded.value = false;
    console.warn('[SkillSlash] 加载 skills 失败:', error);
  } finally {
    skillsLoading.value = false;
  }
};

const loadWorkflows = async (force = false) => {
  if ((workflowsLoaded.value && !force) || workflowsLoading.value) {
    return;
  }
  workflowsLoading.value = true;
  try {
    const response = await fetch('/api/workflows');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data?.error || '加载工作流列表失败');
    }
    availableWorkflows.value = Array.isArray(data.workflows)
      ? data.workflows
          .filter((item: any) => item && item.name)
          .map((item: any) => ({
            name: String(item.name || ''),
            description: String(item.description || ''),
            source: String(item.source || ''),
            updatedAt: String(item.updatedAt || ''),
            nodeCount: Number(item.nodeCount ?? 0)
          }))
      : [];
    workflowsLoaded.value = true;
  } catch (error) {
    availableWorkflows.value = [];
    workflowsLoaded.value = false;
    console.warn('[WorkflowSlash] 加载工作流列表失败:', error);
  } finally {
    workflowsLoading.value = false;
  }
};

const activateWorkflow = async (name: string) => {
  if (workflowActionPending.value) return;
  // 空对话也允许激活（定稿：条件是「智能体空闲」而非「对话非空」）——
  // 后端会自动创建对话并返回新 conversation_id，前端随后进入该对话。
  const conversationId = String(props.currentConversationId || '').trim();
  workflowActionPending.value = true;
  try {
    const body: Record<string, any> = { name };
    if (conversationId) {
      body.conversation_id = conversationId;
    }
    const response = await fetch('/api/workflow/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.success) {
      throw new Error(data?.error || '激活工作流失败');
    }
    // 快照随响应返回：立即写入 store（socketio 广播在轮询架构下不可靠，
    // 任务事件流的首个进度事件要等模型首次汇报才发出）
    if (data?.snapshot) {
      workflowStore.setWorkflow(data.snapshot, true);
    }
    useUiStore().pushToast({ title: `工作流「${name}」已激活`, type: 'success' });
    const newCid = String(data?.conversation_id || '').trim();
    if (newCid && newCid !== conversationId) {
      emit('workflow-activated', newCid);
    }
  } catch (error: any) {
    useUiStore().pushToast({ title: '激活工作流失败', message: String(error?.message || error || ''), type: 'error' });
  } finally {
    workflowActionPending.value = false;
  }
};

const deactivateWorkflow = async () => {
  if (workflowActionPending.value) return;
  const conversationId = String(props.currentConversationId || '').trim();
  if (!conversationId) {
    return;
  }
  workflowActionPending.value = true;
  try {
    const response = await fetch('/api/workflow/deactivate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.success) {
      throw new Error(data?.error || '退出工作流失败');
    }
    // 摘牌成功：写 live=true 的空快照，触发窗口退出动画（与出现对称），而非瞬间卸载
    workflowStore.setWorkflow(null, true);
    useUiStore().pushToast({ title: `已退出工作流「${data?.workflow_name || ''}」`, type: 'success' });
  } catch (error: any) {
    useUiStore().pushToast({ title: '退出工作流失败', message: String(error?.message || error || ''), type: 'error' });
  } finally {
    workflowActionPending.value = false;
  }
};

/** 工作流子菜单项：激活中首项为「退出当前工作流」，其后为可激活列表。 */
const workflowSlashMenuItems = computed<SlashMenuItem[]>(() => {
  const items: SlashMenuItem[] = [];
  const activeName = workflowStore.isActive ? String(workflowStore.snapshot.name || '') : '';
  if (activeName) {
    items.push({
      id: 'workflow-deactivate',
      label: `退出当前工作流`,
      description: `进行中：${activeName} · 摘牌退出（不打断当前工作）`,
      disabled: workflowActionPending.value,
      action: () => {
        void deactivateWorkflow();
      }
    });
  }
  // 激活仅智能体空闲可用（streaming / 审批等待 / 已有激活工作流时禁用）；
  // 空对话允许激活（后端自动创建对话）。
  const activateDisabled =
    !props.isConnected ||
    props.streamingMessage ||
    workflowActionPending.value ||
    !!activeName;
  for (const wf of availableWorkflows.value) {
    const isCurrent = activeName && wf.name === activeName;
    items.push({
      id: `workflow-activate-${wf.name}`,
      label: wf.name,
      description: isCurrent
        ? '当前激活中'
        : `${wf.description || '（无描述）'}${wf.source === 'builtin' ? ' · 内置' : ''}`,
      disabled: activateDisabled || !!isCurrent,
      action: () => {
        void activateWorkflow(wf.name);
      }
    });
  }
  return items;
});

const refreshSkillSlashState = () => {
  const token = findSlashToken();
  if (!token || !props.isConnected || props.inputLocked) {
    closeSlashMenu();
    return;
  }
  closeProjectGitMenu();
  skillSlashOpen.value = true;
  if (skillSlashQuery.value !== token.query) {
    skillSlashActiveIndex.value = 0;
  }
  skillSlashQuery.value = token.query;
  // 「//」直达 skills 子菜单；仅键入来源跟随斜杠数量变化（菜单点入不受影响）
  if (
    token.doubleSlash &&
    slashMenuMode.value === 'root' &&
    !slashDoubleSlashSuppressed.value
  ) {
    slashMenuMode.value = 'skills';
    slashSkillsEntrySource.value = 'typed';
    skillSlashActiveIndex.value = 0;
  } else if (
    !token.doubleSlash &&
    slashMenuMode.value === 'skills' &&
    slashSkillsEntrySource.value === 'typed'
  ) {
    slashMenuMode.value = 'root';
    slashSkillsEntrySource.value = null;
    skillSlashActiveIndex.value = 0;
  }
  if (slashMenuMode.value === 'skills') {
    void loadSkills();
  }
  skillSlashActiveIndex.value = Math.min(
    skillSlashActiveIndex.value,
    Math.max(0, activeSlashMenuItems.value.length - 1)
  );
  scrollSkillSlashSelectionIntoMiddle();
};

const selectSkillSlashItem = (index = skillSlashActiveIndex.value) => {
  const token = findSlashToken();
  const skill = filteredSkills.value[index];
  const instance = editor.value;
  if (!token || !skill || !instance) {
    return;
  }
  selectedSkillRefs.value = [
    ...selectedSkillRefs.value.filter((item) => item.path !== skill.path),
    skill
  ];
  const { state, view, schema } = instance;
  const mentionType = schema.nodes.mention;
  if (!mentionType) {
    return;
  }
  const mentionNode = mentionType.create({
    id: skill.path,
    label: skill.name,
    path: skill.path,
    description: skill.description || '',
    mentionSuggestionChar: ''
  });
  let tr = state.tr.delete(token.start, token.end);
  tr = tr.insert(token.start, mentionNode);
  const afterMention = token.start + mentionNode.nodeSize;
  tr = tr.insertText(' ', afterMention);
  tr = tr.setSelection(TextSelection.near(tr.doc.resolve(afterMention + 1)));
  view.dispatch(tr);
  view.focus();
  closeSlashMenu();
  nextTick(() => {
    adjustTextareaSize();
  });
};

const selectSlashMenuItem = (index = skillSlashActiveIndex.value) => {
  const item = activeSlashMenuItems.value[index];
  if (!item || item.disabled) {
    return;
  }
  if (item.mode) {
    slashMenuParentIndex.value = index;
    slashMenuMode.value = item.mode;
    skillSlashActiveIndex.value = 0;
    slashSkillsEntrySource.value = item.mode === 'skills' ? 'menu' : null;
    if (item.mode === 'skills') {
      void loadSkills();
    }
    if (item.mode === 'workflows') {
      void loadWorkflows();
    }
    if (item.mode === 'workspace') {
      emit('fetch-host-workspaces');
    }
    scrollSkillSlashSelectionIntoMiddle();
    return;
  }
  if (item.skill) {
    const skillIndex = filteredSkills.value.findIndex((skill) => skill.path === item.skill?.path);
    selectSkillSlashItem(skillIndex >= 0 ? skillIndex : index);
    return;
  }
  if (item.action) {
    executeSlashAction(item.action);
  }
};

const handleSlashMenuKeydown = (event: KeyboardEvent): boolean => {
  if (event.ctrlKey && event.key === 'Enter') {
    return false;
  }
  if (skillSlashOpen.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      slashManualScrolled.value = false;
      const count = activeSlashMenuItems.value.length;
      if (count > 0) {
        skillSlashActiveIndex.value = (skillSlashActiveIndex.value + 1) % count;
        scrollSkillSlashSelectionIntoMiddle();
      }
      return true;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      slashManualScrolled.value = false;
      const count = activeSlashMenuItems.value.length;
      if (count > 0) {
        skillSlashActiveIndex.value = (skillSlashActiveIndex.value - 1 + count) % count;
        scrollSkillSlashSelectionIntoMiddle();
      }
      return true;
    }
    if (event.key === 'Enter') {
      const count = activeSlashMenuItems.value.length;
      if (count > 0) {
        event.preventDefault();
        selectSlashMenuItem(skillSlashActiveIndex.value);
      }
      return true;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      if (slashMenuMode.value !== 'root') {
        // 从 // 直达的 skills 退回 root 时，抑制本次 token 的自动重进（否则下一帧又跳回 skills）
        if (slashMenuMode.value === 'skills' && slashSkillsEntrySource.value === 'typed') {
          slashDoubleSlashSuppressed.value = true;
        }
        slashSkillsEntrySource.value = null;
        slashMenuMode.value = 'root';
        skillSlashActiveIndex.value = Math.min(
          slashMenuParentIndex.value,
          Math.max(0, activeSlashMenuItems.value.length - 1)
        );
        scrollSkillSlashSelectionIntoMiddle();
      } else {
        closeSlashMenu();
      }
      return true;
    }
  }
  return false;
};

const onKeydown = (event: KeyboardEvent): boolean => {
  if (handleFileAtKeydown(event)) {
    return true;
  }
  if (handleSlashMenuKeydown(event)) {
    return true;
  }
  requestAnimationFrame(() => {
    refreshSkillSlashState();
    refreshFileAtState();
  });
  return false;
};

const onInputBlur = () => {
  emit('input-blur');
  window.setTimeout(() => {
    closeSlashMenu();
    closeFileAtMenu();
  }, 120);
};

const collectClipboardFiles = (event: ClipboardEvent): File[] => {
  const data = event.clipboardData;
  if (!data) return [];
  const files: File[] = [];
  if (data.items && data.items.length) {
    for (const item of Array.from(data.items)) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  if (!files.length && data.files && data.files.length) {
    files.push(...Array.from(data.files));
  }
  return files;
};

const textToTiptapContent = (text = ''): JSONContent => {
  const lines = String(text || '').split('\n');
  return {
    type: 'doc',
    content: (lines.length ? lines : ['']).map((line) => ({
      type: 'paragraph',
      content: line ? [{ type: 'text', text: line }] : undefined
    }))
  };
};

const getEditorPlainText = () => {
  const instance = editor.value;
  if (!instance) return props.inputMessage || '';
  return instance.getText({ blockSeparator: '\n' } as any);
};

const editorJsonToMessage = (json?: JSONContent | null) => {
  const skillRefs: Array<{ name: string; path: string }> = [];
  const readInline = (node?: JSONContent): string => {
    if (!node) return '';
    if (node.type === 'text') return node.text || '';
    if (node.type === 'mention') {
      const attrs = node.attrs || {};
      const id = String(attrs.id || '');
      const label = String(attrs.label || id).trim();
      const isFile = id.startsWith('file://');
      if (isFile) {
        const filePath = id.slice(7);
        return label && filePath ? `[${label}](file://${filePath})` : label;
      }
      const name = label;
      const path = String(attrs.path || id).trim();
      if (name && path && !skillRefs.some((item) => item.path === path)) {
        skillRefs.push({ name, path });
      }
      return name && path ? `[$${name}](${path})` : name;
    }
    if (node.type === 'hardBreak') return '\n';
    return Array.isArray(node.content) ? node.content.map(readInline).join('') : '';
  };
  const blocks = Array.isArray(json?.content) ? json.content : [];
  const message = blocks
    .map((block) => readInline(block))
    .join('\n')
    .trim();
  return { message, skillRefs };
};

const editor = useEditor({
  content: textToTiptapContent(props.inputMessage || ''),
  editable: props.isConnected && !props.inputLocked,
  extensions: [
    StarterKit.configure({
      codeBlock: false,
      heading: false,
      blockquote: false,
      bulletList: false,
      orderedList: false,
      listItem: false,
      horizontalRule: false
    }),
    Placeholder.configure({
      placeholder: '输入消息... (Ctrl+Enter 发送)'
    }),
    Mention.configure({
      HTMLAttributes: {
        class: 'skill-md-link'
      },
      deleteTriggerWithBackspace: true,
      renderText({ node }) {
        return String(node.attrs.label || node.attrs.id || '');
      },
      renderHTML({ node, HTMLAttributes }) {
        const attrs = HTMLAttributes || {};
        const id = String(node.attrs.id || '');
        const isFile = id.startsWith('file://');
        const className = isFile
          ? `${attrs.class || ''} file-md-link`.trim()
          : `${attrs.class || ''} skill-md-link`.trim();
        const dataAttr = isFile ? 'data-file-path' : 'data-skill-path';
        const dataValue = isFile ? id.slice(7) : node.attrs.path || node.attrs.id || '';
        return [
          'span',
          {
            ...attrs,
            class: className,
            [dataAttr]: dataValue
          },
          String(node.attrs.label || node.attrs.id || '')
        ];
      },
      suggestion: {
        char: '\u0000'
      } as any
    })
  ],
  editorProps: {
    attributes: {
      class: 'stadium-input-editor',
      'data-placeholder': '输入消息... (Ctrl+Enter 发送)'
    },
    handleKeyDown(_view, event) {
      if (event.ctrlKey && event.key === 'Enter') {
        event.preventDefault();
        emit('send-or-stop');
        return true;
      }
      return onKeydown(event);
    },
    handlePaste(_view, event) {
      // 剪贴板包含文件（截图/复制的图片等）时接管粘贴，走上传流程；
      // 纯文本粘贴返回 false，保持默认行为
      const files = collectClipboardFiles(event);
      if (!files.length) return false;
      event.preventDefault();
      emit('paste-files', files);
      return true;
    }
  },
  onUpdate() {
    if (syncingEditorFromProps) return;
    emit('update:input-message', getEditorPlainText());
    emit('input-change');
    nextTick(() => {
      adjustTextareaSize();
      refreshSkillSlashState();
      refreshFileAtState();
    });
  },
  onFocus() {
    emit('input-focus');
  }
});

/* ═══════════════════ 语音输入 ═══════════════════ */

const isRecording = ref(false);
const recognitionInstance = ref<SpeechRecognition | null>(null);
let voiceAnchorPos = 0;
let lastVoiceTextLength = 0;

// 模型状态：idle | downloading | ready | model_not_ready | error
const voiceModelStatus = ref<string>('idle');
const voiceDownloadPercent = ref(0);
const voiceDownloadMsg = ref('');

// 是否支持语音输入（仅 Android APK 环境，通过 VoiceBridge）
const isVoiceInputSupported = computed(() => {
  if (typeof window === 'undefined') return false;
  return !!(window as any).AndroidVoiceBridge;
});

const voiceButtonTitle = computed(() => {
  if (voiceModelStatus.value === 'downloading') return `模型下载中 ${voiceDownloadPercent.value}%`;
  if (voiceModelStatus.value === 'model_not_ready') return '模型未下载，请在个人空间下载';
  if (isRecording.value) return '停止录音';
  return '语音输入';
});

// 注册全局回调（Android Bridge 会调用这些函数）
const setupVoiceBridgeCallbacks = () => {
  if (typeof window === 'undefined') return;
  (window as any).__onVoiceResult = (text: string) => {
    const bridge = (window as any).AndroidVoiceBridge;
    bridge?.debugLog('__onVoiceResult 被调用, text=' + JSON.stringify(text));
    if (!text) {
      bridge?.debugLog('__onVoiceResult: text 为空，跳过');
      return;
    }
    const ed = editor.value;
    if (!ed) {
      bridge?.debugLog('__onVoiceResult: editor.value 为 null');
      console.warn('[VoiceInput] editor is null');
      return;
    }
    bridge?.debugLog('__onVoiceResult: editor.value 存在，准备插入文字');
    bridge?.debugLog(
      '__onVoiceResult: voiceAnchorPos=' +
        voiceAnchorPos +
        ' lastVoiceTextLength=' +
        lastVoiceTextLength
    );
    const { state, view } = ed;
    const docSize = state.doc.content.size;
    const safeAnchor = Math.max(0, Math.min(voiceAnchorPos, docSize));
    const oldEnd = Math.min(safeAnchor + lastVoiceTextLength, docSize);
    bridge?.debugLog(
      '__onVoiceResult: docSize=' + docSize + ' safeAnchor=' + safeAnchor + ' oldEnd=' + oldEnd
    );
    try {
      let tr = state.tr;
      if (oldEnd > safeAnchor) tr = tr.delete(safeAnchor, oldEnd);
      tr = tr.insertText(text, safeAnchor);
      const newCursor = Math.min(safeAnchor + text.length, tr.doc.content.size);
      const TextSelection = (window as any).TextSelection;
      if (TextSelection) {
        tr = tr.setSelection(TextSelection.create(tr.doc, newCursor));
      }
      view.dispatch(tr);
      view.focus();
      lastVoiceTextLength = text.length;
      emit('update:input-message', getEditorPlainText());
      emit('input-change');
      nextTick(() => adjustTextareaSize());
      bridge?.debugLog('__onVoiceResult: 文字插入成功');
    } catch (e: any) {
      bridge?.debugLog('__onVoiceResult: 插入异常 ' + (e.message || String(e)));
    }
  };
  (window as any).__onVoiceStatus = (status: string) => {
    if (status === 'listening' || status === 'processing' || status === 'initializing') {
      // initializing 也视为录音中，防止用户重复点击触发多次 startListening
      isRecording.value = true;
    } else if (status === 'idle') {
      isRecording.value = false;
      voiceAnchorPos = 0;
      lastVoiceTextLength = 0;
    } else if (status === 'model_not_ready') {
      voiceModelStatus.value = 'model_not_ready';
    }
  };
  (window as any).__onVoiceError = (error: string) => {
    console.warn('[VoiceInput] 错误:', error);
    isRecording.value = false;
    voiceAnchorPos = 0;
    lastVoiceTextLength = 0;
  };
  (window as any).__onVoiceDownloadProgress = (pct: number, msg: string) => {
    voiceModelStatus.value = 'downloading';
    voiceDownloadPercent.value = pct;
    voiceDownloadMsg.value = msg;
    if (pct >= 100) {
      voiceModelStatus.value = 'ready';
    }
  };
  (window as any).__onVoiceModelReady = () => {
    voiceModelStatus.value = 'ready';
  };
};

// 初始化时设置回调和检查模型状态
if (typeof window !== 'undefined') {
  setupVoiceBridgeCallbacks();
  // 检查 Android Bridge 模型状态
  const bridge = (window as any).AndroidVoiceBridge;
  if (bridge && typeof bridge.isModelReady === 'function') {
    try {
      voiceModelStatus.value = bridge.isModelReady() ? 'ready' : 'model_not_ready';
    } catch (_) {
      voiceModelStatus.value = 'idle';
    }
  }
}

const isWebSpeechSupported = computed(() => {
  if (typeof window === 'undefined') return false;
  const hasSR = 'SpeechRecognition' in window;
  const hasWebkit = 'webkitSpeechRecognition' in window;
  return hasSR || hasWebkit;
});

const getSpeechRecognitionConstructor = (): typeof SpeechRecognition | null => {
  if (typeof window === 'undefined') return null;
  const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
  return Ctor;
};

const stopVoiceRecording = () => {
  if (recognitionInstance.value) {
    try {
      recognitionInstance.value.stop();
    } catch (e) {
      console.warn('[VoiceInput] recognition.stop() error:', e);
    }
    recognitionInstance.value = null;
  }
  isRecording.value = false;
  voiceAnchorPos = 0;
  lastVoiceTextLength = 0;
};

const initSpeechRecognition = () => {
  const SpeechRecognitionCtor = getSpeechRecognitionConstructor();
  if (!SpeechRecognitionCtor) return null;

  const recognition = new SpeechRecognitionCtor();
  recognition.lang = 'zh-CN';
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  // 记录锚点位置（录音开始时的光标位置）
  const instance = editor.value;
  if (instance) {
    voiceAnchorPos = instance.state.selection.from;
  } else {
    voiceAnchorPos = 0;
  }
  lastVoiceTextLength = 0;

  recognition.onresult = (event: SpeechRecognitionEvent) => {
    let fullText = '';
    for (let i = 0; i < event.results.length; i++) {
      const r = event.results[i];
      fullText += r[0].transcript;
    }

    if (!fullText) {
      return;
    }

    const ed = editor.value;
    if (!ed) {
      console.warn('[VoiceInput] editor is null');
      return;
    }

    // 从锚点位置替换语音文本：先删旧、再插新
    const { state, view } = ed;
    const docSize = state.doc.content.size;
    // ProseMirror 合法位置范围是 [0, docSize]，docSize 本身是有效位置（文档末尾）
    const safeAnchor = Math.max(0, Math.min(voiceAnchorPos, docSize));
    const oldEnd = Math.min(safeAnchor + lastVoiceTextLength, docSize);

    let tr = state.tr;
    if (oldEnd > safeAnchor) {
      tr = tr.delete(safeAnchor, oldEnd);
    }
    tr = tr.insertText(fullText, safeAnchor);

    const newCursor = Math.min(safeAnchor + fullText.length, tr.doc.content.size);
    tr = tr.setSelection(TextSelection.create(tr.doc, newCursor));
    view.dispatch(tr);
    view.focus();

    lastVoiceTextLength = fullText.length;

    emit('update:input-message', getEditorPlainText());
    emit('input-change');
    nextTick(() => {
      adjustTextareaSize();
    });
  };

  recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
    console.warn('[VoiceInput] onerror:', { error: event.error, message: (event as any).message });
    if (event.error === 'aborted') {
      return;
    }
    // no-speech / network / not-allowed / service-not-allowed 等全部打印
    console.warn('[VoiceInput] stopping due to error:', event.error);
    stopVoiceRecording();
  };

  recognition.onstart = () => {
  };

  recognition.onaudiostart = () => {
  };

  recognition.onsoundstart = () => {
  };

  recognition.onspeechstart = () => {
  };

  recognition.onspeechend = () => {
  };

  recognition.onsoundend = () => {
  };

  recognition.onaudioend = () => {
  };

  recognition.onend = () => {
    isRecording.value = false;
    recognitionInstance.value = null;
    voiceAnchorPos = 0;
    lastVoiceTextLength = 0;
  };

  return recognition;
};

const toggleVoiceRecording = () => {

  // Android Bridge 模式
  const bridge = (window as any).AndroidVoiceBridge;
  if (bridge) {
    if (isRecording.value) {
      bridge.stopListening();
      return;
    }
    // 记录锚点
    const instance = editor.value;
    voiceAnchorPos = instance ? instance.state.selection.from : 0;
    lastVoiceTextLength = 0;
    bridge.startListening();
    return;
  }

  // 浏览器原生 Web Speech API 模式
  if (isRecording.value) {
    stopVoiceRecording();
    return;
  }

  const recognition = initSpeechRecognition();
  if (!recognition) {
    console.warn('[VoiceInput] 浏览器不支持语音识别');
    return;
  }

  try {
    recognition.start();
    recognitionInstance.value = recognition;
    isRecording.value = true;
  } catch (error) {
    console.warn('[VoiceInput] 启动语音识别失败:', error);
    stopVoiceRecording();
  }
};

/* ═══════════════════ runtime queue 动画 ═══════════════════ */

const RUNTIME_QUEUE_ANIM_DURATION = 300;
const RUNTIME_QUEUE_ANIM_EASING = 'cubic-bezier(0.25, 0.8, 0.25, 1)';
const runtimeQueueList = ref<HTMLElement | null>(null);
const runtimeQueueItemRefs = new Map<string, HTMLElement>();
const runtimeQueuePrevIds: string[] = [];
const runtimeQueuePrevRects = new Map<string, DOMRect>();
const runtimeQueuePrevElements = new Map<string, HTMLElement>();
const runtimeQueueGhosts = new Map<string, HTMLElement>();
const runtimeQueueFallbackTimers = new WeakMap<HTMLElement, number>();
const runtimeQueueTransitioning = ref(false);
let runtimeQueueAnimationReady = false;
let runtimeQueueTransitionTimer: number | null = null;

const bindRuntimeQueueItemRef = (id: string) => (el: Element | null) => {
  if (!id) return;
  if (el instanceof HTMLElement) {
    runtimeQueueItemRefs.set(id, el);
  } else {
    runtimeQueueItemRefs.delete(id);
  }
};

const resolveRuntimeQueueOffset = (el: HTMLElement): number => {
  const rectHeight = Math.round(el.getBoundingClientRect().height);
  if (rectHeight > 0) return rectHeight;
  if (el.offsetHeight > 0) return el.offsetHeight;
  return 34;
};

const snapshotRuntimeQueueIds = (): string[] =>
  runtimeQueuedMessagesForRender.value
    .map((entry) => String(entry?.id || '').trim())
    .filter((id) => !!id);

const keepRuntimeQueueTransitionCollapsed = () => {
  runtimeQueueTransitioning.value = true;
  if (runtimeQueueTransitionTimer !== null) window.clearTimeout(runtimeQueueTransitionTimer);
  runtimeQueueTransitionTimer = window.setTimeout(() => {
    runtimeQueueTransitioning.value = false;
    runtimeQueueTransitionTimer = null;
    emitComposerHeight();
  }, RUNTIME_QUEUE_ANIM_DURATION + 40);
};

const cancelAnim = (el: HTMLElement) => {
  const timer = runtimeQueueFallbackTimers.get(el);
  if (timer) {
    clearTimeout(timer);
    runtimeQueueFallbackTimers.delete(el);
  }
  el.style.removeProperty('transition');
  el.style.removeProperty('transform');
  el.style.removeProperty('opacity');
};

/**
 * Schedule a CSS transition on an element.
 * All calls in the same synchronous batch share one RAF callback,
 * so all animations start in the same frame.
 */
const scheduleTransition = (
  el: HTMLElement,
  props: string[],
  from: Record<string, string>,
  to: Record<string, string>,
  done?: () => void
) => {
  const finish = () => {
    el.style.removeProperty('transition');
    props.forEach((p) => el.style.removeProperty(p));
    done?.();
  };
  // Apply from state immediately (no transition)
  el.style.transition = 'none';
  Object.entries(from).forEach(([k, v]) => {
    el.style.setProperty(k, v);
  });
  // Defer to next frame: enable transition + apply to state
  // All scheduleTransition calls in the same microtask share this RAF
  requestAnimationFrame(() => {
    el.style.transition = props
      .map((p) => `${p} ${RUNTIME_QUEUE_ANIM_DURATION}ms ${RUNTIME_QUEUE_ANIM_EASING}`)
      .join(', ');
    Object.entries(to).forEach(([k, v]) => {
      el.style.setProperty(k, v);
    });
    const timer = window.setTimeout(finish, RUNTIME_QUEUE_ANIM_DURATION + 40);
    runtimeQueueFallbackTimers.set(el, timer);
  });
};

/* ═══════════════════ FLIP 动画 ═══════════════════ */

// 上一轮渲染的 ID（onBeforeUpdate 时数据已更新，必须用这个记录旧位置）
let runtimeQueueLastRenderedIds: string[] = [];

onBeforeUpdate(() => {
  // 用上一轮渲染的 ID 记录旧位置（此时数据已更新，snapshotRuntimeQueueIds() 是新数据）
  runtimeQueuePrevIds.splice(0, runtimeQueuePrevIds.length, ...runtimeQueueLastRenderedIds);
  runtimeQueuePrevRects.clear();
  runtimeQueuePrevElements.clear();
  runtimeQueueLastRenderedIds.forEach((id) => {
    const el = runtimeQueueItemRefs.get(id);
    if (!el?.isConnected) return;
    runtimeQueuePrevRects.set(id, el.getBoundingClientRect());
    runtimeQueuePrevElements.set(id, el);
  });
});

onUpdated(() => {
  const list = runtimeQueueList.value;
  const currentIds = snapshotRuntimeQueueIds();

  // First render: just bootstrap tracking state
  if (!runtimeQueueAnimationReady) {
    runtimeQueueAnimationReady = true;
    runtimeQueueLastRenderedIds = [...currentIds];
    runtimeQueuePrevIds.splice(0, runtimeQueuePrevIds.length);
    runtimeQueuePrevRects.clear();
    runtimeQueuePrevElements.clear();
    return;
  }

  keepRuntimeQueueTransitionCollapsed();

  const previousIdSet = new Set(runtimeQueuePrevIds);
  const currentIdSet = new Set(currentIds);
  const enteringIds = currentIds.filter((id) => !previousIdSet.has(id));
  const leavingIds = runtimeQueuePrevIds.filter((id) => !currentIdSet.has(id));

  if (!enteringIds.length && !leavingIds.length) return;

  // ── Leave: create ghost clones at old positions ──
  leavingIds.forEach((id) => {
    const src = runtimeQueuePrevElements.get(id);
    const rect = runtimeQueuePrevRects.get(id);
    if (!src || !rect || !list?.isConnected) return;
    cancelAnim(src);

    // Clean up any stale ghost with same id
    const existing = runtimeQueueGhosts.get(id);
    if (existing) {
      cancelAnim(existing);
      existing.remove();
      runtimeQueueGhosts.delete(id);
    }

    const ghost = src.cloneNode(true) as HTMLElement;
    const lr = list.getBoundingClientRect();
    ghost.style.position = 'absolute';
    ghost.style.left = '0';
    ghost.style.top = `${Math.round(rect.top - lr.top)}px`;
    ghost.style.width = `${Math.round(rect.width > 0 ? rect.width : lr.width)}px`;
    ghost.style.pointerEvents = 'none';
    ghost.style.zIndex = '0';
    ghost.style.margin = '0';
    ghost.style.opacity = '1';
    ghost.querySelectorAll('button').forEach((b) => {
      if (b instanceof HTMLButtonElement) b.disabled = true;
    });
    list.appendChild(ghost);
    runtimeQueueGhosts.set(id, ghost);

    // Schedule ghost sink — shares RAF with item animations below
    const offset = resolveRuntimeQueueOffset(ghost);
    scheduleTransition(
      ghost,
      ['transform'],
      { transform: 'translateY(0)' },
      { transform: `translateY(${offset}px)` },
      () => {
        runtimeQueueGhosts.delete(id);
        ghost.remove();
      }
    );

    runtimeQueueItemRefs.delete(id);
  });

  // ── Remaining items: apply FLIP transforms ──
  currentIds.forEach((id) => {
    const el = runtimeQueueItemRefs.get(id);
    if (!el?.isConnected) return;
    cancelAnim(el);

    if (enteringIds.includes(id)) {
      // New item: rise from below
      const offset = resolveRuntimeQueueOffset(el);
      scheduleTransition(
        el,
        ['transform'],
        { transform: `translateY(${offset}px)` },
        { transform: 'translateY(0)' }
      );
      return;
    }

    // Existing item: invert the position delta
    const prev = runtimeQueuePrevRects.get(id);
    if (!prev) return;
    const next = el.getBoundingClientRect();
    const deltaY = prev.top - next.top;
    if (Math.abs(deltaY) < 0.5) return;
    scheduleTransition(
      el,
      ['transform'],
      { transform: `translateY(${deltaY}px)` },
      { transform: 'translateY(0)' }
    );
  });

  runtimeQueuePrevIds.splice(0, runtimeQueuePrevIds.length);
  runtimeQueuePrevRects.clear();
  runtimeQueuePrevElements.clear();
  // 保存本轮 ID 供下个 onBeforeUpdate 使用
  runtimeQueueLastRenderedIds = [...currentIds];
});

const hasComposerContent = computed(() => {
  const hasText = !!(props.inputMessage || '').trim();
  if (props.streamingMessage) {
    return hasText;
  }
  const hasImages = Array.isArray(props.selectedImages) && props.selectedImages.length > 0;
  const hasVideos = Array.isArray(props.selectedVideos) && props.selectedVideos.length > 0;
  return hasText || hasImages || hasVideos;
});

const showStopIcon = computed(() => {
  // 停止按钮只看主对话是否在 streaming。后台任务（子智能体/后台指令）的停止
  // 有独立的“x 个子智能体运行中”按钮和快捷窗口里的后台指令停止入口，不再集成在停止按钮上。
  return !!props.streamingMessage && !hasComposerContent.value;
});

const sendButtonDisabled = computed(() => {
  if (!props.isConnected) {
    return true;
  }
  // 主对话 streaming 时，有内容则可点击（走 stopTask）；无内容也显示停止按钮
  if (props.streamingMessage) {
    return false;
  }
  // 主对话空闲时输入栏正常可用，与是否后台任务在跑无关。
  if (props.inputLocked) {
    return true;
  }
  if (props.mediaUploading) {
    return true;
  }
  return !hasComposerContent.value;
});

const hasRuntimeLayoutExpansion = computed(() => {
  const hasQueue = runtimeQueuedMessagesForRender.value.length > 0;
  const hasImages = Array.isArray(props.selectedImages) && props.selectedImages.length > 0;
  const hasVideos = Array.isArray(props.selectedVideos) && props.selectedVideos.length > 0;
  const hasFiles = Array.isArray(props.selectedFiles) && props.selectedFiles.length > 0;
  return (
    hasQueue ||
    floatingStatusVisible.value ||
    showComposerAvatar.value ||
    skillSlashOpen.value ||
    fileAtOpen.value ||
    hasImages ||
    hasVideos ||
    hasFiles ||
    !!props.inputIsMultiline ||
    !!props.goalModeArmed ||
    !!props.goalRunning ||
    goalCompleted.value
  );
});

const goalCompleted = computed(
  () => String(props.goalProgress?.status || '').toLowerCase() === 'done'
);

const handleSlashMenuTransitionStart = () => {
  slashMenuTransitioning.value = true;
};

const handleSlashMenuAfterEnter = () => {
  slashMenuTransitioning.value = false;
  emitComposerHeight();
};

const handleSlashMenuAfterLeave = () => {
  slashMenuTransitioning.value = false;
  emitComposerHeight();
};

// 当消息队列非空或 skill 选择菜单展开时，把目标横幅收起为仅圆点，
// 让队列/菜单紧贴输入框，不再被横幅顶高。队列清空且无 skill 选择时自动恢复。
const goalBannerCollapsed = computed(
  () =>
    runtimeQueuedMessagesForRender.value.length > 0 ||
    runtimeQueueTransitioning.value ||
    skillSlashMenuOpen.value ||
    slashMenuTransitioning.value
);

const goalBannerTitle = computed(() => {
  if (props.goalRunning) return '目标模式运行中';
  if (goalCompleted.value) return '目标模式完成';
  return '目标模式已就绪';
});

const collectComposerVisualHeight = () => {
  const root = inputAreaRoot.value;
  const shell = compactInputShell.value;
  if (!root || !shell) {
    return 0;
  }
  // 用 offset 布局坐标（offsetTop/offsetHeight）计算可视高度，而非 getBoundingClientRect。
  // 后者会把进入/离开动画的 transform 一起算进去：状态栏进场时还停在 translateY(44px) 的低位，
  // 测得的增长量≈0，要等 260ms 动画结束才量到真实高度、再迟一拍下降——表现为「先挡住、完全出现后才下降」。
  // offset 系列只反映静止布局、不受 transform 影响，状态栏一挂载就按最终高度上报，
  // 输入栏下降与状态栏出现动画得以同时发生。这些浮层与 shell 同为 .stadium-input-wrapper 的直接子节点，
  // 其 offsetParent 一致，offsetTop 可直接比较。
  let top = shell.offsetTop;
  let bottom = shell.offsetTop + shell.offsetHeight;
  const slashMenu = root.querySelector('.skill-slash-menu');
  if (slashMenu instanceof HTMLElement) {
    const sTop = slashMenu.offsetTop;
    const sBottom = slashMenu.offsetTop + slashMenu.offsetHeight;
    return Math.max(0, Math.max(bottom, sBottom) - Math.min(top, sTop));
  }
  // 收起态横幅是脱离流、浮在角上的小圆点，不应计入为聊天区预留的高度，
  // 否则会把消息区可滚动范围顶高。故排除 .goal-mode-banner--collapsed。
  const nodes = root.querySelectorAll(
    '.runtime-queue-list:not(.runtime-queue-list--empty), .floating-status-row, .composer-avatar'
  );
  nodes.forEach((node) => {
    if (!(node instanceof HTMLElement)) return;
    // 离开态：floatingStatusVisible / showComposerAvatar 已翻 false 但元素仍在 DOM 中做退场动画。
    // 此时不再计入它的高度，让输入栏立即回升、与浮层的退场动画同步。
    if (node.classList.contains('floating-status-row') && !floatingStatusVisible.value) return;
    if (node.classList.contains('composer-avatar') && !showComposerAvatar.value) return;
    top = Math.min(top, node.offsetTop);
    bottom = Math.max(bottom, node.offsetTop + node.offsetHeight);
  });
  return Math.max(0, bottom - top);
};

const collectComposerShellHeight = () => {
  const shell = compactInputShell.value;
  if (!shell) {
    return 0;
  }
  return Math.max(0, shell.getBoundingClientRect().height);
};

const emitComposerHeight = () => {
  const visualHeight = collectComposerVisualHeight();
  if (!visualHeight) {
    return;
  }
  const shellHeight = collectComposerShellHeight();
  if (!hasRuntimeLayoutExpansion.value) {
    baselineComposerVisualHeight.value = visualHeight;
  } else if (baselineComposerVisualHeight.value <= 0 && shellHeight > 0) {
    baselineComposerVisualHeight.value = shellHeight;
  } else if (baselineComposerVisualHeight.value > 0 && shellHeight > 0) {
    baselineComposerVisualHeight.value = Math.min(baselineComposerVisualHeight.value, shellHeight);
  } else if (baselineComposerVisualHeight.value <= 0) {
    baselineComposerVisualHeight.value = visualHeight;
  }
  const baseline = baselineComposerVisualHeight.value || visualHeight;
  const growth = Math.max(0, visualHeight - baseline);
  const baseReservedHeight = 123;
  const reservedHeight = Math.ceil(baseReservedHeight + growth);
  emit('composer-height-change', {
    reservedHeight,
    visualHeight,
    growth
  });
};

const adjustTextareaSize = () => {
  const root = inputAreaRoot.value?.querySelector('.stadium-input-editor') as HTMLElement | null;
  if (!root) {
    return;
  }
  const target = root;
  const previousScrollTop = target.scrollTop;
  const selectionShouldStayVisible =
    document.activeElement === target || target.contains(document.activeElement);
  target.style.height = 'auto';
  const computedStyle = window.getComputedStyle(target);
  const lineHeight = parseFloat(computedStyle.lineHeight || '20') || 20;
  // 输入框最大高度：10 行（超出后内部滚动）
  const maxHeight = lineHeight * 10;
  const targetHeight = Math.min(target.scrollHeight, maxHeight);
  const lines = Math.max(1, Math.round(targetHeight / lineHeight));
  const multiline = targetHeight > lineHeight * 1.4;
  applyLineMetrics(lines, multiline);
  target.style.height = `${targetHeight}px`;
  keepEditorSelectionVisible(target, {
    previousScrollTop,
    selectionShouldStayVisible,
    padding: Math.max(4, lineHeight * 0.3)
  });
  nextTick(() => {
    emitComposerHeight();
  });
};

const keepEditorSelectionVisible = (
  target: HTMLElement,
  options: { previousScrollTop: number; selectionShouldStayVisible: boolean; padding: number }
) => {
  const maxScrollTop = Math.max(0, target.scrollHeight - target.clientHeight);
  if (maxScrollTop <= 0) {
    target.scrollTop = 0;
    return;
  }

  if (!options.selectionShouldStayVisible) {
    target.scrollTop = Math.min(maxScrollTop, Math.max(0, options.previousScrollTop));
    return;
  }

  requestAnimationFrame(() => {
    const instance = editor.value;
    if (!instance) {
      target.scrollTop = Math.min(maxScrollTop, Math.max(0, options.previousScrollTop));
      return;
    }

    try {
      const caretRect = instance.view.coordsAtPos(instance.state.selection.head);
      const targetRect = target.getBoundingClientRect();
      const padding = options.padding;
      const currentMaxScrollTop = Math.max(0, target.scrollHeight - target.clientHeight);
      let nextScrollTop = target.scrollTop;

      if (caretRect.bottom > targetRect.bottom - padding) {
        nextScrollTop += caretRect.bottom - targetRect.bottom + padding;
      } else if (caretRect.top < targetRect.top + padding) {
        nextScrollTop -= targetRect.top + padding - caretRect.top;
      }

      target.scrollTop = Math.min(currentMaxScrollTop, Math.max(0, nextScrollTop));
    } catch {
      target.scrollTop = Math.min(maxScrollTop, Math.max(0, options.previousScrollTop));
    }
  });
};

const onInput = (event: Event) => {
  const target = event.target as HTMLTextAreaElement;
  if (!props.isConnected || props.inputLocked) {
    target.value = props.inputMessage || '';
    return;
  }
  emit('update:input-message', target.value);
  emit('input-change');
  adjustTextareaSize();
  nextTick(refreshSkillSlashState);
};

const insertFileAtMentionByPath = (path: string, name: string) => {
  const token = findAtToken();
  const instance = editor.value;
  if (!instance || !token) return;
  const mentionType = instance.schema.nodes.mention;
  if (!mentionType) return;
  const mentionNode = mentionType.create({
    id: `file://${path}`,
    label: name
  });
  const { state, view } = instance;
  let tr = state.tr.delete(token.start, token.end);
  tr = tr.insert(token.start, mentionNode);
  const afterMention = token.start + mentionNode.nodeSize;
  tr = tr.insertText(' ', afterMention);
  tr = tr.setSelection(TextSelection.near(tr.doc.resolve(afterMention + 1)));
  view.dispatch(tr);
  view.focus();
  closeFileAtMenu();
  nextTick(() => {
    adjustTextareaSize();
  });
};

const onFileChange = async (event: Event) => {
  const target = event.target as HTMLInputElement;
  const files = target?.files || null;

  if (fileAtPickerActive.value && files && files.length > 0) {
    fileAtPickerActive.value = false;
    const file = files[0];
    try {
      const response = await fetch(
        `/api/project/files/search?q=${encodeURIComponent(file.name)}&limit=10`
      );
      const data = await response.json().catch(() => ({}));
      const items = Array.isArray(data?.data?.items) ? data.data.items : [];
      const match = items.find((item: FileAtItem) => item.name === file.name) || items[0];
      if (match) {
        insertFileAtMention(match);
      } else {
        insertFileAtMentionByPath(file.name, file.name);
      }
    } catch (error) {
      console.warn('[FileAt] 通过文件选择器查找路径失败:', error);
      insertFileAtMentionByPath(file.name, file.name);
    }
    if (target) target.value = '';
    return;
  }

  emit('file-selected', files);
  if (target) {
    target.value = '';
  }
};

const prepareMessageForSend = () => editorJsonToMessage(editor.value?.getJSON() as JSONContent);

const getComposerDraftMeta = () => ({
  editor_json: editor.value?.getJSON() || null,
  skill_refs: editorJsonToMessage(editor.value?.getJSON() as JSONContent).skillRefs
});

const restoreComposerDraftMeta = (
  meta?: { skill_refs?: SkillItem[]; editor_json?: JSONContent } | null
) => {
  const instance = editor.value;
  if (!instance) return;
  if (meta?.editor_json && typeof meta.editor_json === 'object') {
    syncingEditorFromProps = true;
    instance.commands.setContent(meta.editor_json);
    syncingEditorFromProps = false;
    emit('update:input-message', getEditorPlainText());
    nextTick(adjustTextareaSize);
  }
};

const triggerQuickUpload = () => {
  if (!props.isConnected || props.uploading) {
    return;
  }
  emit('quick-upload');
};

const currentPermissionLabel = computed(() => {
  const matched = (props.permissionOptions || []).find(
    (item) => item.value === props.currentPermissionMode
  );
  return matched ? matched.label : props.currentPermissionMode;
});

/** 对话类型选择器：选项定义 */
const agentTypeOptions = [
  {
    value: 'agent' as const,
    label: '智能体',
    description: '单智能体对话'
  },
  {
    value: 'multi_agent' as const,
    label: '多智能体',
    description: 'Team Leader + 子智能体协作'
  }
];

/** 已打开对话时类型不可变（类型是创建时确定的对话属性） */
const agentTypeLocked = computed(() => !!props.currentConversationId);

/** 按钮文案：有对话显示对话类型，空对话显示待创建类型 */
const agentTypeLabel = computed(() => {
  if (props.currentConversationId) {
    return props.currentConversationType === 'multi_agent' ? '多智能体' : '智能体';
  }
  return props.newConversationType === 'multi_agent' ? '多智能体' : '智能体';
});

/** 运行模式：对话运行中不可切换（后端也会 409 拒绝，UI 侧先锁定避免误点） */
const workModeLocked = computed(() => !!props.streamingMessage);

/** 运行模式按钮文案 */
const workModeLabel = computed(() => {
  const matched = (props.workModeOptions || []).find(
    (item) => item.value === props.currentWorkMode
  );
  return matched ? matched.label : props.currentWorkMode || '计划';
});

/** 计划模式下权限被锁定为只读（UI 禁用 + 后端强制） */
const permissionLockedByPlan = computed(() => props.currentWorkMode === 'plan');

/** 只读模式下执行环境锁定为沙箱（UI 禁用 + 后端强制） */
const executionLockedByReadonly = computed(() => props.currentPermissionMode === 'readonly');

/** 执行环境组禁用条件：plan 全锁 或 readonly 锁执行环境（权限组仅受 plan 锁） */
const executionModeLocked = computed(
  () => permissionLockedByPlan.value || executionLockedByReadonly.value
);

const currentExecutionShortLabel = computed(() => {
  const mode = String(props.currentExecutionMode || '');
  if (mode === 'direct') return '完全访问';
  if (mode === 'sandbox') return '沙箱';
  return mode;
});

const modelContextWindow = computed(() => {
  const list = Array.isArray(props.modelOptions) ? props.modelOptions : [];
  const current = list.find((item) => item.key === props.currentModelKey);
  return Number(current?.contextWindow || 0);
});

const autoDeepCompressEnabled = computed(() => {
  return !!personalizationStore?.form?.auto_deep_compress_enabled;
});

const deepCompressLimit = computed(() => {
  const custom = Number(personalizationStore?.form?.deep_compress_trigger_tokens || 0);
  if (custom > 0) return custom;
  return 150000;
});

const contextUsageLimit = computed(() => {
  if (autoDeepCompressEnabled.value) {
    return deepCompressLimit.value;
  }
  if (modelContextWindow.value > 0) {
    return modelContextWindow.value;
  }
  return 0;
});

const contextUsagePercent = computed(() => {
  const limit = Number(contextUsageLimit.value || 0);
  if (limit <= 0) return 0;
  const current = Math.max(0, Number(props.currentContextTokens || 0));
  return Math.max(0, Math.min(100, (current / limit) * 100));
});

const contextUsagePercentLabel = computed(() => `${Math.round(contextUsagePercent.value)}%`);

const contextUsageColor = computed(() => {
  const percent = contextUsagePercent.value;
  if (percent < 40) return 'var(--state-success)';
  if (percent <= 80) return 'var(--state-warning)';
  return 'var(--state-danger)';
});

const formatCompactTokens = (value: number) => {
  const num = Math.max(0, Number(value || 0));
  if (num >= 1000) {
    const raw = num / 1000;
    const text = raw >= 100 ? Math.round(raw).toString() : raw.toFixed(1).replace(/\.0$/, '');
    return `${text}k`;
  }
  return `${Math.round(num)}`;
};

const contextUsageCompactText = computed(() => {
  const current = formatCompactTokens(props.currentContextTokens || 0);
  const limit = Number(contextUsageLimit.value || 0);
  if (limit <= 0) return `${current}/--`;
  return `${current}/${formatCompactTokens(limit)}`;
});

defineExpose({
  inputAreaRoot,
  stadiumShellOuter,
  compactInputShell,
  stadiumInput,
  fileUploadInput,
  emitComposerHeight,
  prepareMessageForSend,
  getComposerDraftMeta,
  restoreComposerDraftMeta
});

watch(
  () => props.inputMessage,
  async () => {
    const instance = editor.value;
    if (
      instance &&
      !syncingEditorFromProps &&
      getEditorPlainText() !== (props.inputMessage || '')
    ) {
      syncingEditorFromProps = true;
      instance.commands.setContent(textToTiptapContent(props.inputMessage || ''));
      syncingEditorFromProps = false;
    }
    await nextTick();
    adjustTextareaSize();
    refreshSkillSlashState();
  }
);

watch(
  () => props.isConnected,
  async (connected) => {
    editor.value?.setEditable(!!connected && !props.inputLocked);
    if (!connected) {
      editor.value?.commands.blur();
    }
    await nextTick();
    adjustTextareaSize();
  }
);

watch(
  () => props.inputLocked,
  (locked) => {
    editor.value?.setEditable(props.isConnected && !locked);
  }
);

watch(
  () => props.currentConversationId,
  async () => {
    baselineComposerVisualHeight.value = 0;
    await nextTick();
    emitComposerHeight();
  }
);

watch(
  () => props.runtimeQueuedMessages,
  async () => {
    await nextTick();
    emitComposerHeight();
  },
  { deep: true }
);

watch(
  () => props.projectGitSummary,
  async () => {
    if (!projectGitSummaryForRender.value) {
      closeProjectGitMenu();
    }
    await nextTick();
    emitComposerHeight();
  },
  { deep: true }
);

watch(floatingStatusVisible, async () => {
  await nextTick();
  emitComposerHeight();
  window.setTimeout(() => {
    emitComposerHeight();
  }, 280);
});

watch(showComposerAvatar, async () => {
  await nextTick();
  emitComposerHeight();
  window.setTimeout(() => {
    emitComposerHeight();
  }, 280);
});

watch(
  () => props.avatarStatus?.mode,
  () => {
    if (props.avatarStatus?.mode !== 'idle') clearAvatarPokeText();
  }
);

watch(
  () => [props.selectedImages?.length || 0, props.selectedVideos?.length || 0],
  async () => {
    await nextTick();
    emitComposerHeight();
  }
);

// skill 菜单开关会切换目标横幅收起/展开态，需重新上报高度，
// 否则展开回来时聊天区预留高度不会同步恢复。
watch(goalBannerCollapsed, async () => {
  await nextTick();
  emitComposerHeight();
});

// 发送消息或输入锁定时，自动停止语音录音
watch(
  () => props.streamingMessage || props.inputLocked,
  (shouldStop) => {
    if (shouldStop && isRecording.value) {
      stopVoiceRecording();
    }
  }
);

onMounted(() => {
  document.addEventListener('click', closeProjectGitMenu);
  adjustTextareaSize();
  nextTick(() => {
    emitComposerHeight();
    if (typeof ResizeObserver !== 'undefined') {
      composerResizeObserver = new ResizeObserver(() => {
        emitComposerHeight();
      });
      if (inputAreaRoot.value) {
        composerResizeObserver.observe(inputAreaRoot.value);
      }
      if (stadiumShellOuter.value) {
        composerResizeObserver.observe(stadiumShellOuter.value);
      }
      if (compactInputShell.value) {
        composerResizeObserver.observe(compactInputShell.value);
      }
    }
  });
});

onBeforeUnmount(() => {
  stopVoiceRecording();
  document.removeEventListener('click', closeProjectGitMenu);
  editor.value?.destroy();
  runtimeQueueItemRefs.forEach((el) => {
    if (el) {
      cancelAnim(el);
    }
  });
  runtimeQueueGhosts.forEach((ghost) => {
    cancelAnim(ghost);
    ghost.remove();
  });
  runtimeQueueGhosts.clear();
  runtimeQueueItemRefs.clear();
  runtimeQueuePrevIds.splice(0, runtimeQueuePrevIds.length);
  runtimeQueuePrevRects.clear();
  runtimeQueuePrevElements.clear();
  runtimeQueueAnimationReady = false;
  runtimeQueueLastRenderedIds = [];
  if (runtimeQueueTransitionTimer !== null) {
    window.clearTimeout(runtimeQueueTransitionTimer);
    runtimeQueueTransitionTimer = null;
  }
  runtimeQueueTransitioning.value = false;
  if (composerResizeObserver) {
    composerResizeObserver.disconnect();
    composerResizeObserver = null;
  }
  clearAvatarPokeText();
});
</script>

<style scoped>
.composer-avatar {
  position: absolute;
  left: 0;
  right: auto;
  bottom: calc(100% + 6px);
  z-index: auto;
  display: flex;
  align-items: flex-end;
  justify-content: flex-start;
  width: 45px;
  height: 45px;
  pointer-events: auto;
  color: var(--text-primary);
}

.floating-status-row {
  position: absolute;
  left: 49px;
  right: 49px;
  bottom: calc(100% + 6px);
  z-index: auto;
  height: 34px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  pointer-events: none;
}

.floating-project-status {
  position: relative;
  left: auto;
  right: auto;
  bottom: auto;
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 10px 4px 4px;
  border: 1px solid var(--border-default);
  border-radius: 14px;
  background: var(--theme-surface-soft);
  box-shadow: none;
  pointer-events: auto;
}

.composer-avatar .status-avatar {
  display: block;
  flex-shrink: 0;
  margin-bottom: -5px;
}

.composer-avatar__text {
  position: absolute;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
  line-height: 16px;
  color: var(--text-secondary);
  background: var(--chat-surface-color);
  padding: 2px 8px;
  border-radius: 9999px;
  pointer-events: none;
  z-index: 1;
}

.composer-avatar__text--right {
  left: calc(100% + 4px);
  right: auto;
  top: auto;
  bottom: 0;
  transform: none;
}

.composer-avatar__text--top {
  left: -8px;
  bottom: 40px;
  transform: none;
}

.composer-avatar-text-fade-enter-active,
.composer-avatar-text-fade-leave-active {
  transition: opacity 0.18s ease;
}

.composer-avatar-text-fade-enter-from,
.composer-avatar-text-fade-leave-to {
  opacity: 0;
}

.composer-avatar-text-fade-enter-to,
.composer-avatar-text-fade-leave-from {
  opacity: 1;
}

.floating-project-status__left,
.floating-project-status__right {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.floating-project-status__left {
  flex: 1 1 auto;
}

.floating-project-status__right {
  flex: 0 0 auto;
  margin-left: auto;
}

.floating-project-status__menu-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-width: 0;
  height: 26px;
}

.floating-project-status__button,
.floating-project-status__notice {
  height: 26px;
  display: inline-flex;
  align-items: center;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}

.floating-project-status__button {
  min-width: 0;
  padding: 0 9px;
  cursor: pointer;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.floating-project-status__button:hover,
.floating-project-status__button--active,
.floating-project-status__notice--button:hover,
.floating-project-status__submenu button:hover {
  background: var(--theme-tab-active);
  box-shadow: 0 1px 2px var(--shadow-color);
}

.floating-project-status__button--project {
  max-width: min(260px, 36vw);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
}

.floating-project-status__button--branch {
  color: var(--text-secondary);
}

.floating-project-status__notice {
  padding: 0 8px;
  color: var(--text-secondary);
}

.floating-project-status__notice--button {
  cursor: pointer;
}

.floating-project-status__stats {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 26px;
  padding: 0 8px;
  border: none;
  border-radius: 10px;
  background: transparent;
  font-size: 12px;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.floating-project-status__stats:hover {
  background: var(--theme-tab-active);
  box-shadow: 0 1px 2px var(--shadow-color);
}

.floating-project-status__add {
  display: inline-flex;
  align-items: center;
  color: var(--git-diff-add-text);
}

.floating-project-status__del {
  display: inline-flex;
  align-items: center;
  color: var(--git-diff-del-text);
}

.floating-project-status__terminal {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 8px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.2;
  cursor: pointer;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.floating-project-status__terminal:hover {
  background: var(--theme-tab-active);
  color: var(--text-primary);
  box-shadow: 0 1px 2px var(--shadow-color);
}

.floating-project-status__submenu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 46;
  width: max-content;
  min-width: 160px;
  max-width: min(260px, 72vw);
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  border: 1px solid var(--border-default);
  border-radius: 14px;
  background: var(--theme-surface-soft);
  box-shadow: none;
  overflow: hidden;
}

.floating-project-status__submenu button {
  width: 100%;
  min-width: 148px;
  height: 36px;
  padding: 0 10px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.2;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

:global(html[data-theme='dark'] .floating-project-status),
:global(body[data-theme='dark'] .floating-project-status),
:global(html[data-theme='dark'] .floating-project-status__submenu),
:global(body[data-theme='dark'] .floating-project-status__submenu) {
  background: var(--badge-bg) !important;
  border-color: var(--border-card-strong);
}

:global(html[data-theme='dark'] .floating-project-status__button:hover),
:global(body[data-theme='dark'] .floating-project-status__button:hover),
:global(html[data-theme='dark'] .floating-project-status__notice--button:hover),
:global(body[data-theme='dark'] .floating-project-status__notice--button:hover),
:global(html[data-theme='dark'] .floating-project-status__stats:hover),
:global(body[data-theme='dark'] .floating-project-status__stats:hover),
:global(html[data-theme='dark'] .floating-project-status__submenu button:hover),
:global(body[data-theme='dark'] .floating-project-status__submenu button:hover) {
  box-shadow: 0 1px 2px var(--shadow-color);
}

/* 经典主题：git 状态栏及子菜单与输入栏壳体对齐为纯白 --surface-raised
   （基础 --theme-surface-soft 在经典为米色 #f5f0e8，与白色输入栏不一致） */
:global(html[data-theme='classic'] .floating-project-status),
:global(body[data-theme='classic'] .floating-project-status),
:global(html[data-theme='classic'] .floating-project-status__submenu),
:global(body[data-theme='classic'] .floating-project-status__submenu) {
  background: var(--surface-raised);
}

.floating-status-motion-enter-active,
.floating-status-motion-leave-active {
  transition:
    opacity 180ms ease,
    transform 260ms cubic-bezier(0.25, 0.8, 0.25, 1);
  transform-origin: bottom center;
  will-change: transform, opacity;
  /* 状态栏本体永远在最底层：输入栏 shell(z-index:2) > / 菜单(z-index:1) > 状态栏(auto)。
     不在这里设置 z-index，避免创建低层 stacking context 后把二级菜单也压在输入栏下。 */
  z-index: auto;
}

.floating-status-motion-enter-from,
.floating-status-motion-leave-to {
  opacity: 0;
  transform: translateY(44px);
}

.floating-status-motion-enter-to,
.floating-status-motion-leave-from {
  opacity: 1;
  transform: translateY(0);
}

/* 目标模式横幅：与 + 按钮左对齐，置于输入框上方 */
.goal-mode-banner {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px 4px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--theme-surface-soft) 92%, var(--text-tertiary) 8%);
  border: 1px solid var(--theme-chip-border);
  user-select: none;
  transition:
    gap 0.2s ease,
    padding 0.2s ease;
}
/* 收起态：仅保留圆点与外圈，文字消失、右侧收拢，圆点位置（左侧 padding）不变。
   关键：切到 absolute 脱离文档流，避免横幅占用一行流高度把队列/skill 菜单顶起来；
   锚定在输入框上方 6px、左 4px，与展开时的视觉位置一致，圆点位置不变。 */
.goal-mode-banner--collapsed {
  position: absolute;
  left: 4px;
  bottom: calc(100% + 6px);
  z-index: 2;
  margin: 0;
  gap: 0;
  padding-right: 10px;
}
.goal-mode-banner__text {
  overflow: hidden;
  white-space: nowrap;
  max-width: 200px;
  opacity: 1;
  transition:
    max-width 0.2s ease,
    opacity 0.18s ease;
}
.goal-mode-banner--collapsed .goal-mode-banner__text {
  max-width: 0;
  opacity: 0;
}
.goal-mode-banner--running {
  color: var(--accent);
  border-color: var(--accent);
  background: color-mix(in srgb, var(--theme-surface-soft) 82%, var(--accent) 18%);
}
.goal-mode-banner--done {
  color: var(--state-success);
  border-color: var(--state-success);
  background: color-mix(in srgb, var(--theme-surface-soft) 82%, var(--state-success) 18%);
}
:global(:root[data-theme='dark']) .goal-mode-banner,
:global(body[data-theme='dark']) .goal-mode-banner {
  color: var(--text-secondary);
  background: color-mix(in srgb, var(--theme-surface-muted) 82%, var(--text-tertiary) 18%);
  border-color: var(--theme-control-border-strong);
}
:global(:root[data-theme='dark']) .goal-mode-banner--running,
:global(body[data-theme='dark']) .goal-mode-banner--running {
  color: var(--text-primary);
  background: color-mix(in srgb, var(--theme-surface-muted) 72%, var(--accent) 28%);
  border-color: color-mix(in srgb, var(--accent) 70%, white 30%);
}
:global(:root[data-theme='dark']) .goal-mode-banner--done,
:global(body[data-theme='dark']) .goal-mode-banner--done {
  color: color-mix(in srgb, var(--state-success) 30%, white);
  background: color-mix(in srgb, var(--theme-surface-muted) 72%, var(--state-success) 28%);
  border-color: color-mix(in srgb, var(--state-success) 70%, white 30%);
}
.goal-mode-banner--clickable {
  cursor: pointer;
}
.goal-mode-banner__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-tertiary);
}
.goal-mode-banner--running .goal-mode-banner__dot {
  background: var(--accent);
  animation: goal-banner-pulse 1.4s ease-in-out infinite;
}
.goal-mode-banner--done .goal-mode-banner__dot {
  background: var(--state-success);
}
@keyframes goal-banner-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.goal-banner-fade-enter-active,
.goal-banner-fade-leave-active {
  transition:
    opacity 0.18s ease,
    transform 0.18s ease;
}
.goal-banner-fade-enter-from,
.goal-banner-fade-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

.image-inline-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 4px 10px 2px;
  line-height: 1.4;
}

.image-thumbnail-wrapper {
  position: relative;
  width: 60px;
  height: 60px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid var(--border-default);
}

/* 圆角裁切下移到 img 自身，wrapper 不再 overflow:hidden，
   以便删除按钮可以半外凸（与文件块统一） */
.image-thumbnail {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  border-radius: 5px;
}

/* 与 FileChips 的 .file-chip-remove 保持一致 */
.image-remove-btn-hover {
  position: absolute;
  top: -7px;
  right: -7px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--surface-base);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  cursor: pointer;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: color 0.15s ease;
}

.image-thumbnail-wrapper:hover .image-remove-btn-hover {
  display: flex;
}

.image-remove-btn-hover:hover {
  color: var(--state-danger);
}

body[data-theme='dark'] .image-thumbnail-wrapper {
  border-color: var(--border-strong);
}

body[data-theme='dark'] .image-remove-btn-hover {
  background: color-mix(in srgb, var(--chip-bg) 88%, white);
  border-color: var(--border-strong);
}
</style>

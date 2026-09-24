<template>
  <AppShell :download-file="downloadFile" :download-folder="downloadFolder">
    <!-- 拖拽上传提示层 -->
    <transition name="drag-overlay-fade">
      <div v-if="dragOverActive" class="drag-upload-overlay">
        <div class="drag-upload-content">
          <div class="drag-upload-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </div>
          <div class="drag-upload-text">{{ $t('appCore.dragUploadText') }}</div>
          <div class="drag-upload-hint">{{ $t('appCore.dragUploadHint') }}</div>
        </div>
      </div>
    </transition>

    <div v-if="!isConnected && messages.length === 0" class="app-loading-state">
      <div class="loading-animation" aria-hidden="true">
        <!-- From Uiverse.io by Nawsome -->
        <div class="boxes">
          <div class="box">
            <div></div>
            <div></div>
            <div></div>
            <div></div>
          </div>
          <div class="box">
            <div></div>
            <div></div>
            <div></div>
            <div></div>
          </div>
          <div class="box">
            <div></div>
            <div></div>
            <div></div>
            <div></div>
          </div>
          <div class="box">
            <div></div>
            <div></div>
            <div></div>
            <div></div>
          </div>
        </div>
      </div>
      <div class="loading-copy">
        <h2>{{ $t('common.loading') }}</h2>
        <p>{{ $t('appCore.connectingServer') }}</p>
        <button type="button" class="loading-refresh-btn" @click="refreshCurrentPage">
          {{ $t('appCore.refreshPage') }}
        </button>
      </div>
    </div>

    <template v-else>
      <div class="main-container">
        <ConversationSidebar
          v-if="!isMobileViewport"
          :icon-style="iconStyle"
          :format-time="formatTime"
          :running-tasks="runningWorkspaceTasks"
          :current-task-in-progress="taskInProgress"
          :current-conversation-id="currentConversationId"
          :current-workspace-id="currentHostWorkspaceId"
          :default-workspace-id="defaultHostWorkspaceId"
          :workspaces="hostWorkspaces"
          :workspace-kind="versioningHostMode ? 'workspace' : 'project'"
          :host-workspace-enabled="versioningHostMode || dockerProjectMode"
          :workspace-busy="hostWorkspaceSwitching || hostWorkspaceManageSubmitting"
          :workspace-create-submitting="hostWorkspaceCreateSubmitting"
          :workspace-error="hostWorkspaceCreateError"
          :group-by-workspace="personalizationStore?.form?.group_sidebar_by_workspace"
          :versioning-host-mode="versioningHostMode"
          @toggle="toggleSidebar"
          @create="createNewConversation"
          @search="handleSidebarSearchInput"
          @search-submit="handleSidebarSearchSubmit"
          @search-more="loadMoreSearchResults"
          @select="loadConversation"
          @select-running-task="openRunningWorkspaceTask"
          @load-more="loadMoreConversations"
          @personal="openPersonalPage"
          @delete="deleteConversation"
          @duplicate="duplicateConversation"
          @switch-workspace="handleHostWorkspaceSwitch"
          @create-workspace="submitHostWorkspaceCreateFromManage"
          @delete-workspace="handleDeleteWorkspaceFromSwitcher"
          @set-default-workspace="setDefaultHostWorkspace"
          @select-workspace-conversation="handleSelectWorkspaceConversation"
          @create-workspace-conversation="createWorkspaceConversation"
          @reveal-workspace="revealHostWorkspace"
          @rename-workspace="handleRenameWorkspaceFromSidebar"
          @pin-workspace="handlePinWorkspaceFromSidebar"
          @conversation-type-change="handleSidebarConversationTypeChange"
          @open-workflows="openWorkflowsPage"
        />

        <main
          class="chat-container"
          :class="{
            'chat-container--mobile': isMobileViewport,
            'chat-container--app-shell': isAppShell,
            'chat-container--monitor': chatDisplayMode === 'monitor',
            'has-title-ribbon': titleRibbonVisible
          }"
          :style="chatContainerStyle"
        >
          <TokenDrawer
            :visible="Boolean(currentConversationId)"
            :collapsed="tokenPanelCollapsed"
            @toggle="handleTokenPanelToggleClick"
            :current-conversation-tokens="currentConversationTokens"
            :current-context-tokens="currentContextTokens"
            :container-status="containerStatus"
            :container-net-rate="containerNetRate"
            :project-storage="projectStorage"
            :usage-quota="usageQuota"
            :format-token-count="formatTokenCount"
            :format-percentage="formatPercentage"
            :format-bytes="formatBytes"
            :format-quota-value="formatQuotaValue"
            :format-reset-time="formatResetTime"
            :format-rate="formatRate"
          />

          <div v-if="titleRibbonVisible" class="conversation-ribbon" ref="titleRibbon">
            <div class="conversation-ribbon__blur" aria-hidden="true"></div>
            <button
              type="button"
              class="conversation-ribbon__selector"
              data-tutorial="header-model-selector"
              :class="{ open: headerMenuOpen }"
              @click.stop="toggleHeaderMenu"
              :disabled="!isConnected"
            >
              <span class="selector-label">
                <span class="selector-model">{{ currentModelLabel }}</span>
                <span class="selector-sep">·</span>
                <span class="selector-mode">{{ headerRunModeLabel }}</span>
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

            <span class="conversation-ribbon__text">{{
              titleTypingText || currentConversationTitle
            }}</span>

            <transition name="header-menu">
              <div v-if="headerMenuOpen" class="model-mode-dropdown" ref="headerMenu">
                <div class="dropdown-column" data-tutorial="header-model-options">
                  <div class="dropdown-title">
                    <button
                      v-if="headerModelMenuPage === 'codex'"
                      type="button"
                      class="dropdown-title-back"
                      @click.stop="headerModelMenuPage = 'main'"
                    >
                      ‹ {{ $t('appCore.back') }}
                    </button>
                    <template v-else>{{ $t('appCore.model') }}</template>
                  </div>
                  <div class="model-menu-panes">
                    <Transition
                      :name="headerModelMenuPage === 'codex' ? 'type-slide-left' : 'type-slide-right'"
                      @after-leave="clearModelMenuPaneHeight"
                    >
                      <div
                        v-if="headerModelMenuPage === 'main'"
                        key="main"
                        class="dropdown-list dropdown-list--models model-menu-pane"
                      >
                        <button
                          v-for="option in regularModelOptions"
                          :key="option.key"
                          type="button"
                          class="dropdown-item"
                          :class="{ active: option.key === currentModelKey, disabled: option.disabled }"
                          @click.stop="handleHeaderModelSelect(option.key, option.disabled)"
                          :disabled="streamingMessage || !isConnected || option.disabled"
                        >
                          <div class="item-label">{{ option.label }}</div>
                          <span v-if="option.key === currentModelKey" class="item-check">✓</span>
                        </button>
                        <button
                          type="button"
                          class="dropdown-item dropdown-item--codex-entry"
                          @click.stop="headerModelMenuPage = 'codex'"
                        >
                          <div class="item-label">{{ $t('appCore.codexEntry') }}</div>
                          <span class="item-chevron">›</span>
                        </button>
                      </div>
                      <div
                        v-else
                        key="codex"
                        class="dropdown-list dropdown-list--models model-menu-pane"
                      >
                        <button
                          v-for="option in codexModelOptions"
                          :key="option.key"
                          type="button"
                          class="dropdown-item"
                          :class="{ active: option.key === currentModelKey, disabled: option.disabled }"
                          @click.stop="handleHeaderModelSelect(option.key, option.disabled)"
                          :disabled="streamingMessage || !isConnected || option.disabled"
                        >
                          <div class="item-label">{{ option.label }}</div>
                          <span v-if="option.key === currentModelKey" class="item-check">✓</span>
                        </button>
                        <div v-if="!codexModelOptions.length" class="dropdown-empty-hint">
                          {{ $t('appCore.codexNotConnected') }}
                        </div>
                      </div>
                    </Transition>
                  </div>
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
                    <EffortSlider
                      :model-value="reasoningEffort"
                      @update:model-value="setReasoningEffort"
                    />
                  </div>
                </div>
              </div>
            </transition>
          </div>

          <ChatArea
            v-show="chatDisplayMode === 'chat'"
            ref="messagesArea"
            :messages="messages"
            :icon-style="iconStyle"
            :expanded-blocks="expandedBlocks"
            :render-markdown="renderMarkdown"
            :toggle-block="toggleBlock"
            :handle-thinking-scroll="handleThinkingScroll"
            :streaming-message="streamingMessage"
            :get-tool-icon="getToolIcon"
            :get-tool-status-text="getToolStatusText"
            :get-tool-animation-class="getToolAnimationClass"
            :get-tool-description="getToolDescription"
            :format-search-topic="formatSearchTopic"
            :format-search-time="formatSearchTime"
            :format-search-domains="formatSearchDomains"
            :history-loading="historyLoading"
            @stick-state-change="handleStickStateChange"
            @user-scroll-intent="handleUserScrollIntent"
          />
          <VirtualMonitorSurface v-if="chatDisplayMode === 'monitor'" />

          <div v-if="blankHeroActive" class="blank-hero-overlay">
            <StatusAvatar v-if="showStatusAvatar" mode="idle" :size="58" tracking />
            <p class="blank-hero-text">{{ blankWelcomeText }}</p>
          </div>

          <transition name="scroll-to-bottom-fade">
            <div v-if="showScrollToBottomButton" class="scroll-lock-toggle">
              <button @click="toggleScrollLock" class="scroll-lock-btn">
                <svg
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path d="M12 5v12" />
                  <path d="M7 13l5 5 5-5" />
                </svg>
              </button>
            </div>
          </transition>

          <div class="composer-container" :class="{ 'blank-hero-mode': composerHeroActive }">
            <InputComposer
              ref="inputComposer"
              :input-message="inputMessage"
              :input-is-multiline="inputIsMultiline"
              :input-is-focused="inputIsFocused"
              :is-connected="isConnected"
              :streaming-message="streamingUi"
              :input-locked="displayLockEngaged"
              :uploading="uploading"
              :media-uploading="mediaUploading"
              :thinking-mode="thinkingMode"
              :run-mode="resolvedRunMode"
              :model-menu-open="modelMenuOpen"
              :model-options="modelOptions"
              :current-model-key="currentModelKey"
              :quick-menu-open="quickMenuOpen"
              :tool-menu-open="toolMenuOpen"
              :mode-menu-open="modeMenuOpen"
              :tool-settings="toolSettings"
              :tool-settings-loading="toolSettingsLoading"
              :settings-open="settingsOpen"
              :compressing="compressionActiveForCurrentConversation"
              :current-conversation-id="currentConversationId"
              :icon-style="iconStyle"
              :tool-category-icon="toolCategoryIcon"
              :selected-images="selectedImages"
              :selected-videos="selectedVideos"
              :selected-files="selectedFiles"
              :block-upload="policyUiBlocks.block_upload"
              :block-tool-toggle="policyUiBlocks.block_tool_toggle"
              :block-realtime-terminal="policyUiBlocks.block_realtime_terminal"
              :terminal-count="terminalSessions ? Object.keys(terminalSessions).length : 0"
              :host-mode="versioningHostMode"
              :block-focus-panel="policyUiBlocks.block_focus_panel"
              :block-token-panel="policyUiBlocks.block_token_panel"
              :block-compress-conversation="policyUiBlocks.block_compress_conversation"
              :block-conversation-review="policyUiBlocks.block_conversation_review"
              :current-permission-mode="currentPermissionMode"
              :permission-menu-open="permissionMenuOpen"
              :permission-options="permissionModeOptions"
              :execution-mode-enabled="executionModeEnabled"
              :current-execution-mode="currentExecutionMode"
              :execution-mode-options="executionModeOptions"
              :network-permission-enabled="networkPermissionEnabled"
              :current-network-permission="currentNetworkPermission"
              :network-permission-options="networkPermissionOptions"
              :current-context-tokens="currentContextTokens"
              :versioning-enabled="versioningEnabled"
              :runtime-queued-messages="runtimeQueuedMessages"
              :has-pending-runtime-guidance="runtimeGuidanceFallbackQueue.length > 0"
              :project-git-summary="projectGitSummary"
              :user-question-minimized="userQuestionMinimized"
              :pending-user-question-count="pendingUserQuestions.length"
              :plan-approval-minimized="planApprovalMinimized"
              :pending-plan-approval-count="pendingPlanApprovals.length"
              :goal-mode-armed="goalModeArmed"
              :goal-running="goalRunning"
              :goal-progress="goalProgress"
              :current-conversation-type="currentConversationType"
              :new-conversation-type="newConversationType"
              :agent-type-menu-open="agentTypeMenuOpen"
              :current-work-mode="currentWorkMode"
              :work-mode-menu-open="workModeMenuOpen"
              :work-mode-options="workModeOptions"
              :host-workspaces="hostWorkspaces"
              :current-host-workspace-id="currentHostWorkspaceId"
              :host-workspace-switching="hostWorkspaceSwitching"
              :workspace-kind="versioningHostMode ? 'workspace' : (dockerProjectMode ? 'project' : null)"
              :main-chat-idle="mainChatIdle"
              :active-sub-agent-count="activeSubAgentCount"
              :avatar-status="showStatusAvatar && messages.length > 0 ? avatarStatus : null"
              @restore-user-question="restoreUserQuestionDialog"
              @restore-plan-approval="restorePlanApprovalDialog"
              @stop-all-sub-agents="handleStopAllSubAgents"
              @update:input-message="inputSetMessage"
              @input-change="handleInputChange"
              @input-focus="handleInputFocus"
              @input-blur="handleInputBlur"
              @toggle-quick-menu="toggleQuickMenu"
              @send-message="sendMessage"
              @send-or-stop="handleSendOrStop"
              @quick-upload="handleQuickUpload"
              @toggle-tool-menu="toggleToolMenu"
              @toggle-mode-menu="toggleModeMenu"
              @toggle-model-menu="toggleModelMenu"
              @select-run-mode="handleModeSelect"
              @select-model="handleModelSelect"
              @toggle-settings="toggleSettings"
              @update-tool-category="updateToolCategory"
              @realtime-terminal="handleRealtimeTerminalClick"
              @toggle-focus-panel="handleFocusPanelToggleClick"
              @toggle-token-panel="(val) => handleTokenPanelToggleClick(val)"
              @compress-conversation="handleCompressConversationClick"
              @toggle-approval-panel="handleApprovalPanelToggleClick"
              @file-selected="handleFileSelected"
              @paste-files="handlePastedFiles"
              @pick-images="openImagePicker"
              @pick-video="openVideoPicker"
              @remove-image="handleRemoveImage"
              @remove-video="handleRemoveVideo"
              @remove-file="handleRemoveFile"
              @open-review="openReviewDialog"
              @toggle-permission-menu="togglePermissionMenu"
              @change-permission-mode="changePermissionMode"
              @change-execution-mode="changeExecutionMode"
              @change-network-permission="changeNetworkPermission"
              @open-path-authorization="openPathAuthorizationDialog"
              @open-versioning-dialog="openVersioningDialog"
              @guide-runtime-message="handleGuideRuntimeMessage"
              @delete-runtime-message="handleDeleteRuntimeMessage"
              @composer-height-change="handleComposerHeightChange"
              @toggle-goal-mode="handleToggleGoalMode"
              @workflow-activated="handleWorkflowActivated"
              @toggle-agent-type-menu="handleToggleAgentTypeMenu"
              @select-new-conversation-type="handleSelectNewConversationType"
              @toggle-work-mode-menu="toggleWorkModeMenu"
              @change-work-mode="changeWorkMode"
              @new-conversation="createNewConversation"
              @switch-host-workspace="handleHostWorkspaceSwitch"
              @fetch-host-workspaces="fetchHostWorkspaces"
              @open-goal-dialog="goalDialogOpen = true"
              @open-git-changes-panel="openGitChangesPanel"
            />
          </div>
        </main>
        <QuickDock
          v-if="!isMobileViewport"
          :host-mode="versioningHostMode"
        />
        <!-- 快捷窗口展开/收起按钮：零宽锚点始终贴在 QuickDock 右缘，
             按钮绝对定位悬浮在原处，不随面板挤压移动；
             无内容时也常显（不随 qdHasContent 隐藏），避免按钮时隐时现 -->
        <div
          v-if="!isMobileViewport"
          class="qd-toggle-anchor"
        >
          <button
            type="button"
            class="qd-toggle"
            :aria-label="qdExpanded ? $t('appCore.collapseQuickDock') : $t('appCore.expandQuickDock')"
            :aria-pressed="qdExpanded"
            @click="qdUserCollapsed = !qdUserCollapsed"
          >
            <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true">
              <line x1="2" y1="3.5" x2="16" y2="3.5" />
              <line x1="2" y1="7.5" x2="10" y2="7.5" />
              <line x1="2" y1="11.5" x2="14" y2="11.5" />
              <line x1="2" y1="15.5" x2="7" y2="15.5" />
            </svg>
          </button>
        </div>
        <!-- 文件预览面板：桌面端挤压式占位列；移动端由 CSS 改为叠加悬浮层，
             常驻挂载（内部由 previewPath 驱动显隐），保证移动端悬浮层之上可再叠加预览 -->
        <FilePreviewPanel />
        <CitationPopover :host-mode="versioningHostMode" />
        <div
          v-if="!isMobileViewport && (terminalPanelOpen || gitChangesPanelOpen)"
          class="resize-handle resize-handle--right-panels"
          @mousedown="startResize('right', $event)"
        ></div>
        <transition name="right-panels-slide">
          <div
            v-if="!isMobileViewport && (terminalPanelOpen || gitChangesPanelOpen)"
            class="right-panels"
            :style="rightPanelsStyle"
          >
            <div class="right-panels__slot">
              <TerminalPanel
                :width="Math.min(rightWidth || 420, 600)"
                :workspace-id="currentHostWorkspaceId"
                :conversation-id="currentConversationId"
                @close="closeTerminalPanel"
              />
            </div>
            <div
              v-if="terminalPanelOpen && gitChangesPanelOpen"
              class="right-panels__split-handle"
              @mousedown="startRightSplitResize"
            ></div>
            <div class="right-panels__slot">
              <GitChangesPanel
                :width="Math.min(rightWidth || 420, 600)"
                :loading="gitChangesLoading"
                :error="gitChangesError"
                :diff="gitChangesDiff"
                :fold-contexts="gitChangesFoldContexts"
                :host-mode="versioningHostMode"
                @close="closeGitChangesPanel"
                @expand-context="expandGitChangesContext"
                @reset-context="resetGitChangesContext"
              />
            </div>
          </div>
        </transition>
      </div>
      <transition name="desktop-approval-fade">
        <div v-if="!isMobileViewport && !rightCollapsed" class="desktop-approval-overlay">
          <div
            class="desktop-approval-sheet"
            :style="{ width: Math.min(rightWidth || 420, 520) + 'px' }"
          >
            <ToolApprovalPanel
              :collapsed="false"
              :width="Math.min(rightWidth || 420, 520)"
              :approvals="pendingToolApprovals"
              :deciding-approval-ids="decidingApprovalIds"
              :auto-approval-feed-lines="autoApprovalFeedLines"
              :auto-approval-final-message="autoApprovalFinalMessage"
              :auto-approval-title="autoApprovalTitle"
              @switch-unrestricted="handleSwitchPermissionToUnrestricted"
              @approve="approveToolApproval"
              @reject="rejectToolApproval"
              @close="rightCollapsed = true"
            />
          </div>
        </div>
      </transition>

      <PersonalizationDrawer />
      <PathAuthorizationDialog
        :open="pathAuthorizationDialogOpen"
        :value="pathAuthorizationDraft"
        :mode="pathAuthorizationMode"
        :saving="pathAuthorizationSaving"
        @update:value="(v) => (pathAuthorizationDraft = v)"
        @update:mode="setPathAuthorizationMode"
        @close="closePathAuthorizationDialog"
        @save="savePathAuthorization"
      />
      <transition name="overlay-fade">
        <ImagePicker
          v-if="imagePickerOpen"
          :open="imagePickerOpen"
          :entries="imageEntries"
          :initial-selected="selectedImages"
          :loading="imageLoading"
          :uploading="mediaUploading"
          @close="closeImagePicker"
          @confirm="handleImagesConfirmed"
          @local-files="handleLocalImageFiles"
        />
      </transition>
      <transition name="overlay-fade">
        <VideoPicker
          v-if="videoPickerOpen"
          :open="videoPickerOpen"
          :entries="videoEntries"
          :initial-selected="selectedVideos"
          :loading="videoLoading"
          :uploading="mediaUploading"
          @close="closeVideoPicker"
          @confirm="handleVideosConfirmed"
          @local-files="handleLocalVideoFiles"
        />
      </transition>
      <ImageLightbox />
      <transition name="overlay-fade">
        <ConversationReviewDialog
          v-if="reviewDialogOpen"
          :open="reviewDialogOpen"
          :conversations="reviewConversations"
          :current-conversation-id="currentConversationId"
          :selected-id="reviewSelectedConversationId"
          :loading="reviewListLoading"
          :loading-more="reviewListLoadingMore"
          :has-more="reviewListHasMore"
          :submitting="reviewSubmitting"
          :preview="reviewPreviewLines"
          :preview-loading="reviewPreviewLoading"
          :preview-error="reviewPreviewError"
          :preview-limit="reviewPreviewLimit"
          :send-to-model="reviewSendToModel"
          :content-mode="reviewContentMode"
          :generated-path="reviewGeneratedPath"
          :icon-style="iconStyle"
          @close="reviewDialogOpen = false"
          @select="handleReviewSelect"
          @load-more="loadMoreReviewConversations"
          @toggle-send="reviewSendToModel = $event"
          @toggle-content-mode="toggleReviewContentMode"
          @confirm="handleConfirmReview"
        />
      </transition>
      <SubAgentActivityDialog />
      <BackgroundCommandDialog />
      <GoalProgressDialog
        :open="goalDialogOpen"
        :progress="goalProgress"
        @close="goalDialogOpen = false"
      />
      <UserQuestionDialog
        :visible="userQuestionDialogVisible && !userQuestionMinimized && pendingUserQuestions.length > 0"
        :questions="pendingUserQuestions"
        :active-index="userQuestionActiveIndex"
        :submitting-ids="answeringUserQuestionIds"
        @minimize="minimizeUserQuestionDialog"
        @update:active-index="userQuestionActiveIndex = $event"
        @submit="submitUserQuestionAnswers"
        @dismiss="dismissUserQuestions"
      />
      <PlanApprovalDialog
        :visible="pendingPlanApprovals.length > 0 && !planApprovalMinimized"
        :approvals="pendingPlanApprovals"
        :submitting-ids="answeringPlanApprovalIds"
        @submit="submitPlanApproval"
        @minimize="minimizePlanApprovalDialog"
      />
      <SandboxSetupDialog />
      <transition name="overlay-fade">
        <VersioningDialog
          v-if="versioningDialogOpen"
          :host-mode="versioningHostMode"
          :enabled="versioningEnabled"
          :tracking-mode="versioningTrackingMode"
          :loading="versioningLoading"
          :items="versioningCheckpoints"
          :selected-seq="versioningSelectedSeq"
          :detail="versioningSelectedDetail"
          :detail-loading="versioningDetailLoading"
          :restoring="versioningRestoring"
          :restore-mode="versioningRestoreMode"
          :icon-style="iconStyle"
          @close="versioningDialogOpen = false"
          @refresh="refreshVersioningDialog"
          @toggle-enabled="toggleConversationVersioning"
          @select="selectVersioningCheckpoint"
          @update:restore-mode="versioningRestoreMode = $event"
          @update:tracking-mode="versioningTrackingMode = $event"
          @confirm="confirmVersioningRestore"
        />
      </transition>
      <TutorialOverlay v-if="tutorialStore.running" />
      <NewUserTutorialPrompt
        :visible="tutorialPromptVisible"
        :username="tutorialPromptUsername"
        :loading="tutorialPromptLoading"
        @start="handleNewUserTutorialStart"
        @skip="handleNewUserTutorialSkip"
      />

      <div
        v-if="isMobileViewport"
        class="mobile-panel-trigger"
        :class="{ 'is-hidden': Boolean(activeMobileOverlay) }"
        ref="mobilePanelTrigger"
      >
        <div class="mobile-panel-topbar">
          <div class="mobile-panel-topbar__blur" aria-hidden="true"></div>
          <button
            type="button"
            class="mobile-panel-fab"
            :aria-label="$t('appCore.conversationHistory')"
            @click="openMobileOverlay('conversation')"
          >
            <img :src="mobilePanelIcon" alt="" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="mobile-topbar-selector"
            data-tutorial="header-model-selector-mobile"
            :class="{ open: headerMenuOpen }"
            @click.stop="toggleHeaderMenu"
            :disabled="!isConnected"
          >
            <span class="mobile-selector-label">
              <span class="mobile-selector-model">{{ currentModelLabel }}</span>
              <span class="mobile-selector-sep">·</span>
              <span class="mobile-selector-mode">{{ headerRunModeLabel }}</span>
            </span>
          </button>
          <div
            class="mobile-topbar-title"
            :title="currentConversationTitle || $t('overlay.unnamedConversation')"
          >
            {{ currentConversationTitle || $t('overlay.unnamedConversation') }}
          </div>
          <button
            type="button"
            class="mobile-panel-fab mobile-topbar-quickdock"
            :aria-label="$t('appCore.expandQuickDock')"
            :aria-pressed="activeMobileOverlay === 'quickdock'"
            @click="openMobileOverlay('quickdock')"
          >
            <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true">
              <line x1="2" y1="3.5" x2="16" y2="3.5" />
              <line x1="2" y1="7.5" x2="10" y2="7.5" />
              <line x1="2" y1="11.5" x2="14" y2="11.5" />
              <line x1="2" y1="15.5" x2="7" y2="15.5" />
            </svg>
          </button>
        </div>
        <transition name="header-menu">
          <div
            v-if="headerMenuOpen && isMobileViewport"
            class="model-mode-dropdown model-mode-dropdown--mobile"
            ref="headerMenu"
          >
            <div class="dropdown-column" data-tutorial="header-model-options">
              <div class="dropdown-title">
                <button
                  v-if="headerModelMenuPage === 'codex'"
                  type="button"
                  class="dropdown-title-back"
                  @click.stop="headerModelMenuPage = 'main'"
                >
                  ‹ {{ $t('appCore.back') }}
                </button>
                <template v-else>{{ $t('appCore.model') }}</template>
              </div>
              <div class="model-menu-panes">
                <Transition
                  :name="headerModelMenuPage === 'codex' ? 'type-slide-left' : 'type-slide-right'"
                  @after-leave="clearModelMenuPaneHeight"
                >
                  <div
                    v-if="headerModelMenuPage === 'main'"
                    key="main"
                    class="dropdown-list dropdown-list--models model-menu-pane"
                  >
                    <button
                      v-for="option in regularModelOptions"
                      :key="option.key"
                      type="button"
                      class="dropdown-item"
                      :class="{ active: option.key === currentModelKey, disabled: option.disabled }"
                      @click.stop="handleHeaderModelSelect(option.key, option.disabled)"
                      :disabled="streamingMessage || !isConnected || option.disabled"
                    >
                      <div class="item-label">{{ option.label }}</div>
                      <span v-if="option.key === currentModelKey" class="item-check">✓</span>
                    </button>
                    <button
                      type="button"
                      class="dropdown-item dropdown-item--codex-entry"
                      @click.stop="headerModelMenuPage = 'codex'"
                    >
                      <div class="item-label">{{ $t('appCore.codexEntry') }}</div>
                      <span class="item-chevron">›</span>
                    </button>
                  </div>
                  <div
                    v-else
                    key="codex"
                    class="dropdown-list dropdown-list--models model-menu-pane"
                  >
                    <button
                      v-for="option in codexModelOptions"
                      :key="option.key"
                      type="button"
                      class="dropdown-item"
                      :class="{ active: option.key === currentModelKey, disabled: option.disabled }"
                      @click.stop="handleHeaderModelSelect(option.key, option.disabled)"
                      :disabled="streamingMessage || !isConnected || option.disabled"
                    >
                      <div class="item-label">{{ option.label }}</div>
                      <span v-if="option.key === currentModelKey" class="item-check">✓</span>
                    </button>
                    <div v-if="!codexModelOptions.length" class="dropdown-empty-hint">
                      {{ $t('appCore.codexNotConnected') }}
                    </div>
                  </div>
                </Transition>
              </div>
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
                <span v-if="option.value === resolvedRunMode" class="item-check">✓</span>
              </button>
              <div v-if="showEffortSlider" class="dropdown-effort">
                <EffortSlider
                  :model-value="reasoningEffort"
                  @update:model-value="setReasoningEffort"
                />
              </div>
            </div>
          </div>
        </transition>
      </div>

      <transition name="mobile-panel-overlay">
        <div
          v-if="isMobileViewport && activeMobileOverlay === 'conversation'"
          class="mobile-panel-overlay mobile-panel-overlay--left"
          @click.self="closeMobileOverlay"
        >
          <div class="mobile-panel-sheet mobile-panel-sheet--conversation">
            <ConversationSidebar
              class="mobile-overlay-content"
              :icon-style="iconStyle"
              :format-time="formatTime"
              :running-tasks="runningWorkspaceTasks"
              :current-task-in-progress="taskInProgress"
              :current-conversation-id="currentConversationId"
              :current-workspace-id="currentHostWorkspaceId"
              :default-workspace-id="defaultHostWorkspaceId"
              :workspaces="hostWorkspaces"
              :workspace-kind="versioningHostMode ? 'workspace' : 'project'"
              :host-workspace-enabled="versioningHostMode || dockerProjectMode"
              :workspace-busy="hostWorkspaceSwitching || hostWorkspaceManageSubmitting"
              :workspace-create-submitting="hostWorkspaceCreateSubmitting"
              :workspace-error="hostWorkspaceCreateError"
              :group-by-workspace="personalizationStore?.form?.group_sidebar_by_workspace"
              :versioning-host-mode="versioningHostMode"
              :show-collapse-button="true"
              :show-refresh-button="isAppShell"
              collapse-button-variant="close"
              @toggle="closeMobileOverlay"
              @refresh="refreshCurrentPage"
              @create="createNewConversation"
              @search="handleSidebarSearchInput"
              @search-submit="handleSidebarSearchSubmit"
              @search-more="loadMoreSearchResults"
              @select="handleMobileOverlaySelect($event)"
              @select-running-task="openRunningWorkspaceTask"
              @load-more="loadMoreConversations"
              @personal="openPersonalPage"
              @delete="deleteConversation"
              @duplicate="duplicateConversation"
              @switch-workspace="handleHostWorkspaceSwitch"
              @create-workspace="submitHostWorkspaceCreateFromManage"
              @delete-workspace="handleDeleteWorkspaceFromSwitcher"
              @set-default-workspace="setDefaultHostWorkspace"
              @select-workspace-conversation="handleSelectWorkspaceConversation"
              @create-workspace-conversation="createWorkspaceConversation"
              @reveal-workspace="revealHostWorkspace"
              @rename-workspace="handleRenameWorkspaceFromSidebar"
              @pin-workspace="handlePinWorkspaceFromSidebar"
              @conversation-type-change="handleSidebarConversationTypeChange"
            />
          </div>
        </div>
      </transition>

      <transition name="mobile-panel-overlay">
        <div
          v-if="isMobileViewport && activeMobileOverlay === 'approval'"
          class="mobile-panel-overlay mobile-panel-overlay--right mobile-approval-overlay"
          @click.self="closeMobileOverlay('backdrop-click')"
        >
          <div class="mobile-panel-sheet mobile-panel-sheet--approval">
            <ToolApprovalPanel
              class="mobile-overlay-content"
              :collapsed="false"
              :width="Math.min(rightWidth || 420, 460)"
              :approvals="pendingToolApprovals"
              :deciding-approval-ids="decidingApprovalIds"
              :auto-approval-feed-lines="autoApprovalFeedLines"
              :auto-approval-final-message="autoApprovalFinalMessage"
              @switch-unrestricted="handleSwitchPermissionToUnrestricted"
              @approve="approveToolApproval"
              @reject="rejectToolApproval"
              @close="closeMobileOverlay('approval-panel-close-btn')"
            />
          </div>
        </div>
      </transition>

      <!-- 快捷窗口悬浮层：QuickDock 常驻挂载（v-show 控显隐），轮询与状态与桌面端一致；
           关闭悬浮层时由 closeMobileOverlay 负责清掉瞬态面板（详情/预览/菜单） -->
      <transition name="mobile-panel-overlay">
        <div
          v-if="isMobileViewport"
          v-show="activeMobileOverlay === 'quickdock'"
          class="mobile-panel-overlay mobile-panel-overlay--right mobile-quickdock-overlay"
          @click.self="closeMobileOverlay('backdrop-click')"
        >
          <div class="mobile-panel-sheet mobile-panel-sheet--quickdock">
            <!-- 顶部关闭按钮：与桌面端 qd-toggle 同款图标与位置（quickdock.css），
                 点击关闭悬浮层；点遮罩空白处同样关闭 -->
            <button
              type="button"
              class="qd-toggle mobile-quickdock-close"
              :aria-label="$t('appCore.collapseQuickDock')"
              @click="closeMobileOverlay('quickdock-close-btn')"
            >
              <svg viewBox="0 0 18 18" width="16" height="16" aria-hidden="true">
                <line x1="2" y1="3.5" x2="16" y2="3.5" />
                <line x1="2" y1="7.5" x2="10" y2="7.5" />
                <line x1="2" y1="11.5" x2="14" y2="11.5" />
                <line x1="2" y1="15.5" x2="7" y2="15.5" />
              </svg>
            </button>
            <QuickDock :host-mode="versioningHostMode" />
            <div v-if="!qdHasContent" class="mobile-quickdock-empty">
              {{ $t('quickdock.emptyHint') }}
            </div>
          </div>
        </div>
      </transition>

      <!-- 工作流编辑器 demo：/workflows 与 /workflow/<name> 全屏覆盖层 -->
      <WorkflowDemoShell v-if="workflowDemoRoute" :route-path="workflowDemoRoute" />
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref } from 'vue';
import appOptions from './app';
import VideoPicker from './components/overlay/VideoPicker.vue';
import ImageLightbox from './components/overlay/ImageLightbox.vue';
import QuickDock from './components/chat/quickdock/QuickDock.vue';
import FilePreviewPanel from './components/chat/quickdock/FilePreviewPanel.vue';
import CitationPopover from './components/chat/CitationPopover.vue';
import { useTutorialStore } from './stores/tutorial';
import { usePersonalizationStore } from './stores/personalization';

const VirtualMonitorSurface = defineAsyncComponent(
  () => import('./components/chat/VirtualMonitorSurface.vue')
);
const PathAuthorizationDialog = defineAsyncComponent(
  () => import('./components/overlay/PathAuthorizationDialog.vue')
);

const tutorialStore = useTutorialStore();
const personalizationStore = usePersonalizationStore();



const mobilePanelIcon = new URL('../icons/align-left.svg', import.meta.url).href;
const detectAppShell = () => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  const ua = window.navigator?.userAgent || '';
  return (
    params.has('app_shell') ||
    Boolean((window as any)?.AndroidThemeBridge) ||
    /;\s*wv\)/i.test(ua)
  );
};
const isAppShell = ref(detectAppShell());
onMounted(() => {
  isAppShell.value = detectAppShell();
  window.setTimeout(() => {
    isAppShell.value = detectAppShell();
  }, 300);

});

defineOptions(appOptions);
</script>

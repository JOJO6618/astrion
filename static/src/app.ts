// @ts-nocheck
// static/app-enhanced.js - 修复版，正确实现Token实时更新
import { mapActions } from 'pinia';
import { useUiStore } from './stores/ui';
import { useChatStore } from './stores/chat';
import { useInputStore } from './stores/input';
import { useToolStore } from './stores/tool';
import { useResourceStore } from './stores/resource';
import { useUploadStore } from './stores/upload';
import { useFileStore } from './stores/file';
import { useSubAgentStore } from './stores/subAgent';
import { useBackgroundCommandStore } from './stores/backgroundCommand';
import { useFocusStore } from './stores/focus';
import { usePersonalizationStore } from './stores/personalization';
import { useModelStore } from './stores/model';
import { useMonitorStore } from './stores/monitor';
import { appComponents } from './app/components';
import { dataState } from './app/state';
import { computed } from './app/computed';
import { watchers } from './app/watchers';
import { created, mounted, beforeUnmount } from './app/lifecycle';
import { conversationMethods } from './app/methods/conversation';
import { historyMethods } from './app/methods/history';
import { messageMethods } from './app/methods/message';
import { searchMethods } from './app/methods/search';
import { uploadMethods } from './app/methods/upload';
import { resourceMethods } from './app/methods/resources';
import { toolingMethods } from './app/methods/tooling';
import { uiMethods } from './app/methods/ui';
import { taskPollingMethods } from './app/methods/taskPolling';
import { monitorMethods } from './app/methods/monitor';
import { versioningMethods } from './app/methods/versioning';

// 其他初始化逻辑已迁移到 app/bootstrap.ts

const appOptions = {
  data: dataState,
  created,
  mounted,
  beforeUnmount,
  computed,
  watch: watchers,

  methods: {
    ...conversationMethods,
    ...historyMethods,
    ...searchMethods,
    ...messageMethods,
    ...uploadMethods,
    ...resourceMethods,
    ...toolingMethods,
    ...uiMethods,
    ...versioningMethods,
    ...monitorMethods,
    ...taskPollingMethods,
    ...mapActions(useUiStore, {
      uiToggleSidebar: 'toggleSidebar',
      uiSetSidebarCollapsed: 'setSidebarCollapsed',
      uiSetChatDisplayMode: 'setChatDisplayMode',
      uiSetMobileViewport: 'setIsMobileViewport',
      uiSetMobileOverlayMenuOpen: 'setMobileOverlayMenuOpen',
      uiToggleMobileOverlayMenu: 'toggleMobileOverlayMenu',
      uiSetActiveMobileOverlay: 'setActiveMobileOverlay',
      uiCloseMobileOverlay: 'closeMobileOverlay',
      uiPushToast: 'pushToast',
      uiUpdateToast: 'updateToast',
      uiDismissToast: 'dismissToast',
      uiShowQuotaToastMessage: 'showQuotaToastMessage',
      uiDismissQuotaToast: 'dismissQuotaToast',
      uiRequestConfirm: 'requestConfirm',
      uiResolveConfirm: 'resolveConfirm'
    }),
    ...mapActions(useModelStore, {
      modelSet: 'setModel'
    }),
    ...mapActions(useChatStore, {
      chatExpandBlock: 'expandBlock',
      chatCollapseBlock: 'collapseBlock',
      chatClearExpandedBlocks: 'clearExpandedBlocks',
      chatSetThinkingLock: 'setThinkingLock',
      chatClearThinkingLocks: 'clearThinkingLocks',
      chatSetScrollState: 'setScrollState',
      chatEnableAutoScroll: 'enableAutoScroll',
      chatDisableAutoScroll: 'disableAutoScroll',
      chatToggleScrollLockState: 'toggleScrollLockState',
      chatAddUserMessage: 'addUserMessage',
      chatStartAssistantMessage: 'startAssistantMessage',
      chatStartThinkingAction: 'startThinkingAction',
      chatAppendThinkingChunk: 'appendThinkingChunk',
      chatCompleteThinkingAction: 'completeThinking',
      chatStartTextAction: 'startTextAction',
      chatAppendTextChunk: 'appendTextChunk',
      chatCompleteTextAction: 'completeText',
      chatAddSystemMessage: 'addSystemMessage',
      chatEnsureAssistantMessage: 'ensureAssistantMessage',
      chatClearStreamingResidualState: 'clearStreamingResidualState',
      chatResetStreamingAttemptActions: 'resetStreamingAttemptActions'
    }),
    ...mapActions(useInputStore, {
      inputSetFocused: 'setInputFocused',
      inputToggleQuickMenu: 'toggleQuickMenu',
      inputCloseMenus: 'closeMenus',
      inputOpenQuickMenu: 'openQuickMenu',
      inputSetQuickMenuOpen: 'setQuickMenuOpen',
      inputToggleToolMenu: 'toggleToolMenu',
      inputSetToolMenuOpen: 'setToolMenuOpen',
      inputToggleSettingsMenu: 'toggleSettingsMenu',
      inputSetSettingsOpen: 'setSettingsOpen',
      inputSetMessage: 'setInputMessage',
      inputClearMessage: 'clearInputMessage',
      inputSetLineCount: 'setInputLineCount',
      inputSetMultiline: 'setInputMultiline',
      inputSetImagePickerOpen: 'setImagePickerOpen',
      inputSetSelectedImages: 'setSelectedImages',
      inputAddSelectedImage: 'addSelectedImage',
      inputClearSelectedImages: 'clearSelectedImages',
      inputRemoveSelectedImage: 'removeSelectedImage',
      inputSetVideoPickerOpen: 'setVideoPickerOpen',
      inputSetSelectedVideos: 'setSelectedVideos',
      inputAddSelectedVideo: 'addSelectedVideo',
      inputClearSelectedVideos: 'clearSelectedVideos',
      inputRemoveSelectedVideo: 'removeSelectedVideo',
      inputAddSelectedFile: 'addSelectedFile',
      inputRemoveSelectedFile: 'removeSelectedFile',
      inputClearSelectedFiles: 'clearSelectedFiles',
      inputToggleGoalArmed: 'toggleGoalArmed',
      inputSetGoalArmed: 'setGoalArmed',
      inputSetGoalRunning: 'setGoalRunning',
      inputSetGoalProgress: 'setGoalProgress',
      inputSetGoalDialogOpen: 'setGoalDialogOpen'
    }),
    ...mapActions(useToolStore, {
      toolRegisterAction: 'registerToolAction',
      toolUnregisterAction: 'unregisterToolAction',
      toolFindAction: 'findToolAction',
      toolTrackAction: 'trackToolAction',
      toolReleaseAction: 'releaseToolAction',
      toolGetLatestAction: 'getLatestActiveToolAction',
      toolResetTracking: 'resetToolTracking',
      toolSetSettings: 'setToolSettings',
      toolSetSettingsLoading: 'setToolSettingsLoading'
    }),
    ...mapActions(useResourceStore, {
      resourceUpdateCurrentContextTokens: 'updateCurrentContextTokens',
      resourceFetchConversationTokenStatistics: 'fetchConversationTokenStatistics',
      resourceSetCurrentContextTokens: 'setCurrentContextTokens',
      resourceToggleTokenPanel: 'toggleTokenPanel',
      resourceApplyStatusSnapshot: 'applyStatusSnapshot',
      resourceUpdateContainerStatus: 'updateContainerStatus',
      resourceStartContainerStatsPolling: 'startContainerStatsPolling',
      resourceStopContainerStatsPolling: 'stopContainerStatsPolling',
      resourceStartProjectStoragePolling: 'startProjectStoragePolling',
      resourceStopProjectStoragePolling: 'stopProjectStoragePolling',
      resourceStartUsageQuotaPolling: 'startUsageQuotaPolling',
      resourceStopUsageQuotaPolling: 'stopUsageQuotaPolling',
      resourcePollContainerStats: 'pollContainerStats',
      resourceBindContainerVisibilityWatcher: 'bindContainerVisibilityWatcher',
      resourcePollProjectStorage: 'pollProjectStorage',
      resourceFetchUsageQuota: 'fetchUsageQuota',
      resourceResetTokenStatistics: 'resetTokenStatistics',
      resourceSetUsageQuota: 'setUsageQuota'
    }),
    ...mapActions(useUploadStore, {
      uploadHandleSelected: 'handleSelectedFiles',
      uploadBatchFiles: 'uploadFiles'
    }),
    ...mapActions(useFileStore, {
      fileFetchTree: 'fetchFileTree',
      fileSetTreeFromResponse: 'setFileTreeFromResponse',
      fileFetchTodoList: 'fetchTodoList',
      fileSetTodoList: 'setTodoList',
      fileHideContextMenu: 'hideContextMenu',
      fileMarkTreeUnavailable: 'markFileTreeUnavailable'
    }),
    ...mapActions(useMonitorStore, {
      monitorSyncDesktop: 'syncDesktopFromTree',
      monitorResetVisual: 'resetVisualState',
      monitorResetSpeech: 'resetSpeechBuffer',
      monitorShowSpeech: 'enqueueModelSpeech',
      monitorShowThinking: 'enqueueModelThinking',
      monitorEndModelOutput: 'endModelOutput',
      monitorShowPendingReply: 'showPendingReply',
      monitorPreviewTool: 'previewToolIntent',
      monitorQueueTool: 'enqueueToolEvent',
      monitorResolveTool: 'resolveToolResult',
      forceUnlockMonitor: 'resetVisualState'
    }),
    ...mapActions(useSubAgentStore, {
      subAgentFetch: 'fetchSubAgents'
    }),
    ...mapActions(useBackgroundCommandStore, {
      backgroundCommandFetch: 'fetchCommands'
    }),
    ...mapActions(useFocusStore, {
      focusFetchFiles: 'fetchFocusedFiles',
      focusSetFiles: 'setFocusedFiles'
    }),
    ...mapActions(usePersonalizationStore, {
      personalizationOpenDrawer: 'openDrawer',
      personalizationFetch: 'fetchPersonalization'
    })
  }
};

(appOptions as any).components = appComponents;

export default appOptions;

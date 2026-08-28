// Locale namespace: appUi (en-US mirror, keys must match zh-CN/appUi.ts exactly).
// Used by: static/src/app/methods/ui/*, static/src/app/methods/upload/* (UI method-layer toast/confirm/Error text)
export default {
  // ── Generic candidate common words ──
  unknown: 'Unknown', // TODO(common): candidate common word
  saveFailed: 'Failed to save', // TODO(common): candidate common word
  notice: 'Notice', // TODO(common): candidate common word
  pleaseRetry: 'Please retry', // TODO(common): candidate common word
  appliedImmediately: 'Applied immediately', // TODO(common): candidate common word
  switchFailed: 'Failed to switch', // TODO(common): candidate common word
  createFailed: 'Failed to create', // TODO(common): candidate common word
  deleteFailed: 'Failed to delete', // TODO(common): candidate common word
  renameFailed: 'Failed to rename', // TODO(common): candidate common word
  openFailed: 'Failed to open', // TODO(common): candidate common word
  disabledByAdmin: 'Disabled by the administrator', // TODO(common): candidate common word
  forceDisabledByAdmin: 'Force-disabled by the administrator', // TODO(common): candidate common word

  // ── Host / project workspaces (hostWorkspace.ts) ──
  unnamedProject: 'Untitled project',
  fetchProjectsFailed: 'Failed to load project list',
  pathCannotBeEmpty: 'Path cannot be empty',
  projectNameCannotBeEmpty: 'Project name cannot be empty',
  workspaceNameCannotBeEmpty: 'Workspace name cannot be empty',
  workspaceSwitched: 'Workspace switched',
  projectSwitched: 'Project switched',
  switchHostWorkspaceFailed: 'Failed to switch host workspace',
  switchWorkspaceFailed: 'Failed to switch workspace',
  switchProjectFailed: 'Failed to switch project',
  workspaceCreated: 'Workspace created',
  projectCreated: 'Project created',
  createWorkspaceFailed: 'Failed to create workspace',
  createProjectFailed: 'Failed to create project',
  workspaceRenamed: 'Workspace renamed',
  projectRenamed: 'Project renamed',
  renameWorkspaceFailed: 'Failed to rename workspace',
  renameProjectFailed: 'Failed to rename project',
  deleteWorkspaceConfirmTitle: 'Delete workspace',
  deleteProjectConfirmTitle: 'Delete project',
  deleteWorkspaceConfirmMessage:
    'Remove "{name}" from the list? The workspace folder on disk will not be deleted.',
  deleteProjectConfirmMessage:
    'Delete project "{name}"? Its folder and conversation history will be deleted along with it.',
  workspaceDeleted: 'Workspace deleted',
  projectDeleted: 'Project deleted',
  deleteWorkspaceFailed: 'Failed to delete workspace',
  deleteProjectFailed: 'Failed to delete project',
  openWorkspaceFailed: 'Failed to open workspace',
  openProjectFailed: 'Failed to open project',
  setAsDefaultWorkspace: 'Set as default workspace',
  setAsDefaultProject: 'Set as default project',
  setDefaultWorkspaceFailed: 'Failed to set default workspace',
  setDefaultProjectFailed: 'Failed to set default project',

  // ── Plan approval / user questions (dialog.ts) ──
  policyBlockedPersonalSpace: 'Personal space has been disabled by the administrator',
  policyBlockedReview: 'Conversation references have been disabled by the administrator',
  submitPlanDecisionFailed: 'Failed to submit plan decision',
  planApproved: 'Plan approved',
  planApprovedMessage: 'Switched to execute mode and started implementation',
  planRejected: 'Plan rejected',
  planRejectedMessage: 'The AI will revise it based on your feedback and resubmit',
  unavailable: 'Unavailable',
  unavailableNoConnectionMessage: 'Not connected, cannot generate a review file',
  cannotReferenceCurrentConversation: 'Cannot reference the current conversation',
  chooseOtherConversationForReview: 'Choose another conversation to generate the review',
  submitAnswerFailed: 'Failed to submit answer',
  submitApprovalFailed: 'Failed to submit decision',
  approvalFailed: 'Approval failed',

  // ── Model switching (model.ts) ──
  modelDisabled: 'Model disabled',
  conversationHasImagesMessage:
    'This conversation contains images, but the target model does not support image input',
  conversationHasVideosMessage:
    'This conversation contains videos, but the target model does not support video input',
  modelSwitched: 'Model switched',
  switchModelFailed: 'Failed to switch model',

  // ── Run modes / sub-agents (mode.ts) ──
  modeUnavailable: 'Mode unavailable',
  fastOnlyModeMessage: 'The current model only supports fast mode',
  thinkingOnlyModeMessage: 'The current model only supports thinking mode',
  switchThinkingModeFailed: 'Failed to switch thinking mode',
  settingFailed: 'Failed to set',
  setReasoningEffortFailed: 'Failed to set reasoning effort',
  pauseAllSubAgentsTitle: 'Pause all sub-agents?',
  terminateAllSubAgentsTitle: 'Terminate all sub-agents?',
  pauseAllSubAgentsMessage: 'All running sub-agents will stop and become idle. Cancel to do nothing.',
  terminateAllSubAgentsMessage:
    'All background sub-agents will be forcefully terminated. Cancel to do nothing.',
  pause: 'Pause',
  terminate: 'Terminate',
  stopSubAgentsFailed: 'Failed to stop sub-agents',
  subAgentsPaused: 'Sub-agents paused',
  subAgentsTerminated: 'Sub-agents terminated',
  stoppedSubAgentCount: 'Processed {n} sub-agents',

  // ── Panels / permission / execution environment / network permission (panel.ts / permission.ts) ──
  policyBlockedFocusPanel: 'The focus panel has been disabled by the administrator',
  policyBlockedTokenPanel: 'Usage statistics have been disabled by the administrator',
  switchPermissionFailed: 'Failed to switch permission',
  permissionUpdated: 'Permission updated',
  switchExecutionModeFailed: 'Failed to switch execution environment',
  executionModeUpdated: 'Execution environment updated',
  switchNetworkPermissionFailed: 'Failed to switch network permission',
  networkPermissionUpdated: 'Network permission updated',
  networkRestricted: 'Restricted',
  networkFull: 'Fully open',
  switchedToMode: 'Switched to {mode}',
  pathAuthorizationSaved: 'Path authorization saved',
  pathAuthApplyMessage: 'Applies to command tools immediately; restart terminal sessions to apply',
  savePathAuthorizationFailed: 'Failed to save path authorization',

  // ── Review generation / conversation compression (review.ts) ──
  conversationAutoCompressing: 'Auto-compressing conversation',
  compressingNowPleaseWait: 'The conversation is being compressed, please try again later',
  policyBlockedCompress: 'Conversation compression has been disabled by the administrator',
  selectConversation: 'Select a conversation',
  selectConversationForReview: 'Select the conversation record to generate the review from',
  cannotSend: 'Cannot send',
  noActiveConversationMessage: 'No active conversation, cannot auto-send the prompt message',
  reviewPathMissing: 'Review file path was not returned',
  reviewSuggestReadFull: 'I suggest reading it in full.',
  reviewSuggestReadBySearch: 'I suggest using the read tool to search or read in sections.',
  reviewAutoMessage:
    'Continue this task for me. The conversation file is at {path} and is {count} characters long. {suggestion} Read the file to understand it first, then do not continue working -- report your understanding to me and wait for my instructions.',
  reviewFileGenerated: 'Review file generated',
  generateFailed: 'Failed to generate',
  generateReviewFailed: 'Failed to generate review',
  fetchPreviewFailed: 'Failed to load preview',

  // ── Connection / upload (drag.ts / paste.ts / socket.ts) ──
  notConnected: 'Not connected',
  waitForConnectionBeforeUpload: 'Wait for the server connection before uploading',
  uploadDisabled: 'Upload disabled',
  uploadDisabledByAdmin: 'Uploading has been disabled by the administrator',
  modelDoesNotSupportImage: 'The current model does not support images',
  chooseImageModelMessage: 'Choose a model that supports image input before sending images',
  modelDoesNotSupportVideo: 'The current model does not support videos',
  switchToVideoModelMessage: 'Switch to a model that supports video input before sending videos',
  workspaceBootstrapTitle: 'No workspaces yet',
  workspaceBootstrapMessage: 'Click the "Workspace" button in the sidebar to create your first workspace',
  statusApiRequestFailed: 'Status API request failed: {status}',
  hostModeFileTreeUnavailable: 'The file tree is unavailable in host mode',
  dockerModeFilesChanged: 'In Docker mode the files area now shows the project list',

  // ── Upload (entries.ts / picker.ts / process.ts / quick.ts) ──
  noImagesFound: 'No images found',
  noImagesInWorkspace: 'No image files available in the workspace',
  loadImagesFailed: 'Failed to load images',
  noVideosFound: 'No videos found',
  noVideosInWorkspace: 'No video files available in the workspace',
  loadVideosFailed: 'Failed to load videos',
  uploadingTitle: 'Uploading',
  waitImageUploadDone: 'Wait for the current image upload to finish',
  waitFileUploadDone: 'Wait for the current file upload to finish',
  noFileReceived: 'No file received',
  noValidFileContent: 'The system did not return valid file content. Please try again',
  limitReachedTitle: 'Limit reached',
  maxImagesMessage: 'You can select up to {max} images',
  maxFilesMessage: 'You can attach up to {max} files',
  ignoredTitle: 'Ignored',
  skippedNonImageFiles: 'Non-image files were skipped',
  exceededTitle: 'Quantity exceeded',
  truncatedImagesMessage: 'You can add up to {n} more images; the rest were truncated',
  truncatedFilesMessage: 'You can attach up to {n} more files; the rest were truncated',
  waitVideoUploadDone: 'Wait for the current video upload to finish',
  skippedNonVideoFiles: 'Non-video files were skipped',
  tooManyVideosTitle: 'Too many videos',
  onlyOneVideoMessage: 'Only one video can be selected at a time; the first one was used',

  // ── System / tutorial / terminal / work mode (system.ts / tutorial.ts / terminal.ts / workMode.ts) ──
  blankWelcomeDefault: 'How can I help you?',
  updateTutorialStatusFailed: 'Failed to update tutorial status',
  tutorialSaveFailedMessage: 'Failed to save tutorial status, please try again later',
  policyBlockedTerminal: 'The realtime terminal has been disabled by the administrator',
  workModeRunningMessage:
    'Conversation is running; wait for the current task to finish before switching run modes',
  switchRunModeFailed: 'Failed to switch run mode',
  // TODO(common): run-mode level words (plan/ask/execute) candidate common words
  workModePlan: 'Plan',
  workModeAsk: 'Ask',
  workModeExecute: 'Execute',
  runModeUpdated: 'Run mode updated',

  // ── Git / running tasks / input draft (git.ts / workspace.ts / composer.ts) ──
  loadGitChangesFailed: 'Failed to load Git changes',
  fetchRunningTasksFailed: 'Failed to load running tasks',
  saveInputDraftFailed: 'Failed to save input draft',
  fetchInputDraftFailed: 'Failed to load input draft',

  // ── Sub-agent / background command done labels (shared.ts) ──
  subAgentTaskDone: 'Sub-agent {agentId} finished',
  backgroundRunCommandDone: 'Background run_command finished',
} as const;
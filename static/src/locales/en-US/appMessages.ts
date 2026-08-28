// Locale namespace: appMessages (en-US mirror, keys must match zh-CN/appMessages.ts exactly).
// 规范见 doc/frontend/i18n_spec.md。
// Used by: static/src/app/methods/message/{chat,runtimeQueue,send,systemCommand}.ts
//          static/src/app/methods/conversation/{action,load,state}.ts
//          static/src/app/methods/history.ts、static/src/app/methods/versioning.ts
export default {
  // ── Messages: clear/compress conversation (message/chat.ts) ──
  clearChatTitle: 'Clear chat',
  clearChatConfirmMessage: 'Are you sure you want to clear all conversation records? This cannot be undone.',
  clearChatConfirmText: 'Clear',
  cannotCompressTitle: 'Cannot compress',
  cannotCompressMessage: 'No conversation to compress.',
  compressingTitle: 'Compressing',
  compressingMessage: 'Compressing the conversation, please wait...',
  compressionCompletedTitle: 'Compression complete',
  compressionCompletedMessage: 'Earlier conversation content has been compressed',
  compressionFailed: 'Compression failed',
  compressionErrorTitle: 'Compression error',
  // TODO(common): candidate common word
  sendFailedTitle: 'Send failed',
  createTaskFailedMessage: 'Failed to create the task, please try again',
  createNewConversationFailedMessage: 'Failed to create a new conversation, please try again',

  // ── Messages: runtime queue (message/runtimeQueue.ts) ──
  cannotQueueTitle: 'Cannot queue',
  noActiveTaskMessage: 'No active task detected, please try again later',
  queueMessageFailed: 'Failed to queue the message',
  queueFailedTitle: 'Queue failed',
  // TODO(common): candidate common word
  deleteFailed: 'Delete failed',
  guideMessageNotSent: 'The guide message was not sent, please try again later',
  cannotGuideTitle: 'Cannot guide',
  submitGuideFailed: 'Failed to submit the guide',
  guideSetTitle: 'Set as guide conversation',
  guideSetMessage: 'Will be inserted into the current conversation after the next tool result',
  guideFailedTitle: 'Guide failed',

  // ── Messages: send/stop (message/send.ts) ──
  autoCompressingTitle: 'Conversation is auto-compressing',
  autoCompressingBlockSend: 'Sending/stopping is unavailable while compression is in progress',
  compressionBlocksSend: 'You can continue sending after compression completes',
  textRequiredTitle: 'Text required',
  textRequiredMessage: 'Attached files must be sent with a text message',
  subAgentRunningTitle: 'Background sub-agent running',
  subAgentRunningMessage: 'Please wait for the background task to finish before sending images/videos',
  runningTextOnlyTitle: 'Text only while running',
  runningTextOnlyMessage: 'Send images/videos after the current task finishes',
  conversationRunningTitle: 'Conversation is running',
  conversationRunningMessage: 'Please wait for the current conversation task to finish before sending a new message; other conversations in the same workspace can run in parallel.',
  connectionLostTitle: 'Connection lost',
  connectionLostMessage: "Messages can't be sent right now; please retry after the connection is restored",
  uploadingTitle: 'Uploading',
  uploadingMessage: 'Please wait for the image/video upload to finish before sending',
  modelNoImageTitle: 'Model does not support images',
  modelNoImageMessage: 'Switch to a model that supports image input before sending images',
  modelNoVideoTitle: 'Model does not support videos',
  modelNoVideoMessage: 'Switch to a model that supports video input before sending videos',
  noMixedMediaTitle: 'Send media separately',
  noMixedMediaMessage: 'Videos and images must be sent separately; each message can contain only one media type',
  videoProcessingTitle: 'Video processing',
  videoProcessingMessage: 'Reading the video can take a while, please be patient',
  initializingBackupTitle: 'Initializing backup',
  initializingBackupMessage: 'Creating a full workspace snapshot, please wait...',
  createConversationFailed: 'Failed to create conversation',
  stopRequestedTitle: 'Stop requested',
  stopRequestedMessage: 'If the main conversation does not stop, please wait; background tasks can be stopped from the status bar',
  autoCompressingBlockStop: 'Compression is in progress; the task cannot be stopped right now',

  // ── Messages: system command (message/systemCommand.ts) ──
  commandEmpty: 'Command cannot be empty',
  connectionUnavailable: 'Connection unavailable',
  connectionUnavailableMessage: 'Cannot run commands right now, please try again later.',
  commandExecutionFailed: 'Command execution failed',
  clearedTitle: 'Cleared',
  conversationCleared: 'Conversation cleared',
  systemStatus: 'System status',
  statusUpdatedTitle: 'Status updated',
  statusFetched: 'System status fetched',
  commandFailedLabel: 'Command failed',
  commandExecutedTitle: 'Command executed',
  commandDone: 'Done',

  // ── Conversation: actions (conversation/action.ts) ──
  // TODO(common): candidate common word
  serverNotSuccessMessage: 'Server did not return a success status',
  createConversationErrorTitle: 'Error creating conversation',
  createWorkspaceConversationErrorTitle: 'Error creating workspace conversation',
  deleteConversationTitle: 'Delete conversation',
  deleteConversationConfirmMessage: 'Are you sure you want to delete this conversation? This cannot be undone.',
  deleteConversationFailedTitle: 'Failed to delete conversation',
  deleteConversationErrorTitle: 'Error deleting conversation',
  duplicateConversationTitle: 'Duplicated conversation',
  duplicateConversationFailedTitle: 'Failed to duplicate conversation',
  duplicateConversationErrorTitle: 'Error duplicating conversation',

  // ── Conversation: load (conversation/load.ts) ──
  loadConversationFailedTitle: 'Failed to load conversation',
  loadConversationErrorTitle: 'Error loading conversation',

  // ── Versioning (versioning.ts) ──
  // Title "版本管理" reuses common.versioning, not redefined here
  versioningFetchStatusFailed: 'Failed to fetch version status',
  versioningLoadCheckpointsFailed: 'Failed to load checkpoints',
  versioningEnabledForNext: 'Versioning enabled for the next new conversation',
  versioningDisabledForNext: 'Versioning cancelled for the next new conversation',
  versioningToggleFailed: 'Failed to toggle versioning',
  versioningOn: 'Enabled',
  versioningOff: 'Disabled',
  versioningSwitchFailed: 'Switch failed',
  versioningDetailParseFailed: 'Failed to parse detail response',
  versioningLoadDetailFailed: 'Failed to load detail',
  versioningScopeConversationOnly: 'conversation only',
  versioningScopeConversationAndWorkspace: 'conversation and workspace',
  versioningModeCopy: 'duplicate the conversation',
  versioningModeOverwrite: 'overwrite the current conversation',
  versioningRestoreConfirmTitle: 'Confirm restore',
  versioningRestoreConfirmMessage: 'Restore {scope} to the state at input #{seq} and {mode}. Continue?',
  versioningRestoreConfirmText: 'Restore',
  versioningRestoreFailed: 'Restore failed',
  versioningRestoreConversationTitle: 'Restored conversation',
  versioningRestoreCopyDone: 'Duplicated and restored to a new conversation',
  versioningRestoreDone: 'Restore complete',
} as const;
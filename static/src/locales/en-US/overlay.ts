// Locale namespace: overlay (en-US mirror, keys must match zh-CN/overlay.ts exactly).
// Used by: static/src/components/overlay/* (13 dialogs / overlays)
export default {
  // TODO(common): candidate common words — generic statuses/verbs that could be merged into common.ts:
  //  stopInProgress/stopManually/stopFailed/uploading/sendFromLocal/refresh/switchOn/switchOff/complete/inProgress/calling
  // ── Shared stop actions (BackgroundCommandDialog / SubAgentActivityDialog) ──
  stopInProgress: 'Stopping...',
  stopManually: 'Stop manually',
  stopFailed: 'Failed to stop',

  // ── BackgroundCommandDialog ──
  bgCommandTitle: 'Background command {id}',
  bgCommandReadingOutput: 'Reading background command output...',

  // ── ConversationReviewDialog ──
  reviewTitle: 'Conversation Review',
  reviewSubtitle: 'Select a conversation to generate a review file',
  generatedHint: 'Generated {path}',
  conversationList: 'Conversations',
  conversationCount: '{n} total',
  noConversations: 'No conversations',
  unnamedConversation: 'Untitled conversation',
  currentTag: 'Current',
  messageCount: '{n} msgs',
  toolCount: ' · {n} tools',
  loadingMore: 'Loading...',
  loadMore: 'Load more',
  noMore: 'No more',
  previewTitle: 'Preview (first {n})',
  previewGenerating: 'Generating preview...',
  previewEmptyHint: 'Select a conversation on the left to preview',
  previewLimitHint: 'Shows up to the first {n} lines',
  previewCount: '{n} lines',
  sendToModel: 'Send to model',
  generating: 'Generating...',

  // ── GoalProgressDialog ──
  goalDoneTitle: 'Goal completed',
  goalStoppedTitle: 'Goal mode stopped',
  goalRunningTitle: 'Goal mode in progress',
  goalLabel: 'Goal: {goal}',
  metricTurns: 'Turns run',
  metricTokens: 'Tokens used',
  metricToolCalls: 'Tool calls',
  metricDuration: 'Duration',
  stopReason: 'Stop reason: {reason}',
  summaryDone: 'Summary',
  summaryLatest: 'Latest progress',
  statusDone: 'Completed',
  statusStopped: 'Stopped',
  reasonIdleNoTool: 'The main model stopped without calling any tools (it may be stuck)',
  reasonMaxTurns: 'Max turn limit reached',
  reasonMaxTokens: 'Cumulative token limit reached',
  reasonUserCancel: 'Stopped manually by user',

  // ── ImageLightbox ──
  imagePreview: 'Image preview',
  closePreview: 'Close preview',

  // ── ImagePicker ──
  imagePickerTitle: 'Select images (up to 9)',
  uploading: 'Uploading...',
  sendFromLocal: 'Send from local',
  noImages: 'No image files found',
  imageSelectedCount: 'Selected {n} / 9',

  // ── NewUserTutorialPrompt ──
  tutorialPromptAriaLabel: 'New user tutorial prompt',
  welcome: 'Welcome',
  newUserTitle: 'New user {name}',
  user: 'User',
  tutorialPromptDesc: 'Would you like a quick tutorial to get familiar with this system?',
  processing: 'Processing...',
  startTutorial: "Let's start!",
  noMorePrompt: "Don't ask again",

  // ── PathAuthorizationDialog ──
  pathAuthTitle: 'Path Authorization',
  writableMode: 'Read & write',
  readableMode: 'Read-only',
  writableHint:
    'Read-write paths are writable in the writable sandbox and read-only in the read-only sandbox.',
  readableHint:
    'Read-only paths join the read whitelist of the read-only sandbox (which denies all reads by default except system dirs, the workspace, and authorized paths).',
  writablePlaceholder: 'One path per line, e.g. ~/Desktop/agents-export',
  readablePlaceholder: 'One path per line, e.g. ~/Documents/reference',

  // ── PlanApprovalDialog ──
  planApprovalAriaLabel: 'Plan awaiting approval',
  planApprovalTitle: 'Plan awaiting approval',
  planApprovalMinimize: 'Close the dialog; restore it later from the status bar',
  planTruncatedNote: '(Content too long; preview truncated. See the file for the full content)',
  planCommentPlaceholder:
    'Feedback (optional): added as extra requirements when approving, or describe what to adjust when rejecting...',
  planRejectTitle: 'Reject this plan; the AI will revise it based on your feedback and resubmit',
  reject: 'Reject',
  planApproveTitle: 'Approve the plan and switch to execute mode to start',
  submittingPlan: 'Submitting...',
  approveAndExecute: 'Approve & execute',

  // ── SubAgentActivityDialog ──
  subAgentProgressTitle: 'Sub-agent #{id} progress',
  readingActivity: 'Reading sub-agent activity...',
  noActivity: 'No activity records',
  toolReadFile: 'Reading {path}',
  toolWriteFile: 'Writing file {path}',
  toolReadSkill: 'Reading skill {name}',
  toolWebSearch: 'Searching the web for {query}',
  toolExtractWebpage: 'Extracting {url}',
  toolRunCommand: 'Running command {command}',
  toolEditFile: 'Editing {path}',
  toolReadMedia: 'Reading media file {path}',
  toolGeneric: 'Tool',
  stateCompleted: 'Completed',
  stateCalling: 'Calling',
  stateInProgress: 'In progress',

  // ── TutorialOverlay ──
  tutorialExit: 'Exit',
  tutorialFallbackTitle: 'Tutorial',
  tutorialWaitingTarget: 'Target element not found. Retry later or skip this step.',
  tutorialMustClickHint: 'Click the highlighted target to continue.',
  tutorialScrollHint: 'Tip: personal space content is scrollable.',
  tutorialComplete: 'Finish',
  tutorialClickFirst: 'Click the highlighted target first',
  tutorialNextSkip: 'Next (skip)',
  tutorialNext: 'Next',

  // ── UserQuestionDialog ──
  userQuestionAriaLabel: 'Your answer is needed',
  windowControlAria: 'Window controls',
  minimizeAria: 'Minimize for now',
  userQuestionWindowTitle: 'Action needed',
  questionIndex: 'Question {current} of {total}',
  questionNavAria: 'Switch question',
  prevQuestionAria: 'Previous question',
  nextQuestionAria: 'Next question',
  answerPlaceholder: 'Type your answer...',
  dismissTitle: 'Skip this question; the AI will ask you directly in the conversation instead',
  dismiss: '✕ Skip',
  submittingAnswer: 'Submitting...',

  // ── VersioningDialog ──
  switchOn: 'On',
  switchOff: 'Off',
  refresh: 'Refresh',
  emptyMessage: '(Empty message)',
  filesCount: '{n} files',
  noCheckpoints: 'No version points',
  loadingDetail: 'Loading details...',
  detailTitle: 'Commit details #{seq}',
  noDiffLines: 'No text changes to show',
  diffTruncated: 'Content too long, truncated',
  selectCheckpoint: 'Select a version point to view details',
  restoreScope: 'Restore scope',
  scopeConversationOnly: 'Conversation only',
  scopeWorkspaceAndConversation: 'Conversation & workspace',
  restoreModeLabel: 'Restore mode',
  modeOverwrite: 'Overwrite current conversation',
  modeCopy: 'Copy conversation',
  restoring: 'Restoring...',
  confirmRestore: 'Confirm restore',

  // ── VideoPicker ──
  videoPickerTitle: 'Select video (1 at a time)',
  noVideos: 'No video files found',
  videoSelectedCount: 'Selected {n} / 1',
} as const;
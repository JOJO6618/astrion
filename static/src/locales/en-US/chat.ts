// chat message pack (wave-2 migration namespace, group 4: chat core components)
// Spec: doc/frontend/i18n_spec.md. Key structure must match zh-CN/chat.ts exactly (tsc enforced).
export default {
  // —— User message bubble: expand / copy / branch actions ——
  expand: 'Expand',
  collapse: 'Collapse',
  branch: 'Branch',
  userDefaultName: 'User',
  subAgentName: 'Sub-agent',
  generating: 'Generating...',

  // —— User message header source labels ——
  userHeaderGuide: 'Guidance',
  userHeaderGoal: 'Goal',
  userHeaderReview: 'Review',
  userHeaderCompression: 'Compression',
  userHeaderNotify: 'Notification',

  // —— Compact brief summary labels ——
  briefGoalReview: 'Goal review complete',
  briefSubAgent: 'Background sub-agent complete',
  briefBackgroundCommand: 'Background command complete',
  briefCompression: 'Conversation compressed',
  briefGoal: 'Goal in progress',
  briefGuidance: 'Running guidance',
  briefNotify: 'System notification',
  briefSystem: 'System message',

  // —— Sub-agent completion notice (system action label fallback) ——
  subAgentDoneLabel: 'Sub-agent {n} complete',

  // —— Astrion header work timer status ——
  workInProgress: 'Working {duration}',
  workCompleted: 'Work complete {duration}',

  // —— Thinking block status ——
  thinking: 'Thinking',
  thinkingRunning: 'Thinking...',

  // —— File append (append / append_payload) ——
  targetFile: 'target file',
  appendDone: 'File append complete',
  appendSuccess: 'Append content written to {path} (saved to file)',
  appendFailed: 'Failed to write to {path}; content captured for later fixes.',
  appendWarning: 'No closing marker detected. Continue adding content as prompted.',
  appendWarningFollow: 'No closing marker detected. Continue adding content as prompted.',
  linesCount: '· {n} lines',
  bytesCount: '· {n} bytes',

  // —— System message / quick nav ——
  systemMessage: 'System message (role: {role})',
  quickNavAria: 'Quick jump to user inputs',
  emptyInput: '(Empty input)',
  noReply: '(No reply yet)',

  // —— Tool execution summary (minimal mode) ——
  executing: 'Executing...',
  callingTool: 'Calling {name}...',
  toolCompleted: 'Tool complete',
  executingTool: 'Executing tool...',
  runTool: 'Run tool',
  summaryRead: 'Read {n} files',
  summaryCommand: 'Ran {n} commands',
  summaryEdit: 'Edited files {n} times',
  summarySearch: 'Searched {n} times',
  summaryWebpage: 'Viewed {n} webpages',
  summaryMcp: 'Ran MCP tools {n} times',
  summaryWorkflowActivate: 'Activated {n} workflows',
  summaryWorkflowAdvance: 'Advanced workflow {n} times',
  summaryWorkflow: 'Ran {n} workflow operations',
  summaryTodoCreate: 'Created {n} todos',
  summaryTodoUpdate: 'Updated {n} todos',
  summaryMemoryUpdate: 'Updated memory {n} times',
  summaryMemoryRead: 'Viewed memory {n} times',
  summaryConversation: 'Reviewed {n} conversations',
  summarySubAgent: 'Created {n} sub-agents',
  summarySubAgentManage: 'Managed sub-agents {n} times',
  summaryWait: 'Waited {n} times',
  summaryAsk: 'Asked {n} times',
  summaryPlan: 'Submitted {n} plans',
  summarySkill: 'Archived {n} skills',
  summarySettings: 'Updated personalization {n} times',
  summaryEasterEgg: 'Triggered {n} easter eggs',
  summaryOther: 'Ran {n} other operations',

  // —— Stacked blocks "More" header ——
  more: 'More',
  allExpanded: 'Showing all',
  stepsTotal: '{n} steps total',
  stepsHidden: '{n} steps collapsed',

  // —— Code block ——
  copyCode: 'Copy code',

  // —— Edit summary card ——
  filesEdited: 'Edited {n} files',
  fileChanges: 'File changes: {path}',
  diffTruncated: 'Content too long; truncated',
  diffEmpty: 'No text changes to show',

  // —— File type labels (FileChips) ——
  fileTypeDoc: 'Document',
  fileTypeSheet: 'Spreadsheet',
  fileTypeSlides: 'Presentation',
  fileTypePdf: 'PDF',
  fileTypeText: 'Text',
  fileTypeArchive: 'Archive',
  fileTypeCode: 'Code',
  fileTypeGeneric: 'File',
  removeFile: 'Remove {name}',

  // —— PDF preview ——
  pdfLoading: 'Loading PDF...',
  pdfLoadFailed: 'Failed to load PDF',
  pdfEmpty: 'PDF content is empty',
  pdfRenderFailed: 'Failed to render PDF',

  // —— File display card ——
  preview: 'Preview',
  fileEmpty: 'File is empty',
  htmlPreviewNotice: 'References external resources; preview may be incomplete',
  networkError: 'Network error',
  imageLoadFailed: 'Failed to load image',
  csvTruncated: 'Showing first {n} rows. Download the file for full content.',

  // —— Virtual monitor surface (complements monitor.*) ——
  vmBrowserTitle: 'Multimodal Knowledge Browser',
  vmBrowserReady: 'Ready to search...',
  vmWebExtract: 'Web Extraction',
  vmExtractWait: 'Waiting to extract',
  vmFile: 'Files',
  vmTerminal: 'Terminal',
  vmCommandLine: 'Command line',
  vmReaderTitle: 'Reader',
  vmMemory: 'Memory',
  vmTodo: 'Todos',
  vmWait: 'Wait',
  vmNewFile: 'New file',
  vmNewFolder: 'New folder',
  vmReadFile: 'Read file',
  vmEditFile: 'Edit file',
  vmRename: 'Rename',
  vmDeleteFile: 'Delete file',
  vmFocusFile: 'Focus file',
  vmUnfocus: 'Unfocus',
  vmSaveWebpage: 'Save webpage',
  vmSaveSnapshot: 'Save snapshot',
  vmResetTerminal: 'Reset terminal',
  vmCloseTerminal: 'Close terminal',

  // —— Monitor scene progress states (progressMap stores keys; getSceneProgressLabel resolves with t() at call time) ——
  // Note: zh texts must keep the '正在...' prefix — stores/monitor.ts transformStatus strips it and
  // wraps with t('stores.playbackStatus') ('Replaying {label}'); en texts don't match the prefix so pass through.
  progressBrowserSearch: 'Searching...',
  progressWebExtract: 'Extracting...',
  progressWebSave: 'Saving...',
  progressAppendFile: 'Editing...',
  progressModifyFile: 'Editing...',
  progressCreateFile: 'Creating...',
  progressDeleteFile: 'Deleting...',
  progressReadFile: 'Reading...',
  progressReader: 'Reading...',
  progressFocus: 'Focusing...',
  progressUnfocus: 'Processing...',
  progressRunCommand: 'Running command',
  progressTerminalSession: 'Opening terminal',
  progressTerminalInput: 'Terminal input',
  progressTerminalSnapshot: 'Fetching terminal output',
  progressMemoryUpdate: 'Syncing memory...',
  progressTodoCreate: 'Updating todos...',
  progressTodoUpdate: 'Updating todos...',
  progressTodoFinish: 'Completing task...',
  progressTodoFinishConfirm: 'Confirming task...',
  progressTodoDelete: 'Removing task...',
  progressWait: 'Waiting...',
  progressSleep: 'Waiting...',
  progressCreateFolder: 'Creating folder...',
  progressRenameFile: 'Renaming...',
  progressTerminalReset: 'Resetting terminal...',
  progressTerminalSleep: 'Preparing to wait',
  progressTerminalRun: 'Terminal running',
  progressOcr: 'Extracting...',
  progressMemory: 'Syncing memory...',
  progressTodo: 'Managing todos...',
  progressGenericTool: 'Calling tool',
} as const;
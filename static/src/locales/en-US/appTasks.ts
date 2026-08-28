// Locale namespace: appTasks (en-US mirror, keys must match zh-CN/appTasks.ts exactly).
// Used by: static/src/app/methods/taskPolling/{aiStream,compression,goal,lifecycle,placeholder,tool,workflow}.ts, methods/tooling.ts, methods/resources.ts
export default {
  // ── Generic status labels (shared by resources / tooling) ──
  // TODO(common): candidate common word
  unknown: 'Unknown',
  unknownTime: 'Unknown time',
  hostMode: 'Host mode',
  containerMode: 'Container mode',
  // TODO(common): candidate common word
  paused: 'Paused',
  // TODO(common): candidate common word
  stopped: 'Stopped',
  // TODO(common): candidate common word
  justNow: 'Just now',
  minutesAgo: '{n} min ago',
  hoursAgo: '{n} hr ago',

  // ── Conversation compression (compression.ts) ──
  // TODO(common): candidate common word
  compressionManual: 'Manual',
  // TODO(common): candidate common word
  compressionAuto: 'Auto',
  compressing: 'Compressing',
  compressingMessage: 'Compressing conversation ({mode})...',
  shallowCompressionTitle: 'Auto shallow compression',
  shallowCompressedMessage: 'Auto-compressed {n} older tool results',
  compressionComplete: 'Compression complete',
  compressedEarlierContent: 'Compressed earlier conversation content',

  // ── Task restore (compression.ts) ──
  taskRestoredTitle: 'Task resumed',
  taskRestoredMessage: 'Found a running task and reconnected',

  // ── Awaiting-first-content placeholder (aiStream.ts / compression.ts) ──
  thinkingLabel: 'Thinking...',

  // ── Goal / workflow review panel (goal.ts / workflow.ts) ──
  goalReviewTitle: 'Goal review',
  workflowReviewTitle: 'Workflow review',
  reviewStarted: 'Review started',
  reviewRound: 'Review round {n}',

  // ── Auto-approval (tool.ts) ──
  autoApprovalRecordTitle: 'Auto-approval record',
  autoApprovalStarted: 'Auto-approval started',
  approvalApproved: 'Approved',
  approvalRejected: 'Rejected',
  approvalFinalMessage: '{decision}\nReason: {reason}',
  reasonNotProvided: 'Not provided',

  // ── Tool blocks & tool settings (tool.ts / tooling.ts) ──
  preparingTool: 'Preparing to call {name}...',
  interruptedByNewResponse: 'Interrupted by a new response',
  cannotModify: 'Cannot modify',
  categoryEnforcedByAdmin: 'This tool category is enforced by the administrator',
  cannotSwitchTool: 'Cannot switch tool',
  toolToggleLockedByAdmin: 'Tool enable/disable is locked by the administrator',

  // ── User question notification (tool.ts) ──
  answerNeededTitle: '{dot} Needs an answer - Agents',
  questionConfirmTitle: 'Need your confirmation on a question',

  // ── Task errors & retry (lifecycle.ts) ──
  retrySoonTitle: 'Retrying soon',
  retryInSeconds: 'Retrying in {n}s (attempt {attempt}/{max})\nError: {error}',
  toolCallFailed: 'Tool call failed',
  taskFailedTitle: 'Task failed',
  apiErrorTitle: 'API call failed',
  apiErrorMessage: 'Model service error: {error}',
  timeoutTitle: 'Task timed out',
  timeoutMessage: 'The task ran too long and was stopped automatically',
  quotaTitle: 'Quota exceeded',
  quotaMessage: 'Your usage quota has been used up',

  // ── Download (resources.ts) ──
  cannotCompleteDownload: 'Could not complete the download',
} as const;
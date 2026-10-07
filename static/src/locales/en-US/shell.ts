// Locale namespace: shell (en-US mirror, keys must match zh-CN/shell.ts exactly).
// Used by: static/src/components/shell/*, static/src/components/panels/*
export default {
  // ── ConfirmDialog: default texts (overridable by callers) ──
  confirmOperation: 'Confirm action',

  // ── FileContextMenu: file/folder context menu ──
  downloadFile: 'Download file',
  downloadArchive: 'Download archive',

  // ── QuotaToast / ToastStack: notification close button ──
  closeNotification: 'Close notification',

  // ── FocusPanel: focused files panel ──
  focusFilesCount: 'Focused files ({n}/3)',
  closeFocusPanel: 'Close focus panel',
  noFocusFiles: 'No focused files',

  // ── GitChangesPanel: Git changes panel ──
  closeGitPanel: 'Close Git changes panel',
  loadingGitChanges: 'Loading Git changes...',
  noUncommittedChanges: 'No uncommitted changes',
  openFileWithApp: 'Open file with an app',
  dockerModeUnavailable: 'Unavailable in Docker mode',
  noAppsDetected: 'No apps detected',
  restore: 'Restore',
  hiddenLines: '{n} unchanged lines',
  unsetUpstream: 'No upstream branch set',
  detectAppsFailed: 'Failed to detect apps',
  openFileFailed: 'Failed to open file',

  // ── TerminalPanel: terminal panel ──
  waitingTerminalSession: 'Waiting for terminal session...',
  closeTerminalPanel: 'Close terminal panel',
  noOpenTerminals: 'No open terminals',

  // ── ToolApprovalPanel: tool approval panel ──
  closeApprovalPanel: 'Close approval panel',
  noPendingApprovals: 'No pending approvals',
  pathLabel: 'Path: ',
  renameLabel: 'Rename: ',
  toolLabel: 'Tool: ',
  summaryLabel: 'Summary: ',
  approvalTitle: 'Tool approval',
  collapseApprovalPanel: 'Collapse approval panel',
  restoreApprovalPanel: 'Expand approval panel',
  requestReason: 'Request reason',
  parameters: 'Parameters',
  singleFullAccess: 'One-time full access',
  allow: 'Allow',
  allowFullAccessExecution: 'Allow this execution with full access',
  reviewRecords: 'Review records',
  noApprovalRecords: 'No records',
  reviewStages: {
    start: 'Review started',
    modelCall: 'Calling review model',
    runCommand: 'Running review command',
    complete: 'Review finished',
    decision: 'Review decision'
  },
  autoReviewStatus: {
    pending: 'Waiting for automatic review',
    reviewing: 'Automatic review in progress',
    approved: 'Automatic review approved',
    rejected: 'Automatic review rejected'
  },
  finalDecision: {
    approved: 'Execution allowed',
    rejected: 'Execution rejected',
    expired: 'Expired',
    cancelled: 'Cancelled',
    timeout: 'Timed out'
  },
  humanDecision: {
    approved: 'Approved by you',
    rejected: 'Rejected'
  },
  run: 'Run',
  reject: 'Reject',
  // Tool-name label mapping (map stores keys, resolved with t() at use site)
  toolRunCommand: 'Run command',
  toolTerminalInput: 'Terminal input',
  toolCreateFile: 'Create file',
  toolCreateFolder: 'Create folder',
  toolDeleteFile: 'Delete file',
  toolRenameFile: 'Rename file',
  toolWriteFile: 'Write file',
  toolEditFile: 'Edit file',
  pendingApproval: 'Pending approval'
} as const;

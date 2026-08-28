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
  switchToUnrestricted: 'Switch to unrestricted',
  run: 'Run',
  reject: 'Reject',
  toolApprovalTitle: 'Tool approval ({n})',
  // Tool-name label mapping (map stores keys, resolved with t() at use site)
  toolRunCommand: 'Run command',
  toolTerminalInput: 'Terminal input',
  toolCreateFile: 'Create file',
  toolCreateFolder: 'Create folder',
  toolDeleteFile: 'Delete file',
  toolRenameFile: 'Rename file',
  toolWriteFile: 'Write file',
  toolEditFile: 'Edit file',
  pendingApproval: 'Pending approval',
} as const;
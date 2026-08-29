// English mirror of zh-CN/quickdock.ts — keys must stay identical (type-enforced by en-US.ts).
// 规范见 doc/frontend/i18n_spec.md。
export default {
  // —— Window titles ——
  subAgent: 'Sub-agent',
  backgroundCommand: 'Background command',
  agentNamed: 'Sub-agent {id}',
  more: 'More',
  fileWindowTitle: 'Files',
  todoWindowTitle: 'To-dos',

  // —— QuickDock global ⋯ menu ——
  menuForceStop: 'Force stop',
  menuRevealInManager: 'Reveal in file manager',
  menuCopyPath: 'Copy path',
  killForceStopFailed: 'Failed to force stop',
  revealDetectAppsFailed: 'Failed to detect available apps',
  revealNoAppsFound: 'No available apps found',
  revealOpenFileFailed: 'Failed to open file',
  copiedRelativePath: 'Relative path copied',

  // —— Runner detail panel ——
  noProgress: 'No progress yet',
  noOutput: 'No output yet',
  statusIdle: 'Idle',
  statusCompleted: 'Completed',
  statusFailed: 'Failed',
  statusTimeout: 'Timed out',
  statusTerminated: 'Terminated',
  statusEnded: 'Ended',
  contextTokensTitle: 'Context {tokens} tokens',

  // —— Tool timeline ——
  toolReadFile: 'Read {path}',
  toolWriteFile: 'Write {path}',
  toolReadSkill: 'Read skill {name}',
  toolSearch: 'Search {query}',
  toolExtract: 'Extract {url}',
  toolRunCommand: 'Run command {command}',
  toolEditFile: 'Edit {path}',
  toolReadMedia: 'Read media file {path}',

  // —— Sub-agent detail panel: context compression notice ——
  contextCompressed: 'Context compressed (round {round})',

  // —— File preview panel ——
  resizeWidthHint: 'Drag to resize width',
  previewTypeUnsupported: 'This file type cannot be previewed',
  loadFailedHttp: 'Failed to load (HTTP {status})',

  // —— Workflow window ——
  reviewing: 'Reviewing',
  roundsLabel: 'Round {n}',
  workflowEndRow: 'Complete',

  // —— Effort slider ——
  effortTitle: 'Reasoning effort',
  effortDefault: 'Default',
  effortMoreEfficient: 'More efficient',
  effortMoreIntelligent: 'Smarter',

  // —— File-at reference menu ——
  atMenuAria: 'File reference',
  atMenuPicker: 'Select in file manager',
  atMenuPickerDesc: 'Choose a local file',
  atMenuNoMatch: 'No matching files',
  atMenuSearching: 'Searching...',

  // —— Quick menu ——
  uploading: 'Uploading...',
  uploadFile: 'Upload file',
  conversationReview: 'Review conversation',
  sendImage: 'Send image',
  sendVideo: 'Send video',
  disableTools: 'Disable tools',
  goalMode: 'Goal mode',
  goalDone: 'Done',
  goalArmed: 'Ready',
  settingsMenu: 'Settings',
  toolSettingsSyncing: 'Syncing tool settings...',
  noControllableTools: 'No controllable tools',
  lockedByAdmin: 'Locked by admin',
  toolDisable: 'Disable',
  toolEnable: 'Enable',
  realtimeTerminal: 'Live terminal',
  usageStats: 'Usage stats',
  compressing: 'Compressing...',
  compressConversation: 'Compress conversation',
  approvalPanel: 'Approval panel',
  pathAuthorization: 'Path authorization',
} as const;
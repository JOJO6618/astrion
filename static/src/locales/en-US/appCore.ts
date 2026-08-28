// Locale namespace: appCore (en-US mirror, keys must match zh-CN/appCore.ts exactly).
export default {
  // Drag & drop upload / initial loading (App.vue)
  dragUploadText: 'Release to upload images',
  dragUploadHint: 'Drag image or video files here',
  connectingServer: 'Connecting to server, please wait',
  refreshPage: 'Refresh page',

  // Header dropdown / mobile navigation (App.vue)
  model: 'Model',
  runMode: 'Run mode',
  collapseQuickDock: 'Collapse quick dock',
  expandQuickDock: 'Expand quick dock',
  switchWorkspace: 'Switch workspace',
  conversationHistory: 'Chat history',
  personalSpace: 'Personal space',

  // show_html / show_file cards (bootstrap.ts)
  refresh: 'Refresh',
  fullscreen: 'Fullscreen',
  cardActions: 'Card actions',
  imageUnsupportedPath: "Can't display image: unsupported image path {src}",
  imageMissingPath: "Can't display image: image path missing",
  imageLoadFailed: 'Failed to load image',
  renderingContent: 'Rendering content...',
  showFileMissingPath: '[show_file: missing path attribute]',

  // Header run mode / model (computed.ts)
  fastMode: 'Fast mode',
  fastModeDesc: 'Low reasoning, faster responses',
  thinkingModeDesc: 'Continuous reasoning for complex tasks',
  modelNotSelected: 'No model selected',
  // Plural forms in English; callers should pass both {n} and {count}.
  backgroundCommandCount: '1 background command | {count} background commands',
  backgroundAgentCount: '1 background agent | {count} background agents',
  backgroundRunning: 'running...',
  thinking: 'Thinking...',
  waitingApiResponse: 'Waiting for API response...',

  // Blank hero welcome lines (state.ts blankWelcomePool)
  welcomeHelp: 'How can I help?',
  welcomeHot: 'Curious about trending topics?',
  welcomeHomework: 'Need help with homework?',
  welcomeCode: 'Up for some code?',
  welcomeChat: 'Care for a chat?',
  welcomeOrganize: 'Want me to organize your thoughts?',
  welcomeTool: 'Want me to build a small tool for you?',
  welcomeContinue: 'Send me a line and I will take it from here.',
  welcomeDraw: 'Want me to draw something for you?',
  welcomeCat: 'Want to see some kittens?',
  welcomePuzzle: 'Throw a hard problem at me.',
  welcomeFun: 'Want to see something fun?',
  welcomeNeed: 'Tell me what you need~',

  // Network permission / work mode / permission mode / execution mode options (state.ts).
  // Labels reuse appUi.* / input.* / personalization.*; descriptions and missing labels live here.
  networkRestrictedDesc: 'Local loopback only; no external network',
  networkFullDesc: 'Allow all outbound and inbound connections',
  workModePlanDesc: 'Plan only; execute after approval',
  workModeAskDesc: 'Discuss to confirm, then start',
  workModeExecuteDesc: 'Fill in details and execute directly',
  permissionApproval: 'Approval',
  permissionAutoApproval: 'Auto-approval',
  permissionUnrestricted: 'Unrestricted',
  permissionReadonlyDesc: 'Read/search tools only; no workspace modifications',
  permissionApprovalDesc: 'Tools that modify workspace files run only after your approval',
  permissionAutoApprovalDesc:
    'Writes in the workspace pass through; high-risk operations are auto-approved by the background agent',
  permissionUnrestrictedDesc: 'Keep current default behavior with no extra restrictions',
  executionSandboxDesc: 'All commands run in the system sandbox',
  executionDirectDesc: 'All commands run directly on the host',
} as const;
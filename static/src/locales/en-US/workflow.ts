// Locale namespace: workflow (en-US mirror, keys must match zh-CN/workflow.ts exactly).
export default {
  // Top bar
  backToLibrary: 'Back to workflow library',
  badgeBuiltin: 'Built-in',
  badgeUser: 'User',
  unsaved: 'Unsaved',
  checkIssues: 'View structure warnings and errors',
  autoLayout: 'Auto layout',
  addNodeTitle: 'Add a node at the center of the canvas (or double-click empty canvas to add a stage)',
  addNode: 'Add node',

  // Add node types
  stage: 'Stage',
  stageDesc: 'AI run, one in one out',
  review: 'Review',
  reviewDesc: 'Diamond, approve/reject',
  branch: 'Branch',
  branchDesc: 'Many-to-many, conditional routing',
  start: 'Start',
  startDesc: 'Entry point, only one allowed',
  end: 'End',
  endDesc: 'Exit, multiple allowed',

  // Workflow properties
  workflowProps: 'Workflow properties',
  nameLabel: 'Name (unique id)',
  descLabel: 'Description',
  descPlaceholder: 'Used to identify this workflow when selected',
  reviewCapability: 'Review agent forensics',
  reviewReadonly: 'Read-only review',
  reviewActive: 'Command-enabled review',
  reviewModeHint: 'Active mode lets the review agent run read-only commands to verify the result',
  maxRoundsLabel: 'Max rounds per stage',
  endMethodLabel: 'Overall completion',
  endMethodPlaceholder: 'e.g., report written and review approved',
  globalNotes: 'Global notes',
  globalNotesLabel: 'How it works / verification / completion',

  // Stage properties
  stageProps: 'Stage properties',
  stageNameLabel: 'Stage name',
  stageGoalLabel: 'Stage goal',
  stageGoalPlaceholder: 'What this stage should accomplish',
  stageWorkLabel: 'How to work',
  stageWorkPlaceholder: 'How this stage works (natural language)',
  forwardRoute: 'Forward route',
  removeRouteAriaLabel: 'Remove route to {name}',
  endStageEmpty: 'End stage (no forward route)',
  routeHint: 'Drag from the handle on the right of a node to the target; a new line replaces the existing one. Add a branch node to fork.',

  // Delete node (shared across panels)
  deleteNode: 'Delete this node',
  deleteNodeWarning: 'Routes pointing to it will also be removed',
  confirmDelete: 'Confirm delete',

  // Review properties
  reviewProps: 'Review properties',
  reviewNameLabel: 'Review name',
  reviewFocusLabel: 'Review focus',
  reviewFocusPlaceholder: 'What the review agent checks (natural language)',
  maxRejectsLabel: 'Max rejections (times)',
  maxRejectsHint: 'Escalates to the user after the limit (demo only records the value)',
  passRoute: 'Approval route',
  removePassRouteAriaLabel: 'Remove approval route to {name}',
  passEndsWorkflow: 'Approval ends the workflow',
  passRouteHint: 'Where the workflow goes after approval — drag from the right handle of the diamond (blue line)',
  rejectRoute: 'Rejection route',
  removeRejectRouteAriaLabel: 'Remove rejection route to {name}',
  rejectRequired: 'A rejection route is required',
  rejectRouteHint: 'Where the workflow goes on rejection — drag from the top or bottom handle of the diamond (red line; top and bottom exits are equivalent, direction follows the target position)',

  // Branch properties
  branchProps: 'Branch properties',
  branchNameLabel: 'Branch name',
  branchOuts: 'Outgoing lines ({n})',
  removeOutAriaLabel: 'Remove outgoing line to {name}',
  branchConditionPlaceholder: 'Condition: take this path when...',
  branchNoOuts: 'No outgoing lines (dead end)',
  branchRouteHint: 'Drag lines one by one from the right handle; 1-in n-out splits, n-in 1-out joins. Give every outgoing line a condition — the AI picks the path based on them.',

  // Start / end nodes
  startNode: 'Start node',
  endNode: 'End node',
  boundaryNameLabel: 'Name',
  entryRoute: 'Entry route',
  disconnectEntryAriaLabel: 'Disconnect entry route to {name}',
  entryNotConnected: 'Not connected (drag from the right handle of the start node to the first node)',
  startOnlyHint: 'The workflow starts here; only one start node is allowed',

  // Structure validation labels
  issueErrors: '{n} errors',
  issueWarnings: '{n} warnings',
  issueOk: 'Structure OK',

  // Transient flash messages
  flashReplaceReject: 'Replaced the previous reject route (was to "{name}")',
  flashBranchRetarget: 'Redirected this outgoing line to "{name}"',
  flashReplaceRoute: 'Replaced the previous outgoing line (was to "{name}")',
  flashFixErrors: 'Fix the {n} structure errors first',
  flashSaved: 'Saved',
  saveFailed: 'Save failed',
} as const;
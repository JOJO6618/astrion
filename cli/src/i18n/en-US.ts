// English copy (keys must mirror zh-CN.ts exactly)
export default {
  // ── Boot flow ──
  'boot.connecting': 'Connecting to local Astrion service…',
  'boot.connectFailed': 'Cannot connect to local Astrion service ({url}). Please start the service and retry.',
  'boot.starting': 'No running service found. Starting local Astrion service for you (first run takes a few seconds)…',
  'boot.staleServer': 'A service is running but is too old for CLI access (no host token generated). Please restart it and retry.',
  'boot.startTimeout': 'Local service start timed out. Please start it manually (python -m server.app) and retry.',
  'boot.workspace.title': 'Workspace',
  'boot.workspace.notRegistered': 'Current directory is not an Astrion workspace yet:',
  'boot.workspace.askCreate': 'Create it as a workspace?',
  'boot.workspace.create': 'Create',
  'boot.workspace.cancel': 'Cancel',
  'boot.workspace.creating': 'Creating workspace…',
  'boot.session.creating': 'Creating session…',
  'boot.hint.navigate': '←→ select   Enter confirm   Esc quit',

  // ── Approvals ──
  'approval.title': 'Tool Approval',
  'approval.hint': '←→ choose   Enter run   Esc close',
  'approval.run': 'Run',
  'approval.reject': 'Reject',
  'approval.unrestricted': 'Switch to unrestricted',
  'approval.empty': 'No pending approvals',
  'approval.approved': 'Approved and ran: ',
  'approval.rejected': 'Rejected: ',
  'approval.switched': 'Switched to unrestricted and ran: ',

  // ── Reasoning effort ──
  'effort.default.label': 'Default',
  'effort.default.desc': 'Use API default behavior',
  'effort.low.desc': 'Minimal reasoning, fastest',
  'effort.medium.desc': 'Light reasoning',
  'effort.high.desc': 'Balanced reasoning',
  'effort.xhigh.desc': 'Deep reasoning',
  'effort.max.desc': 'Max reasoning, for the hardest tasks',
  'effort.slider': '└ Reasoning effort',

  // ── Path authorization ──
  'path.access.rw': 'Read & Write',
  'path.access.ro': 'Read-only',

  // ── Boundary groups ──
  'boundary.mode.title': 'Work Mode',
  'boundary.mode.plan.label': 'Plan',
  'boundary.mode.plan.desc': 'Plan and discuss only; act after approval',
  'boundary.mode.ask.label': 'Ask',
  'boundary.mode.ask.desc': 'Discuss first, execute after confirmation',
  'boundary.mode.execute.label': 'Execute',
  'boundary.mode.execute.desc': 'Plan autonomously and start directly',
  'boundary.permission.title': 'Permission Mode',
  'boundary.permission.readonly.label': 'Read-only',
  'boundary.permission.readonly.desc': 'Read only; all writes are denied',
  'boundary.permission.approval.label': 'Approval',
  'boundary.permission.approval.desc': 'Writes/commands require approval',
  'boundary.permission.auto.label': 'Auto-review',
  'boundary.permission.auto.desc': 'Review agent auto-approves writes',
  'boundary.permission.unrestricted.label': 'Unrestricted',
  'boundary.permission.unrestricted.desc': 'Free read/write in workspace',
  'boundary.env.title': 'Execution Environment',
  'boundary.env.sandbox.label': 'Sandbox',
  'boundary.env.sandbox.desc': 'Commands run in OS sandbox',
  'boundary.env.direct.label': 'Direct',
  'boundary.env.direct.desc': 'Run directly on host (high risk)',
  'boundary.network.title': 'Network Access',
  'boundary.network.restricted.label': 'Restricted',
  'boundary.network.restricted.desc': 'Localhost only',
  'boundary.network.full.label': 'Full',
  'boundary.network.full.desc': 'External network allowed',

  // ── Help ──
  'help.slash': 'Command menu (at line start or after space)',
  'help.guidance': 'Guidance (inject into current run)',
  'help.thinking': 'Expand / collapse thinking',
  'help.replay': 'Replay demo',
  'help.updown': 'Select in panels',
  'help.leftright': 'Effort slider / path group switch',
  'help.enter': 'Confirm / run',
  'help.backspace': 'Delete / reject (when input is empty)',
  'help.esc': 'Back / close / quit',

  // ── Status bar ──
  'status.thinking': 'thinking',
  'status.fast': 'fast',

  // ── Runtime ──
  'runtime.sendFailed': 'Send failed: ',
  'runtime.decideFailed': 'Approval action failed: ',
  'runtime.pollFailed': 'Event polling interrupted: ',
  'runtime.windowGap': 'Event stream gap (server window overflow); resumed from latest position.',
  'runtime.stopped': 'Task stopped',
  'runtime.error': 'Task error: ',
  'approval.params': 'Arguments',
  'tool.run_command': 'Run command',
  'tool.write_file': 'Write file',
  'tool.edit_file': 'Edit file',
  'tool.read_file': 'Read file',

  // ── Session list / loading ──
  'session.new': 'New chat',
  'session.untitled': 'Untitled chat',
  'session.draftHint': 'New chat draft (starts on first message)',
  'session.loaded': 'Loaded conversation: ',
  'session.loadFailed': 'Failed to load conversation: ',
  'time.justNow': 'just now',
  'time.minutesAgo': 'm ago',
  'time.hoursAgo': 'h ago',
  'time.daysAgo': 'd ago',

  // ── /context panel ──
  'context.title': 'Context usage   Esc to close',
  'context.current': 'Current ',
  'context.totalInput': 'Input   ',
  'context.totalOutput': 'Output  ',
  'context.cacheInput': 'Cached  ',
  'context.cacheHitRate': 'Hit rate',

  // ── Settings persistence ──
  'settings.saveFailed': 'Failed to save settings: ',
} as const;

// English mirror of zh-CN/commandBlocking.ts — keys must stay identical (type-enforced by en-US.ts).
// 规范见 doc/frontend/i18n_spec.md。
export default {
  // ── Settings row (GeneralTab) ──
  settingsTitle: 'Command blocking',
  settingsDesc:
    'When enabled, commands containing a blocking rule are stopped before execution. ' +
    'Rules are personal and apply to all of your workspaces.',
  manageRules: 'Manage rules',

  // ── Rules dialog (CommandBlockingDialog) ──
  title: 'Command blocking',
  statusOn: 'Enabled',
  statusOff: 'Disabled',
  loading: 'Loading...',
  matchHint:
    'Matching: case-insensitive text containment. A command is blocked when it contains any rule.',
  sharedHint: 'Rules are personal and shared across all of your workspaces.',
  rulesLabel: 'Blocking rules',
  rulesPlaceholder: 'One rule per line, e.g. rm -rf',
  useRecommended: 'Use recommended rules',
  recommendedHint:
    'Recommended rules are only appended to the draft and take effect after you save.',
  noRecommended: 'No recommended rules available',

  // ── Errors (kept and shown inline, no alert/confirm) ──
  loadFailedHint: 'Failed to load. Saving is unavailable.',
  toggleFailed: 'Failed to update the toggle',
  saveFailed: 'Failed to save',
  invalidResponse: 'The server returned invalid command blocking settings. Please retry.'
} as const;

// English mirror of zh-CN/commandBlocking.ts — keys must stay identical (type-enforced by en-US.ts).
// 规范见 doc/frontend/i18n_spec.md。
export default {
  // ── Settings row (GeneralTab) ──
  settingsTitle: 'Command blocking',
  settingsDesc: 'Stop agents from running commands that match your rules',
  manageRules: 'Manage rules',

  // ── Rules dialog (CommandBlockingDialog) ──
  title: 'Command blocking',
  statusOn: 'Enabled',
  statusOff: 'Disabled',
  loading: 'Loading...',
  summary:
    '{status}. Rules apply to all of your workspaces; a command is blocked when it contains any rule (case-insensitive).',
  rulesLabel: 'Blocking rules',
  rulesDesc: 'Which command content gets blocked',
  rulesPlaceholder: 'One rule per line, e.g. rm -rf',
  useRecommended: 'Add recommended rules',
  recommendedHint: 'Added {n} recommended rules. They take effect after you save.',

  // ── Errors (kept and shown inline, no alert/confirm) ──
  loadFailedHint: 'Failed to load. Saving is unavailable.',
  toggleFailed: 'Failed to update the toggle',
  saveFailed: 'Failed to save',
  invalidResponse: 'The server returned invalid command blocking settings. Please retry.'
} as const;

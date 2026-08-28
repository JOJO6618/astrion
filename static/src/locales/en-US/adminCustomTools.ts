// Locale namespace: adminCustomTools (en-US mirror, keys must match zh-CN/adminCustomTools.ts exactly).
export default {
  gateDescription: 'Enter your admin secondary password to manage custom tools.',
  // —— SecondaryGate (shared secondary-password gate used by 4 admin pages) ——
  gate: {
    notConfiguredTitle: 'Secondary password not set',
    notConfiguredDesc:
      'Sensitive admin actions (invites, password management, policy configuration) are protected by a secondary password. The server has not been configured yet — set it up first:',
    step1Text: 'Generate a password hash on the server:',
    step1Code:
      'python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash(\'your secondary password\'))"',
    step2TextPre: 'Write the output to the',
    step2TextPost: 'section of settings.json:',
    step3Text: 'Restart the service, then click "Recheck" below.',
    recheck: 'Recheck',
    rechecking: 'Checking...',
    title: 'Enter admin secondary password',
    passwordPlaceholder: 'Secondary password',
    verifying: 'Verifying...',
    confirmEnter: 'Confirm & enter',
    defaultDescription: 'Secondary verification is required to view sensitive data.',
  },
  // —— CustomToolsGuideApp (developer guide page) ——
  guide: {
    title: 'Developer Guide',
    subtitle: 'A complete walkthrough for writing, organizing, and debugging custom tools.',
  },
  renderFailed: 'Failed to render guide',
  // —— CustomToolsApp (tool list + editor + modals) ——
  backToList: 'Back to list',
  loadFailedWith: 'Failed to load: {message}',
  main: {
    title: 'Custom Tools Administration',
    subtitle:
      'One folder per tool; the three layers live in separate files. Visible and callable by admins only.',
    newTool: 'New tool',
    refreshList: 'Refresh list',
    viewGuide: 'View guide',
    backToMonitor: 'Back to monitor',
    policyConfig: 'Policy',
    toolList: 'Tools',
    toolCount: '{count} tools',
    emptyTools: 'No custom tools yet',
    noDescription: 'No description',
    params: 'Params: {count}',
    timeout: 'Timeout: {seconds}s',
    noReturnLayer: '(no return layer)',
    toolNotFound: 'Tool {id} not found',
  },
  editor: {
    noDescription: 'No description',
    hint1Pre: 'Hint: dicts/sets in execution.py must be wrapped in',
    hint1Post: 'to avoid template replacement.',
    hint2: 'No restart needed after saving — custom tools are reloaded automatically.',
    loadingTool: 'Loading tool...',
  },
  create: {
    title: 'Create New Tool',
    toolIdLabel: 'Tool ID (lowercase / underscore):',
    descriptionLabel: 'Description:',
    creating: 'Creating...',
    create: 'Create',
    idRequired: 'Please enter a tool ID',
    idInvalid: 'Tool ID must start with a letter and may contain letters, digits, _ and -',
    failed: 'Failed to create tool',
  },
  delete: {
    title: 'Confirm Delete',
    confirmPre: 'Delete tool',
    confirmPost: '? This action cannot be undone.',
    deleting: 'Deleting...',
    failed: 'Failed to delete tool',
  },
  save: {
    failed: 'Failed to save {name}',
  },
} as const;
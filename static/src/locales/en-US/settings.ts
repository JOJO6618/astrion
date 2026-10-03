// Locale namespace: settings (en-US mirror, keys must match zh-CN/settings.ts exactly).
// Used by: static/src/components/settings/ (full-screen settings shell and section tabs)
// Note: section body copy (setting rows) reuses the personalization namespace (shared tab components);
// this namespace only covers the shell / navigation / page headers.
export default {
  // ── Shell ──
  title: 'Settings',
  backAriaLabel: 'Back to chats',
  navAriaLabel: 'Switch settings sections',

  // ── Nav groups ──
  groupModel: 'Model',
  groupSystem: 'System',
  groupAgents: 'Agents',
  groupInterface: 'Interface',
  groupAdmin: 'Administration',

  // ── Nav items ──
  navProviders: 'Providers',
  navModels: 'Models',
  navModelPref: 'Model Preferences',
  navCodex: 'Codex',
  navGeneral: 'General',
  navWorkspace: 'Workspace',
  navSearch: 'Web Search',
  navTools: 'Tools',
  navContext: 'Context & Memory',
  navFiles: 'Files',
  navVoice: 'Voice',
  navSubAgents: 'Sub-agents',
  navReviewAgents: 'Review Agents',
  navAppearance: 'Appearance',
  navAdmin: 'Admin',

  // ── Section page headers (title + description) ──
  providersTitle: 'Providers',
  providersDesc:
    'Connect model providers. Enter an API key to automatically fetch all available models from that provider—no per-model setup required.',
  modelsTitle: 'Models',
  modelsDesc:
    'All registered models. Provider models update automatically with sync; you can also add fully custom models manually.',
  modelPrefTitle: 'Model Preferences',
  modelPrefDesc: 'Default model, run mode, and reasoning effort.',
  codexTitle: 'Codex',
  codexDesc: 'Codex subscription sign-in, proxy settings, and usage & credits management.',
  generalTitle: 'General',
  generalDesc: 'Basic behavior for conversations and the app.',
  runDataTitle: 'Runtime data directory',
  runDataDesc:
    'Choose where the desktop app stores conversations, user data, and runtime settings. After restart, data migration runs before the app opens and shows a progress window.',
  runDataEnvLocked:
    'ASTRION_DESKTOP_DATA_ROOT is set, so in-app directory changes are disabled. Change or remove this environment variable to use this setting.',
  runDataPathLabel: 'Data directory path',
  runDataPathPlaceholder: 'Enter a new data directory path',
  runDataBrowse: 'Choose in Finder',
  runDataCurrentPath: 'Current directory',
  runDataModeLabel: 'Directory switch method',
  runDataConfirmTitle: 'Confirm data directory change',
  runDataCancel: 'Cancel',
  runDataConfirm: 'Confirm',
  runDataMigrate: 'Migrate current data',
  runDataStartFresh: 'Use target directory data',
  runDataMigrateDesc:
    'Copy all current contents. The target must contain no data; Finder-created .DS_Store files are ignored. Wait for running tasks to finish first. The original directory is kept.',
  runDataFreshDesc:
    'Do not copy old data. Use existing data in the target directory, or start with an empty directory.',
  runDataApply: 'Apply directory settings',
  runDataWorking: 'Working…',
  runDataRestartRequired: 'Directory setting saved. Restart to apply it.',
  runDataRestartNow: 'Restart now',
  runDataRestarting: 'Restarting…',
  runDataError: {
    environment_locked:
      'The environment variable controls the data directory. Change or remove ASTRION_DESKTOP_DATA_ROOT first.',
    path_required: 'Enter a data directory path first.',
    same_directory: 'The new directory is the same as the current one.',
    overlapping_directories:
      'The new directory cannot contain or be contained by the current directory.',
    target_not_directory: 'The target path is not a usable directory.',
    target_not_empty:
      'The migration target contains existing data. Choose “Use target directory data” to use it, or select an empty directory.',
    verification_failed:
      'The copied data did not pass verification. The setting was not changed and the original directory is kept.',
    restart_failed: 'Could not restart the app. Quit and reopen it manually when convenient.',
    request_failed: 'The operation failed. Check directory permissions and try again.',
    unknown: 'The operation failed. Check directory permissions and try again.'
  },
  workspaceTitle: 'Workspace',
  workspaceDesc: 'Default permissions, sandbox environment, and versioning defaults.',
  searchTitle: 'Web Search',
  searchDesc: 'Tavily search API key and webpage extraction policy.',
  toolsTitle: 'Tools',
  toolsDesc: 'Tool approvals, Skills, and tool loading policy.',
  contextTitle: 'Context & Memory',
  contextDesc: 'Conversation context, project memory injection, and compression policy.',
  filesTitle: 'Files',
  filesDesc: 'Image and file handling.',
  voiceTitle: 'Voice',
  voiceDesc: 'Voice model download and status.',
  subAgentsTitle: 'Sub-agents',
  subAgentsDesc: 'Sub-agent models, compression threshold, and role management.',
  reviewAgentsTitle: 'Review Agents',
  reviewAgentsDesc: 'Models and runtime parameters for review agents.',
  appearanceTitle: 'Appearance',
  appearanceDesc: 'Theme, interface language, and display options.',
  adminTitle: 'Admin',
  adminDesc: 'Deployment-level management entries (open in a new window).',

  // ── Providers section ──
  providersConnectedSection: 'Connected',
  providersCatalogSection: 'Add provider',
  providersCatalogDesc: 'Pick a provider and enter an API key; its model list syncs automatically.',
  protocolNoteMultiPartial:
    'Multi-protocol gateway: Chat Completions and Responses models work out of the box; Anthropic / Google protocol models are registered and listed, but calls are not supported in this version.',
  protocolNoteMultiPartialCount: '{count} of them use protocols not yet supported for calls.',
  localBadge: 'Local',
  providerConnected: 'Connected',
  providerModelsCount: '{count} models available',
  providerRefresh: 'Refresh models',
  providerDisconnect: 'Disconnect',
  providerDisconnecting: 'Disconnecting…',
  providerDelete: 'Delete',
  providerDeleting: 'Deleting…',
  providerConnect: 'Connect',
  providerConnecting: 'Connecting…',
  providerLastSync: 'Last synced {time}',
  refreshSuccess: 'Refreshed. {count} models in total.',
  disconnectConfirmTitle: 'Disconnect provider',
  disconnectConfirmMessage:
    'After disconnecting {name}, all of its models are removed from the model list immediately.',
  deleteProviderConfirmTitle: 'Delete custom provider',
  deleteProviderConfirmMessage:
    'After deleting {name}, its credentials and all of its models are removed immediately.',
  customBadge: 'Custom',
  connectSuccess: 'Connected. Fetched {count} models.',
  connectDialogTitle: 'Connect {name}',
  connectApiDesc: 'Enter an API key to automatically fetch all available models from {url}.',
  connectKeyHint: 'Get an API key at:',
  connectApiKeyLabel: 'API key',
  connectApiKeyPlaceholder: 'sk-...',
  connectApiKeyHint:
    'The key is stored only in the local runtime data directory (0600 permissions) and is never sent back to the frontend in plain text.',
  connectSubmit: 'Test & connect',
  connectTesting: 'Connecting…',
  codexConnectDesc:
    'Sign in with a ChatGPT Pro/Plus subscription (shares the Codex sign-in flow); models included in the subscription are fetched automatically after authorization.',
  codexConnectSuccess: 'Authorized. ChatGPT subscription connected.',
  codexBrowserAuth: 'Browser authorization',
  codexBrowserAuthDesc: 'Open the browser to authorize with your ChatGPT account',
  codexDeviceAuth: 'Headless mode',
  codexDeviceAuthDesc:
    'Show a device code to enter in any browser (for remote servers without a browser)',
  customProviderTitle: 'Custom provider',
  customProviderBadge: 'OpenAI compatible',
  customProviderDesc: 'Add any OpenAI-compatible provider via a base URL',
  customProviderNote:
    'Configure an OpenAI-compatible provider. After saving, the model list is fetched automatically from the given address.',
  customProviderApiKeyHint:
    'Optional. Leave empty if you manage authentication via custom headers.',
  fieldProviderId: 'Provider ID',
  fieldProviderIdHint: 'Use lowercase letters, digits, hyphens, or underscores',
  fieldDisplayName: 'Display name',
  fieldDisplayNamePlaceholder: 'My AI provider',
  fieldBaseUrl: 'Base URL',
  fieldHeaders: 'Headers (optional)',
  addHeaderRow: '+ Add header',

  // ── Models section ──
  addCustomModel: 'Add custom model',
  addCustomProvider: 'Add custom provider',
  modelsEmpty: 'No registered models yet.',
  modelGroupProviderBadge: 'Provider synced',
  modelGroupCustom: 'Custom',
  modelGroupCustomBadge: 'Manually configured',
  modelVisibleSwitch: 'Visible to me in model pickers (per-user setting)',
  modelsEditHint:
    'Provider-synced models cannot be edited (metadata updates with sync); only custom models can be edited or deleted. Visibility toggles are per-user settings.',
  capImage: 'Image',
  capVideo: 'Video',
  capThinking: 'Thinking',
  editLabel: 'Edit',
  customModelAddTitle: 'Add custom model',
  customModelEditTitle: 'Edit custom model',
  customModelNote:
    'Configure a model fully by hand (equivalent to writing one entry in custom_models.json).',
  customModelSaved: 'Custom model saved.',
  customModelDeleted: 'Custom model deleted.',
  customModelDeleteConfirmTitle: 'Delete custom model',
  customModelDeleteConfirmMessage: 'Delete {name}? This cannot be undone.',
  fieldModelName: 'Model name (registry key)',
  fieldModelNameHint: 'Slashes, backslashes, and whitespace are not allowed',
  fieldModelDisplayNamePlaceholder: 'Qwen3 Coder on internal gateway',
  fieldModelDesc: 'Description',
  fieldModelDescPlaceholder: 'Extra description for the model (optional)',
  fieldApiUrl: 'API URL',
  fieldApiKeyEnvPlaceholder: 'sk-... or {example}',
  fieldApiKeyEnvHint: 'Supports {example} environment variable references.',
  fieldModelId: 'Model ID',
  fieldMultimodal: 'Multimodal capability',
  customModelCapNone: 'Text only',
  customModelCapImageVideo: 'Image + video',
  fieldReasoning: 'Reasoning capability',
  customModelReasoningFast: 'Fast only',
  customModelReasoningThinking: 'Thinking only',
  customModelReasoningBoth: 'Fast + thinking',
  fieldReasoningEffort: 'Reasoning effort levels',
  fieldModelEnabled: 'Enable this model',
  fieldContextWindow: 'Context window',
  fieldMaxOutput: 'Max output tokens',

  // ── Placeholder sections ──
  placeholderHint:
    'This section will be available in a future iteration; only a placeholder is shown for now.'
} as const;

// Locale namespace: utils (en-US mirror, keys must match zh-CN/utils.ts exactly).
// Spec: doc/frontend/i18n_spec.md. Used by: static/src/utils/formatters.ts,
// static/src/utils/showHtmlFullscreen.ts, static/src/composables/useMarkdownRenderer.ts,
// static/src/composables/useLegacySocket.ts
export default {
  // ── show_html fullscreen preview (showHtmlFullscreen.ts) ──
  fullscreenPreview: 'Fullscreen preview',
  // TODO(common): candidate common word
  refresh: 'Refresh',
  closeEsc: 'Close (Esc)',
  closeFullscreenPreview: 'Close fullscreen preview',

  // ── Markdown rendering (useMarkdownRenderer.ts) ──
  copyCode: 'Copy code',

  // ── Quota (formatters.ts) ──
  quotaTypeThinking: 'Thinking model',
  quotaTypeSearch: 'Search',
  quotaTypeFast: 'Standard model',
  quotaExhaustedResetIn: '{type} quota exhausted, resets at {time}',

  // ── WebSocket events (useLegacySocket.ts) ──
  contextTooLong: 'Conversation context is too long',
  contextNearLimit: 'The current conversation context is nearing its limit. Consider using compression.',
  videoReading: 'Reading video',
  videoReadingSlow: 'Reading the video may take a while, please be patient',
  unknownFile: 'Unknown file',
  subAgentItem: 'Sub-agent {id} ({summary})',
  noDescription: 'No description',
  waitingForSubAgents: '⏳ Waiting for {n} background sub-agents to complete: {list}',
  // TODO(common): candidate common word (list separator)
  listSeparator: ', ',
  unknownErrorOccurred: 'An unknown error occurred',
  requestRecord: 'Request log: {path}',
  endpoint: 'Endpoint: {endpoint}',
  model: 'Model: {model}',
  conversationIdLabel: 'Conversation ID: {id}',
  taskIdLabel: 'Task ID: {id}',
  apiError: 'API error',
  apiErrorWithCode: 'API error {code}',
  systemStatus: 'System status:\n{data}',
  commandFailed: 'Command failed: {message}',
} as const;
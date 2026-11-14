// Locale namespace: storageStats (en-US)
// Consumers: static/src/components/settings/tabs/StorageTab.vue and tabs/storage/ children (Settings → Storage)
export default {
  // ── State ──
  loading: 'Measuring…',
  loadFailed: 'Measurement failed: {error}',
  refresh: 'Refresh',
  scanning: 'Measuring…',
  neverScanned: 'Not measured yet',
  lastScan: 'Last measured {time}',
  unsupported: 'Storage stats are available in desktop / host mode only.',
  empty: 'Nothing to measure.',
  // ── Overview ──
  totalUsed: 'Astrion uses',
  // ── Categories ──
  catBackups: 'Backups & snapshots',
  catLogs: 'Logs',
  catMcpProfiles: 'Browser / MCP',
  catAgentConversations: 'Agent chats',
  catMedia: 'Chat media',
  catSubAgents: 'Sub-agents',
  catChat: 'Chat conversations',
  catOther: 'Other',
  // ── Cleanup ──
  cleanupTitle: 'Clean up',
  cleanupDesc: 'You will be asked to confirm. Only logs and old conversation backups can be cleaned; other categories are read-only.',
  backupRowTitle: 'Conversation backups & snapshots',
  backupRowDesc: 'Removes whole conversation backups. Chats stay intact; only their historical checkpoints are removed.',
  logRowTitle: 'Logs',
  logRowDesc: 'Clears every log file in the data directory; logs still being written are truncated.',
  keepLabel: 'Keep last',
  keepDays: '{n} days',
  keepCustom: 'Custom…',
  keepDaysUnit: 'days',
  customPlaceholder: 'Days',
  customApply: 'OK',
  customHint: '1–3650 days',
  willRemove: 'Will remove {count} backups · about {size}',
  nothingToRemove: 'Nothing older than that',
  cleanButton: 'Clean',
  cleanLogsButton: 'Clear all',
  cleaning: 'Cleaning…',
  // ── Confirmation ──
  confirmBackupTitle: 'Clean old conversation backups',
  confirmBackupMessage: 'This removes {count} conversation backups last updated more than {days} days ago, freeing about {size}.',
  confirmBackupWarning: 'This cannot be undone. Chat messages are not affected; only historical checkpoints are removed.',
  confirmLogsTitle: 'Clear logs',
  confirmLogsMessage: 'This clears all log files, freeing about {size}.',
  confirmLogsWarning: 'Logs still being written will be truncated; the backend keeps running.',
  confirmButton: 'Delete',
  cancelButton: 'Cancel',
  // ── Result ──
  cleanBackupDone: 'Removed {count} backups, freed {size}',
  cleanLogsDone: 'Cleared logs, freed {size}',
  cleanFailed: 'Cleanup failed: {error}',
  cleanPartial: '{count} items could not be removed'
};

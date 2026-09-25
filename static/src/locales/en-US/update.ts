// Locale namespace: update (en-US mirror, keys must match zh-CN/update.ts exactly).
// Used by: static/src/stores/desktopUpdate.ts,
//   static/src/components/sidebar/SoftwareUpdateDialog.vue, ConversationSidebar.vue
export default {
  // Sidebar entry
  entryTitle: 'Software Update',
  newVersionBadge: 'Update available',

  // Dialog
  dialogTitle: 'Software Update',
  checking: 'Checking for updates…',
  currentVersion: 'Current version',
  latestVersion: 'Latest version',
  upToDate: 'You are up to date',
  newVersionAvailable: 'New version available',
  releaseNotes: 'Release notes',
  noReleaseNotes: 'No release notes',
  publishedAt: 'Published {date}',
  updateNow: 'Update & Restart',
  later: 'Later',
  recheck: 'Check again',

  // Install progress (state mirrors the Rust control bridge)
  downloading: 'Downloading update…',
  installing: 'Installing, the app will restart automatically…',
  restarting: 'Install complete, restarting…',
  updateFailed: 'Update failed',

  // Check failure
  checkFailed: 'Failed to check for updates',
};

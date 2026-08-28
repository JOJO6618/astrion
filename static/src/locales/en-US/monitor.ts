// Locale namespace: monitor (en-US mirror, keys must match zh-CN/monitor.ts exactly).
export default {
  // Desktop app icon labels (structural short labels)
  appBrowser: 'Browser',
  appTerminal: 'Terminal',
  appCommand: 'Command Line',
  appPython: 'Python',
  appMemory: 'Memory',
  appTodo: 'To-dos',
  appSubagent: 'Sub-agent',

  // Terminal session naming and hints
  terminalName: 'Terminal {n}',
  terminalTitle: 'Terminal',
  terminalReset: 'Terminal reset',
  terminalEmptyHint: 'No terminals yet — click + to create one',

  // Bubbles and generic status
  thinkLabel: 'Thinking',
  waitingReply: 'Waiting for reply',
  toolError: 'Tool failed',

  // Browser search
  browserReady: 'Ready to search...',
  browserSearching: 'Searching...',
  browserOpening: 'Opening page...',
  defaultSearchQuery: 'Search content',
  searchStatus: 'Searching',
  searchFailed: 'Search failed',
  searchCompleted: 'Search complete, results loaded',
  searchIncomplete: 'Search incomplete',
  noSearchResults: 'No search results',
  searchResultFallback: 'Search result',

  // Web extraction
  extractTitle: 'Web extract',
  extractWaiting: 'Waiting to extract',
  extractInProgress: 'Extracting...',
  extractFailed: 'Extraction failed',
  extractStateFailed: 'Extraction failed',
  extractStateComplete: 'Extraction complete',
  extractFailedLabel: 'Extraction failed',
  extractFailedItem: '⚠️ {url}: {error}',
  extractSectionTitle: 'Web summary',
  extractSectionItem: 'Web page {n}',
  noExtractionSummary: 'No summary returned',

  // File operations
  statusCreatingFolder: 'Creating folder',
  defaultFolderName: 'New folder',
  createFolderFailed: 'Failed to create folder',
  statusCreatingFile: 'Creating file',
  defaultFileName: 'New file',
  createFileFailed: 'Failed to create file',
  statusRenaming: 'Renaming',
  renameFailed: 'Rename failed',
  statusDeletingFile: 'Deleting file',
  statusEditing: 'Editing',
  editFailed: 'Failed to edit file',
  editorEmpty: '(File is empty)',

  // Command line
  commandTitle: 'Command',
  statusCallingTool: 'Calling {tool}',
  commandFailed: 'Command failed',
  commandDone: 'Command finished',
  commandSent: 'Command sent',

  // Reader / focus
  statusReading: 'Reading',
  readerModeSearch: 'File search',
  readerModeExtract: 'Extract snippet',
  readingContent: 'Reading file content...',
  readFailed: 'Read failed',
  noDisplayContent: 'No displayable content',
  defaultDocPath: 'Document',
  defaultFilePath: 'File',
  statusFocusingFile: 'Focusing file',
  focusFailed: 'Failed to focus file',
  loadingContent: 'Loading file content...',
  focusedReady: 'File focused — view it in the focus panel',
  statusProcessing: 'Processing',
  unfocusFailed: 'Failed to unfocus',
  unfocused: 'Unfocused',
  statusExtracting: 'Extracting',
  ocrFailed: 'OCR failed',
  ocrReady: 'OCR content ready',

  // Memory / to-dos
  memorySynced: 'Memory synced',
  statusSyncingMemory: 'Syncing memory',
  defaultMemory: 'New memory',
  statusUpdatingTodo: 'Updating to-dos',
  defaultTodoSummary: 'Todo summary',
  todoEmptySummary: 'No summary',
  statusAdjustingTodo: 'Adjusting to-dos',
  statusFinishingTask: 'Finishing task',
  statusRemovingTodo: 'Removing to-do',

  // Terminal scenes / waiting
  statusResettingTerminal: 'Resetting terminal',
  statusOpeningTerminal: 'Opening terminal',
  statusCallingTerminalInput: 'Calling terminal_input',
  statusGettingTerminal: 'Fetching terminal',
  statusWaiting: 'Waiting',
  statusSavingWeb: 'Saving web page',
  waitOverrun: 'Waiting +{n}s{dots}',

  // Reader fallbacks
  readerEmptyFallback: 'No content',
  noVisibleContent: 'Nothing to display',
} as const;
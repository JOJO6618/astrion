// 文案命名空间：monitor（zh-CN 源语言）
// 规范见 doc/frontend/i18n_spec.md。由迁移任务填充，en-US 同名文件必须保持 key 完全一致（tsc 强制）。
export default {
  // 桌面应用图标标签（结构性短标签）
  appBrowser: '浏览器',
  appTerminal: '终端',
  appCommand: '命令行',
  appPython: 'Python',
  appMemory: '记忆',
  appTodo: '待办事项',
  appSubagent: '子代理',

  // 终端会话命名与提示
  terminalName: '终端{n}',
  terminalTitle: '终端',
  terminalReset: '终端已重置',
  terminalEmptyHint: '尚未创建终端，点击 + 新建',

  // 气泡与通用状态
  thinkLabel: '思考中',
  waitingReply: '等待回复',
  toolError: '工具执行错误',

  // 浏览器搜索
  browserReady: '准备搜索...',
  browserSearching: '正在搜索...',
  browserOpening: '正在打开网页...',
  defaultSearchQuery: '搜索内容',
  searchStatus: '正在搜索',
  searchFailed: '搜索失败',
  searchCompleted: '搜索完成，已加载结果',
  searchIncomplete: '搜索未完成',
  noSearchResults: '暂无搜索结果',
  searchResultFallback: '搜索结果',

  // 网页提取
  extractTitle: '网页提取',
  extractWaiting: '等待提取',
  extractInProgress: '提取中...',
  extractFailed: '网页提取失败',
  extractStateFailed: '提取失败',
  extractStateComplete: '提取完成',
  extractFailedLabel: '提取失败',
  extractFailedItem: '⚠️ {url}: {error}',
  extractSectionTitle: '网页摘要',
  extractSectionItem: '网页 {n}',
  noExtractionSummary: '未返回任何摘要内容',

  // 文件操作
  statusCreatingFolder: '正在创建文件夹',
  defaultFolderName: '新建文件夹',
  createFolderFailed: '创建文件夹失败',
  statusCreatingFile: '正在创建文件',
  defaultFileName: '新建文件',
  createFileFailed: '创建文件失败',
  statusRenaming: '正在重命名',
  renameFailed: '重命名失败',
  statusDeletingFile: '正在删除文件',
  statusEditing: '正在编辑',
  editFailed: '文件编辑失败',
  editorEmpty: '（文件当前为空）',

  // 命令行
  commandTitle: '命令行',
  statusCallingTool: '调用 {tool}',
  commandFailed: '命令执行失败',
  commandDone: '命令执行完成',
  commandSent: '命令已发送',

  // 阅读 / 聚焦
  statusReading: '正在阅读',
  readerModeSearch: '文件搜索',
  readerModeExtract: '提取片段',
  readingContent: '正在读取文件内容...',
  readFailed: '阅读失败',
  noDisplayContent: '未返回可显示内容',
  defaultDocPath: '文档',
  defaultFilePath: '文件',
  statusFocusingFile: '正在聚焦文件',
  focusFailed: '聚焦文件失败',
  loadingContent: '正在加载文件内容...',
  focusedReady: '文件已聚焦，可在右侧聚焦面板查看',
  statusProcessing: '正在处理',
  unfocusFailed: '取消聚焦失败',
  unfocused: '已取消聚焦',
  statusExtracting: '正在提取',
  ocrFailed: 'OCR 失败',
  ocrReady: 'OCR 内容就绪',

  // 记忆 / 待办
  memorySynced: '记忆已同步',
  statusSyncingMemory: '正在同步记忆',
  defaultMemory: '新记忆',
  statusUpdatingTodo: '正在更新待办',
  defaultTodoSummary: '待办摘要',
  todoEmptySummary: '暂无摘要',
  statusAdjustingTodo: '正在调整待办',
  statusFinishingTask: '正在完成任务',
  statusRemovingTodo: '正在移除待办',

  // 终端场景 / 等待
  statusResettingTerminal: '正在重置终端',
  statusOpeningTerminal: '打开终端',
  statusCallingTerminalInput: '调用 terminal_input',
  statusGettingTerminal: '正在获取终端',
  statusWaiting: '正在等待',
  statusSavingWeb: '正在保存网页',
  waitOverrun: '等待中 +{n}s{dots}',

  // 阅读器兜底
  readerEmptyFallback: '暂无内容',
  noVisibleContent: '未返回可视内容',
} as const;
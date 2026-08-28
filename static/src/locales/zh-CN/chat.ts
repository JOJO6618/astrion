// chat 文案包（第二波迁移新增命名空间，组4：聊天区核心组件）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
//
// TODO(common): 候选公共词（语义通用、可能在多个命名空间复用，由主任务归并到 common）：
//   - expand「展开」/ collapse「收起」/ branch「分支」
//   - generating「生成中…」/ executing「执行中...」/ preview「预览」
//   - justNow / secondsAgo / minutesAgo / hoursAgo / timeDate（相对时间族，adminApi/adminDashboard 已有 justNow 先例）
//   - more「更多」/ allExpanded「已展开全部」
//   - rename「重命名」/ deleteFile「删除文件」/ wait「等待」
//   - networkError「网络错误」
//   - terminal「终端」/ commandLine「命令行」/ memory「记忆」/ todos「待办事项」/ newFile「新建文件」/ newFolder「新建文件夹」
//   - userHeader* 消息来源头部标签族 / fileType* 文件类型标签族
export default {
  // —— 用户消息气泡：折叠 / 复制 / 分支操作 ——
  expand: '展开',
  collapse: '收起',
  branch: '分支',
  userDefaultName: '用户',
  subAgentName: '子智能体',
  generating: '生成中…',

  // —— 用户消息头部来源标签 ——  // —— 用户消息头部来源标签 ——
  userHeaderGuide: '引导',
  userHeaderGoal: '目标',
  userHeaderReview: '审核',
  userHeaderCompression: '压缩',
  userHeaderNotify: '通知',

  // —— 简略信息（compact brief）概要标签 ——
  briefGoalReview: '目标审核完成',
  briefSubAgent: '后台子智能体完成',
  briefBackgroundCommand: '后台指令完成',
  briefCompression: '对话压缩完成',
  briefGoal: '目标进行中',
  briefGuidance: '运行中引导',
  briefNotify: '系统通知',
  briefSystem: '系统消息',

  // —— 子智能体完成通知（系统 action 标签兜底文案） ——
  subAgentDoneLabel: '子智能体{n} 任务完成',

  // —— Astrion 头部工作计时状态 ——
  workInProgress: '工作中 {duration}',
  workCompleted: '工作完成 {duration}',

  // —— 思考块状态 ——
  thinking: '思考过程',
  thinkingRunning: '正在思考...',

  // —— 文件追加（append / append_payload） ——
  targetFile: '目标文件',
  appendDone: '文件追加完成',
  appendSuccess: '已写入 {path} 的追加内容（内容已保存至文件）',
  appendFailed: '向 {path} 写入失败，内容已截获供后续修复。',
  appendWarning: '未检测到结束标记，请根据提示继续补充。',
  appendWarningFollow: '未检测到结束标记，请按提示继续补充。',
  linesCount: '· 行数 {n}',
  bytesCount: '· 字节 {n}',

  // —— 系统消息 / 快捷导航 ——
  systemMessage: '系统消息 (role: {role})',
  quickNavAria: '用户输入快捷跳转',
  emptyInput: '（空输入）',
  noReply: '（暂无回复）',

  // —— 工具执行摘要（极简模式 MinimalBlocks） ——
  executing: '执行中...',
  callingTool: '正在调用 {name}...',
  toolCompleted: '工具执行完成',
  executingTool: '正在执行工具...',
  runTool: '执行工具',
  summaryRead: '读取了 {n} 个文件',
  summaryCommand: '运行了 {n} 个指令',
  summaryEdit: '编辑了 {n} 次文件',
  summarySearch: '搜索了 {n} 次',
  summaryWebpage: '查看了 {n} 次网页',
  summaryMcp: '执行了 {n} 次 MCP 工具',
  summaryWorkflowActivate: '激活了 {n} 个工作流',
  summaryWorkflowAdvance: '推进了 {n} 次工作流进度',
  summaryWorkflow: '执行了 {n} 次工作流操作',
  summaryTodoCreate: '创建了 {n} 次待办',
  summaryTodoUpdate: '更新了 {n} 次待办',
  summaryMemoryUpdate: '更新了 {n} 次记忆',
  summaryMemoryRead: '查看了 {n} 次记忆',
  summaryConversation: '回顾了 {n} 次对话',
  summarySubAgent: '创建了 {n} 个子智能体',
  summarySubAgentManage: '管理了 {n} 次子智能体',
  summaryWait: '等待了 {n} 次',
  summaryAsk: '询问了 {n} 次',
  summaryPlan: '提交了 {n} 次计划',
  summarySkill: '归档了 {n} 次技能',
  summarySettings: '更新了 {n} 次个性化设置',
  summaryEasterEgg: '触发了 {n} 次彩蛋',
  summaryOther: '执行了 {n} 次其他操作',

  // —— 堆叠块（StackedBlocks「更多」头） ——
  more: '更多',
  allExpanded: '已展开全部',
  stepsTotal: '共 {n} 个步骤',
  stepsHidden: '{n} 个步骤折叠',

  // —— 代码块 ——
  copyCode: '复制代码',

  // —— 编辑摘要卡片（EditSummaryCard） ——
  filesEdited: '已编辑 {n} 个文件',
  fileChanges: '文件变更：{path}',
  diffTruncated: '内容过长，已截断展示',
  diffEmpty: '暂无可展示的文本变更行',

  // —— 文件类型标签（FileChips） ——
  fileTypeDoc: '文档',
  fileTypeSheet: '电子表格',
  fileTypeSlides: '演示文稿',
  fileTypePdf: 'PDF',
  fileTypeText: '文本',
  fileTypeArchive: '压缩包',
  fileTypeCode: '代码',
  fileTypeGeneric: '文件',
  removeFile: '移除 {name}',

  // —— PDF 预览（PdfPreview） ——
  pdfLoading: 'PDF 加载中...',
  pdfLoadFailed: 'PDF 加载失败',
  pdfEmpty: 'PDF 内容为空',
  pdfRenderFailed: 'PDF 渲染失败',

  // —— 文件展示卡片（ShowFileCard） ——
  preview: '预览',
  fileEmpty: '文件内容为空',
  htmlPreviewNotice: '含外部资源引用，预览可能不完整',
  networkError: '网络错误',
  imageLoadFailed: '图片加载失败',
  csvTruncated: '仅显示前 {n} 行，完整内容请下载查看',

  // —— 虚拟监视器界面（VirtualMonitorSurface，与 monitor.* 互补） ——
  vmBrowserTitle: '多模态知识浏览器',
  vmBrowserReady: '准备搜索...',
  vmWebExtract: '网页提取',
  vmExtractWait: '等待提取',
  vmFile: '文件',
  vmTerminal: '终端',
  vmCommandLine: '命令行',
  vmReaderTitle: '阅读器',
  vmMemory: '记忆',
  vmTodo: '待办事项',
  vmWait: '等待',
  vmNewFile: '新建文件',
  vmNewFolder: '新建文件夹',
  vmReadFile: '阅读文件',
  vmEditFile: '编辑文件',
  vmRename: '重命名',
  vmDeleteFile: '删除文件',
  vmFocusFile: '聚焦文件',
  vmUnfocus: '取消聚焦',
  vmSaveWebpage: '保存网页',
  vmSaveSnapshot: '保存快照',
  vmResetTerminal: '重置终端',
  vmCloseTerminal: '关闭终端',

  // —— 监视器场景进行状态（progressMap 顶层映射表存 key，getSceneProgressLabel 调用时 t(key) 解析） ——
  // ⚠️ 中文必须保持「正在…」前缀结构：stores/monitor.ts 的 transformStatus 会对以「正在」开头的
  //    zh 文案 slice(2) 后拼装 t('stores.playbackStatus')（回放{label}），英文不命中前缀即原样透传。
  progressBrowserSearch: '正在搜索',
  progressWebExtract: '正在提取',
  progressWebSave: '正在保存',
  progressAppendFile: '正在编辑',
  progressModifyFile: '正在编辑',
  progressCreateFile: '正在创建',
  progressDeleteFile: '正在删除',
  progressReadFile: '正在读取',
  progressReader: '正在读取',
  progressFocus: '正在聚焦',
  progressUnfocus: '正在处理',
  progressRunCommand: '运行命令',
  progressTerminalSession: '打开终端',
  progressTerminalInput: '终端输入',
  progressTerminalSnapshot: '获取终端输出',
  progressMemoryUpdate: '正在同步记忆',
  progressTodoCreate: '正在更新待办',
  progressTodoUpdate: '正在更新待办',
  progressTodoFinish: '正在完成任务',
  progressTodoFinishConfirm: '正在确认任务',
  progressTodoDelete: '正在移除任务',
  progressWait: '正在等待',
  progressSleep: '正在等待',
  progressCreateFolder: '正在创建文件夹',
  progressRenameFile: '正在重命名',
  progressTerminalReset: '正在重置终端',
  progressTerminalSleep: '准备等待',
  progressTerminalRun: '终端运行中',
  progressOcr: '正在提取',
  progressMemory: '正在同步记忆',
  progressTodo: '正在管理待办',
  progressGenericTool: '调用工具',
} as const;
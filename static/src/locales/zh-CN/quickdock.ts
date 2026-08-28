// quickdock 文案包（第二波迁移新增命名空间，组G：快捷窗口/运行详情 + input 快速菜单族）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
//
// TODO(common): 候选公共词（语义通用、可能在多个命名空间复用，由主任务归并到 common）：
//   - more「更多」（chat 已有同义候选）
//   - default「默认」/ settings「设置」
//   - uploading「上传中...」/ compressing「压缩中...」/ searching「搜索中...」（进行中状态族）
//   - reviewing「审核中」/ enable「启用」/ disable「禁用」
//   - subAgent「子智能体」（与 chat.subAgentName 语义重复，待归并去重）
export default {
  // —— 窗口标题 ——
  subAgent: '子智能体',
  backgroundCommand: '后台指令',
  agentNamed: '子智能体 {id}',
  more: '更多',
  fileWindowTitle: '文件',
  todoWindowTitle: '待办事项',

  // —— QuickDock 全局 ⋯ 菜单 ——
  menuForceStop: '强制关闭',
  menuRevealInManager: '在文件管理器中打开',
  menuCopyPath: '复制路径',
  killForceStopFailed: '强制关闭失败',
  revealDetectAppsFailed: '检测可用应用失败',
  revealNoAppsFound: '未找到可用应用',
  revealOpenFileFailed: '打开文件失败',
  copiedRelativePath: '已复制相对路径',

  // —— 运行详情面板（RunnerDetailPanel） ——
  noProgress: '暂无进度',
  noOutput: '暂无输出',
  statusIdle: '空闲',
  statusCompleted: '已完成',
  statusFailed: '已失败',
  statusTimeout: '已超时',
  statusTerminated: '已终止',
  statusEnded: '已结束',
  contextTokensTitle: '上下文 {tokens} tokens',

  // —— 工具时间线（动作描述走整句插值） ——
  toolReadFile: '阅读 {path}',
  toolWriteFile: '写入文件 {path}',
  toolReadSkill: '阅读技能 {name}',
  toolSearch: '搜索 {query}',
  toolExtract: '提取 {url}',
  toolRunCommand: '运行命令 {command}',
  toolEditFile: '编辑 {path}',
  toolReadMedia: '读取媒体文件 {path}',
  toolStateDone: '完成',
  toolStateFailed: '失败',
  toolStateCalling: '调用中',
  toolStateProgress: '进行中',

  // —— 文件预览面板（FilePreviewPanel） ——
  resizeWidthHint: '拖拽调整宽度',
  previewTypeUnsupported: '该文件类型不支持预览',
  loadFailedHttp: '加载失败（HTTP {status}）',

  // —— 工作流窗口（WorkflowWindow） ——
  reviewing: '审核中',
  roundsLabel: '{n} 轮',
  workflowEndRow: '结束',

  // —— 推理强度滑块（EffortSlider） ——
  effortTitle: '推理强度',
  effortDefault: '默认',
  effortMoreEfficient: '更高效',
  effortMoreIntelligent: '更智能',

  // —— 文件引用菜单（FileAtMenu） ——
  atMenuAria: '文件引用',
  atMenuPicker: '在文件管理器中选中',
  atMenuPickerDesc: '选择本地文件',
  atMenuNoMatch: '没有匹配的文件',
  atMenuSearching: '搜索中...',

  // —— 快速菜单（QuickMenu） ——
  uploading: '上传中...',
  uploadFile: '上传文件',
  conversationReview: '对话回顾',
  sendImage: '发送图片',
  sendVideo: '发送视频',
  disableTools: '工具禁用',
  goalMode: '目标模式',
  goalDone: '完成',
  goalArmed: '已就绪',
  settingsMenu: '设置',
  toolSettingsSyncing: '正在同步工具状态...',
  noControllableTools: '暂无可控工具',
  lockedByAdmin: '被管理员锁定',
  toolDisable: '禁用',
  toolEnable: '启用',
  realtimeTerminal: '实时终端',
  usageStats: '用量统计',
  compressing: '压缩中...',
  compressConversation: '压缩对话',
  approvalPanel: '审批面板',
  pathAuthorization: '路径授权',
} as const;
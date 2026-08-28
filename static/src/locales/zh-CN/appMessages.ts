// 文案命名空间：appMessages（zh-CN 源语言）—— 第二波迁移组1（独占）
// 规范见 doc/frontend/i18n_spec.md。由迁移任务填充，en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：static/src/app/methods/message/{chat,runtimeQueue,send,systemCommand}.ts
//         static/src/app/methods/conversation/{action,load,state}.ts
//         static/src/app/methods/history.ts、static/src/app/methods/versioning.ts
export default {
  // ── 消息：清空/压缩对话（message/chat.ts） ──
  clearChatTitle: '清除对话',
  clearChatConfirmMessage: '确定要清除所有对话记录吗？该操作不可撤销。',
  clearChatConfirmText: '清除',
  cannotCompressTitle: '无法压缩',
  cannotCompressMessage: '当前没有可压缩的对话。',
  compressingTitle: '压缩中',
  compressingMessage: '对话正在压缩，请稍候...',
  compressionCompletedTitle: '压缩完成',
  compressionCompletedMessage: '已压缩较早的对话内容',
  compressionFailed: '压缩失败',
  compressionErrorTitle: '压缩对话异常',
  // TODO(common): 候选公共词
  sendFailedTitle: '发送失败',
  createTaskFailedMessage: '创建任务失败，请重试',
  createNewConversationFailedMessage: '创建新对话失败，请重试',

  // ── 消息：运行时队列（message/runtimeQueue.ts） ──
  cannotQueueTitle: '暂不可堆积',
  noActiveTaskMessage: '未检测到活跃任务，请稍后再试',
  queueMessageFailed: '堆积消息失败',
  queueFailedTitle: '堆积失败',
  // TODO(common): 候选公共词
  deleteFailed: '删除失败',
  guideMessageNotSent: '引导消息未发出，请稍后重试',
  cannotGuideTitle: '暂不可引导',
  submitGuideFailed: '提交引导失败',
  guideSetTitle: '已设为引导对话',
  guideSetMessage: '将在下一次工具结果后插入到当前对话',
  guideFailedTitle: '引导失败',

  // ── 消息：发送/停止（message/send.ts） ──
  autoCompressingTitle: '对话自动压缩中',
  autoCompressingBlockSend: '当前不可发送/停止，请等待压缩完成',
  compressionBlocksSend: '压缩完成后才能继续发送消息',
  textRequiredTitle: '需要文字消息',
  textRequiredMessage: '附加文件需随文字消息一起发送',
  subAgentRunningTitle: '后台子智能体运行中',
  subAgentRunningMessage: '请等待后台任务结束后再发送图片/视频',
  runningTextOnlyTitle: '运行中仅支持文本',
  runningTextOnlyMessage: '图片/视频请等待当前任务结束后发送',
  conversationRunningTitle: '当前对话正在运行',
  conversationRunningMessage: '请等待当前对话任务完成后再发送新消息；同工作区的其他对话可正常并行。',
  connectionLostTitle: '连接已断开',
  connectionLostMessage: '当前无法发送消息，请等待连接恢复后重试',
  uploadingTitle: '上传中',
  uploadingMessage: '请等待图片/视频上传完成后再发送',
  modelNoImageTitle: '当前模型不支持图片',
  modelNoImageMessage: '请切换到支持图片输入的模型再发送图片',
  modelNoVideoTitle: '当前模型不支持视频',
  modelNoVideoMessage: '请切换到支持视频输入的模型后再发送视频',
  noMixedMediaTitle: '请勿同时发送',
  noMixedMediaMessage: '视频与图片需分开发送，每条仅包含一种媒体',
  videoProcessingTitle: '视频处理中',
  videoProcessingMessage: '读取视频需要较长时间，请耐心等待',
  initializingBackupTitle: '正在初始化备份',
  initializingBackupMessage: '正在创建完整工作区快照，请稍候...',
  createConversationFailed: '创建对话失败',
  stopRequestedTitle: '停止请求已发送',
  stopRequestedMessage: '若主对话未停止，请稍候；后台任务可通过状态栏单独停止',
  autoCompressingBlockStop: '压缩进行中，当前不可停止任务',

  // ── 消息：系统命令（message/systemCommand.ts） ──
  commandEmpty: '命令不能为空',
  connectionUnavailable: '连接不可用',
  connectionUnavailableMessage: '当前无法执行命令，请稍后重试。',
  commandExecutionFailed: '命令执行失败',
  clearedTitle: '已清除',
  conversationCleared: '对话已清除',
  systemStatus: '系统状态',
  statusUpdatedTitle: '状态已更新',
  statusFetched: '已获取系统状态',
  commandFailedLabel: '命令失败',
  commandExecutedTitle: '命令已执行',
  commandDone: '完成',

  // ── 对话：动作（conversation/action.ts） ──
  // TODO(common): 候选公共词
  serverNotSuccessMessage: '服务器未返回成功状态',
  createConversationErrorTitle: '创建对话异常',
  createWorkspaceConversationErrorTitle: '创建工作区对话异常',
  deleteConversationTitle: '删除对话',
  deleteConversationConfirmMessage: '确定要删除这个对话吗？删除后无法恢复。',
  deleteConversationFailedTitle: '删除对话失败',
  deleteConversationErrorTitle: '删除对话异常',
  duplicateConversationTitle: '复制的对话',
  duplicateConversationFailedTitle: '复制对话失败',
  duplicateConversationErrorTitle: '复制对话异常',

  // ── 对话：加载（conversation/load.ts） ──
  loadConversationFailedTitle: '加载对话失败',
  loadConversationErrorTitle: '加载对话异常',

  // ── 版本控制（versioning.ts） ──
  // 标题「版本管理」复用 common.versioning，不在此重复定义
  versioningFetchStatusFailed: '获取版本状态失败',
  versioningLoadCheckpointsFailed: '加载版本点失败',
  versioningEnabledForNext: '已为下一次新对话开启版本管理',
  versioningDisabledForNext: '已取消下一次新对话的版本管理',
  versioningToggleFailed: '切换版本管理失败',
  versioningOn: '已开启',
  versioningOff: '已关闭',
  versioningSwitchFailed: '切换失败',
  versioningDetailParseFailed: '详情响应解析失败',
  versioningLoadDetailFailed: '加载详情失败',
  versioningScopeConversationOnly: '仅回溯对话',
  versioningScopeConversationAndWorkspace: '回溯对话和工作区',
  versioningModeCopy: '复制对话',
  versioningModeOverwrite: '覆盖当前对话',
  versioningRestoreConfirmTitle: '确认回溯',
  versioningRestoreConfirmMessage: '将{scope}到输入 #{seq} 对应的状态，并{mode}。是否继续？',
  versioningRestoreConfirmText: '回溯',
  versioningRestoreFailed: '回溯失败',
  versioningRestoreConversationTitle: '版本回溯对话',
  versioningRestoreCopyDone: '已复制并回溯到新对话',
  versioningRestoreDone: '回溯完成',
} as const;
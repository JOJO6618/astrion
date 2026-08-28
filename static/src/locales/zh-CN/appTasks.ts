// 文案命名空间：appTasks（zh-CN 源语言）
// 规范见 doc/frontend/i18n_spec.md。en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：static/src/app/methods/taskPolling/{aiStream,compression,goal,lifecycle,placeholder,tool,workflow}.ts、methods/tooling.ts、methods/resources.ts
export default {
  // ── 通用状态标签（resources / tooling 复用） ──
  // TODO(common): 候选公共词
  unknown: '未知',
  unknownTime: '未知时间',
  hostMode: '宿主机模式',
  containerMode: '容器模式',
  // TODO(common): 候选公共词
  paused: '已暂停',
  // TODO(common): 候选公共词
  stopped: '已停止',
  // TODO(common): 候选公共词
  justNow: '刚刚',
  minutesAgo: '{n} 分钟前',
  hoursAgo: '{n} 小时前',

  // ── 对话压缩（compression.ts） ──
  // TODO(common): 候选公共词
  compressionManual: '手动',
  // TODO(common): 候选公共词
  compressionAuto: '自动',
  compressing: '压缩中',
  compressingMessage: '对话正在{mode}压缩，请稍候...',
  shallowCompressionTitle: '自动浅层压缩',
  shallowCompressedMessage: '已自动压缩 {n} 条较早工具结果',
  compressionComplete: '压缩完成',
  compressedEarlierContent: '已压缩较早的对话内容',

  // ── 任务恢复（compression.ts） ──
  taskRestoredTitle: '任务恢复',
  taskRestoredMessage: '检测到进行中的任务，已恢复连接',

  // ── 等待首包占位（aiStream.ts / compression.ts） ──
  thinkingLabel: '思考中...',

  // ── 目标/工作流审核面板（goal.ts / workflow.ts） ──
  goalReviewTitle: '目标审批',
  workflowReviewTitle: '工作流审核',
  reviewStarted: '开始审核',
  reviewRound: '审核轮次 {n}',

  // ── 自动审批（tool.ts） ──
  autoApprovalRecordTitle: '自动审批记录',
  autoApprovalStarted: '自动审批开始',
  approvalApproved: '批准通过',
  approvalRejected: '拒绝',
  approvalFinalMessage: '{decision}\n原因：{reason}',
  reasonNotProvided: '未提供',

  // ── 工具块与工具设置（tool.ts / tooling.ts） ──
  preparingTool: '准备调用 {name}...',
  interruptedByNewResponse: '已被新的响应中断',
  cannotModify: '无法修改',
  categoryEnforcedByAdmin: '该工具类别被管理员强制设置',
  cannotSwitchTool: '无法切换工具',
  toolToggleLockedByAdmin: '工具启用/禁用已被管理员锁定',

  // ── 用户提问通知（tool.ts） ──
  answerNeededTitle: '{dot} 需要回答 - Agents',
  questionConfirmTitle: '需要你确认一个问题',

  // ── 任务错误与重试（lifecycle.ts） ──
  retrySoonTitle: '即将重试',
  retryInSeconds: '将在 {n} 秒后重试（第 {attempt}/{max} 次）\n错误：{error}',
  toolCallFailed: '工具调用失败',
  taskFailedTitle: '任务执行失败',
  apiErrorTitle: 'API 调用失败',
  apiErrorMessage: '模型服务异常：{error}',
  timeoutTitle: '任务超时',
  timeoutMessage: '任务执行时间过长，已自动停止',
  quotaTitle: '配额不足',
  quotaMessage: '您的使用配额已用尽',

  // ── 下载（resources.ts） ──
  cannotCompleteDownload: '无法完成下载',
} as const;
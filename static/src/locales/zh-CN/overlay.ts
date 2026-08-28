// overlay 文案包（第二波迁移新增命名空间）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
// 使用方：static/src/components/overlay/*（13 个弹窗/浮层组件）
export default {
  // TODO(common): 候选公共词——以下词多为通用状态/动词，可归并到 common.ts：
  //  停止中.../手动停止/停止失败/上传中.../从本地发送/刷新/开启/关闭/完成/进行中/调用中
  // ── 停止操作共用（BackgroundCommandDialog / SubAgentActivityDialog） ──
  stopInProgress: '停止中...',
  stopManually: '手动停止',
  stopFailed: '停止失败',

  // ── BackgroundCommandDialog：后台指令 ──
  bgCommandTitle: '后台指令 {id}',
  bgCommandReadingOutput: '正在读取后台指令输出...',

  // ── ConversationReviewDialog：对话回顾 ──
  reviewTitle: '对话回顾',
  reviewSubtitle: '选择要生成回顾文件的对话',
  generatedHint: '已生成 {path}',
  conversationList: '对话列表',
  conversationCount: '共 {n} 条',
  noConversations: '暂无对话',
  unnamedConversation: '未命名对话',
  currentTag: '当前',
  messageCount: '{n}条',
  toolCount: ' · {n}工具',
  loadingMore: '载入中...',
  loadMore: '加载更多',
  noMore: '没有更多了',
  previewTitle: '预览（前 {n} 条）',
  previewGenerating: '预览生成中...',
  previewEmptyHint: '选择左侧对话以查看预览',
  previewLimitHint: '最多展示前 {n} 条',
  previewCount: '{n} 条',
  sendToModel: '是否发送给模型',
  generating: '生成中...',

  // ── GoalProgressDialog：目标进度 ──
  goalDoneTitle: '目标已完成',
  goalStoppedTitle: '目标模式停止',
  goalRunningTitle: '目标模式进行中',
  goalLabel: '目标：{goal}',
  metricTurns: '已运行轮数',
  metricTokens: '消耗 token',
  metricToolCalls: '工具调用',
  metricDuration: '用时',
  stopReason: '停止原因：{reason}',
  summaryDone: '完成总结',
  summaryLatest: '最后进展',
  statusDone: '已完成',
  statusStopped: '已停止',
  reasonIdleNoTool: '主模型停止时未调用任何工具（可能卡住）',
  reasonMaxTurns: '已达到最大轮数上限',
  reasonMaxTokens: '已达到累计 token 上限',
  reasonUserCancel: '用户手动停止',

  // ── ImageLightbox：图片预览 ──
  imagePreview: '图片预览',
  closePreview: '关闭预览',

  // ── ImagePicker：图片选择 ──
  imagePickerTitle: '选择图片（最多9张）',
  uploading: '上传中...',
  sendFromLocal: '从本地发送',
  noImages: '未找到图片文件',
  imageSelectedCount: '已选 {n} / 9',

  // ── NewUserTutorialPrompt：新手教程提示 ──
  tutorialPromptAriaLabel: '新手教程提示',
  welcome: '欢迎',
  newUserTitle: '新用户 {name}',
  user: '用户',
  tutorialPromptDesc: '是否需要通过新手教程来快速认识这个系统？',
  processing: '处理中...',
  startTutorial: '开始吧！',
  noMorePrompt: '不再提示',

  // ── PathAuthorizationDialog：路径授权 ──
  pathAuthTitle: '路径授权',
  writableMode: '可读可写',
  readableMode: '仅可读',
  writableHint: '可读可写路径在 workspace-write 沙箱中可写入，只读沙箱中仅可读。',
  readableHint: '仅可读路径在只读沙箱中会被加入允许读取列表；工作区可写沙箱默认已可读。',
  writablePlaceholder: '每行一个路径，例如：~/Desktop/agents-export',
  readablePlaceholder: '每行一个路径，例如：~/Documents/reference',

  // ── PlanApprovalDialog：计划待批准 ──
  planApprovalAriaLabel: '计划待批准',
  planApprovalTitle: '计划待批准',
  planTruncatedNote: '（内容过长，已截断预览，完整内容见文件）',
  planCommentPlaceholder: '意见（可选）：批准时作为补充要求，拒绝时说明需要调整的方向…',
  planRejectTitle: '拒绝这份计划，AI 将根据你的意见修订后重新提交',
  reject: '拒绝',
  planApproveTitle: '批准计划并切换到执行模式开始实施',
  submittingPlan: '提交中…',
  approveAndExecute: '批准并执行',

  // ── SubAgentActivityDialog：子智能体活动 ──
  subAgentProgressTitle: '子智能体 #{id} 进度',
  readingActivity: '正在读取子智能体活动...',
  noActivity: '暂无活动记录',
  toolReadFile: '阅读 {path}',
  toolWriteFile: '写入文件 {path}',
  toolReadSkill: '阅读技能 {name}',
  toolWebSearch: '在互联网中搜索 {query}',
  toolExtractWebpage: '在互联网中提取 {url}',
  toolRunCommand: '运行命令 {command}',
  toolEditFile: '编辑 {path}',
  toolReadMedia: '读取媒体文件 {path}',
  toolGeneric: '工具',
  stateCompleted: '完成',
  stateCalling: '调用中',
  stateInProgress: '进行中',

  // ── TutorialOverlay：新手教程浮层 ──
  tutorialExit: '退出',
  tutorialFallbackTitle: '新手教程',
  tutorialWaitingTarget: '未找到目标元素，可稍后重试或跳过当前步骤。',
  tutorialMustClickHint: '请点击高亮目标继续。',
  tutorialScrollHint: '提示：个人空间内容可上下滚动查看。',
  tutorialComplete: '完成',
  tutorialClickFirst: '请先点击高亮目标',
  tutorialNextSkip: '下一步（跳过）',
  tutorialNext: '下一步',

  // ── UserQuestionDialog：用户提问弹窗 ──
  userQuestionAriaLabel: '需要你回答一个问题',
  windowControlAria: '窗口控制',
  minimizeAria: '暂时收起',
  userQuestionWindowTitle: '需要你确认',
  questionIndex: '问题 {current} / {total}',
  questionNavAria: '问题切换',
  prevQuestionAria: '上一个问题',
  nextQuestionAria: '下一个问题',
  answerPlaceholder: '输入文字回答…',
  dismissTitle: '不回答当前这个问题，AI 将改为在对话中直接提问',
  dismiss: '✕ 不回答',
  submittingAnswer: '提交中...',

  // ── VersioningDialog：版本管理 ──
  switchOn: '开启',
  switchOff: '关闭',
  refresh: '刷新',
  emptyMessage: '(空消息)',
  filesCount: '{n} 文件',
  noCheckpoints: '暂无版本点',
  loadingDetail: '加载详情中...',
  detailTitle: '提交详情 #{seq}',
  noDiffLines: '暂无可展示的文本变更行',
  diffTruncated: '内容过长，已截断展示',
  selectCheckpoint: '请选择一个版本点查看详情',
  restoreScope: '回溯范围',
  scopeConversationOnly: '仅回溯对话',
  scopeWorkspaceAndConversation: '回溯对话和工作区',
  restoreModeLabel: '回溯模式',
  modeOverwrite: '覆盖当前对话',
  modeCopy: '复制对话',
  restoring: '回溯中...',
  confirmRestore: '确认回溯',

  // ── VideoPicker：视频选择 ──
  videoPickerTitle: '选择视频（一次最多 1 个）',
  noVideos: '未找到视频文件',
  videoSelectedCount: '已选 {n} / 1',
} as const;
// appUi 文案包（第二波迁移新增命名空间）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
// 使用方：static/src/app/methods/ui/*、static/src/app/methods/upload/*（UI 方法层 toast/confirm/Error 文案）
export default {
  // ── 通用候选公共词 ──
  unknown: '未知', // TODO(common): 候选公共词
  saveFailed: '保存失败', // TODO(common): 候选公共词
  notice: '提示', // TODO(common): 候选公共词
  pleaseRetry: '请重试', // TODO(common): 候选公共词
  appliedImmediately: '已立即生效', // TODO(common): 候选公共词
  switchFailed: '切换失败', // TODO(common): 候选公共词
  createFailed: '新建失败', // TODO(common): 候选公共词
  deleteFailed: '删除失败', // TODO(common): 候选公共词
  renameFailed: '重命名失败', // TODO(common): 候选公共词
  openFailed: '打开失败', // TODO(common): 候选公共词
  disabledByAdmin: '已被管理员禁用', // TODO(common): 候选公共词
  forceDisabledByAdmin: '被管理员强制禁用', // TODO(common): 候选公共词

  // ── 宿主机/项目工作区（hostWorkspace.ts） ──
  unnamedProject: '未命名项目',
  fetchProjectsFailed: '获取项目列表失败',
  pathCannotBeEmpty: '路径不能为空',
  projectNameCannotBeEmpty: '项目名称不能为空',
  workspaceNameCannotBeEmpty: '工作区名称不能为空',
  workspaceSwitched: '工作区已切换',
  projectSwitched: '项目已切换',
  switchHostWorkspaceFailed: '切换宿主机工作区失败',
  switchWorkspaceFailed: '切换工作区失败',
  switchProjectFailed: '切换项目失败',
  workspaceCreated: '工作区已创建',
  projectCreated: '项目已创建',
  createWorkspaceFailed: '新建工作区失败',
  createProjectFailed: '新建项目失败',
  workspaceRenamed: '工作区已重命名',
  projectRenamed: '项目已重命名',
  renameWorkspaceFailed: '重命名工作区失败',
  renameProjectFailed: '重命名项目失败',
  deleteWorkspaceConfirmTitle: '删除工作区',
  deleteProjectConfirmTitle: '删除项目',
  deleteWorkspaceConfirmMessage: '确定要从列表中删除“{name}”吗？不会删除磁盘上的工作区文件夹。',
  deleteProjectConfirmMessage: '确定要删除项目“{name}”吗？该项目文件夹和对话记录会被一并删除。',
  workspaceDeleted: '工作区已删除',
  projectDeleted: '项目已删除',
  deleteWorkspaceFailed: '删除工作区失败',
  deleteProjectFailed: '删除项目失败',
  openWorkspaceFailed: '打开工作区失败',
  openProjectFailed: '打开项目失败',
  setAsDefaultWorkspace: '已设为默认工作区',
  setAsDefaultProject: '已设为默认项目',
  setDefaultWorkspaceFailed: '设置默认工作区失败',
  setDefaultProjectFailed: '设置默认项目失败',

  // ── 计划审批/用户问答（dialog.ts） ──
  policyBlockedPersonalSpace: '个人空间已被管理员禁用',
  policyBlockedReview: '对话引用已被管理员禁用',
  submitPlanDecisionFailed: '提交计划决策失败',
  planApproved: '计划已批准',
  planApprovedMessage: '已切换到执行模式，开始实施',
  planRejected: '已拒绝计划',
  planRejectedMessage: 'AI 将根据你的意见修订后重新提交',
  unavailable: '无法使用',
  unavailableNoConnectionMessage: '当前未连接，无法生成回顾文件',
  cannotReferenceCurrentConversation: '无法引用当前对话',
  chooseOtherConversationForReview: '请选择其他对话生成回顾',
  submitAnswerFailed: '提交回答失败',
  submitApprovalFailed: '提交审批失败',
  approvalFailed: '审批失败',

  // ── 模型切换（model.ts） ──
  modelDisabled: '模型被禁用',
  conversationHasImagesMessage: '当前对话包含图片，目标模型不支持图片输入',
  conversationHasVideosMessage: '当前对话包含视频，目标模型不支持视频输入',
  modelSwitched: '模型已切换',
  switchModelFailed: '切换模型失败',

  // ── 运行模式/子智能体（mode.ts） ──
  modeUnavailable: '模式不可用',
  fastOnlyModeMessage: '当前模型仅支持快速模式',
  thinkingOnlyModeMessage: '当前模型仅支持思考模式',
  switchThinkingModeFailed: '切换思考模式失败',
  settingFailed: '设置失败',
  setReasoningEffortFailed: '设置推理强度失败',
  pauseAllSubAgentsTitle: '是否暂停所有子智能体？',
  terminateAllSubAgentsTitle: '是否终结所有子智能体？',
  pauseAllSubAgentsMessage: '所有正在运行的子智能体将停止工作并变为空闲状态。取消表示不执行操作。',
  terminateAllSubAgentsMessage: '所有后台子智能体将被强制终止。取消表示不执行操作。',
  pause: '暂停',
  terminate: '终结',
  stopSubAgentsFailed: '停止子智能体失败',
  subAgentsPaused: '子智能体已暂停',
  subAgentsTerminated: '子智能体已终结',
  stoppedSubAgentCount: '已处理 {n} 个子智能体',

  // ── 面板/权限/执行环境/网络权限（panel.ts / permission.ts） ──
  policyBlockedFocusPanel: '聚焦面板已被管理员禁用',
  policyBlockedTokenPanel: '用量统计已被管理员禁用',
  switchPermissionFailed: '切换权限失败',
  permissionUpdated: '权限已更新',
  switchExecutionModeFailed: '切换执行环境失败',
  executionModeUpdated: '执行环境已更新',
  switchNetworkPermissionFailed: '切换网络权限失败',
  networkPermissionUpdated: '网络权限已更新',
  networkRestricted: '受限',
  networkFull: '完全开放',
  switchedToMode: '已切换为 {mode}',
  pathAuthorizationSaved: '路径授权已保存',
  pathAuthApplyMessage: '命令工具立即生效；终端会话请重开后生效',
  savePathAuthorizationFailed: '保存路径授权失败',

  // ── 回顾生成/对话压缩（review.ts） ──
  conversationAutoCompressing: '对话自动压缩中',
  compressingNowPleaseWait: '当前对话正在压缩，请稍后再试',
  policyBlockedCompress: '压缩对话已被管理员禁用',
  selectConversation: '请选择对话',
  selectConversationForReview: '请选择要生成回顾的对话记录',
  cannotSend: '无法发送',
  noActiveConversationMessage: '当前没有活跃对话，无法自动发送提示消息',
  reviewPathMissing: '未获取到生成的文件路径',
  reviewSuggestReadFull: '建议直接完整阅读。',
  reviewSuggestReadBySearch: '建议使用 read 工具进行搜索或分段阅读。',
  reviewAutoMessage:
    '帮我继续这个任务，对话文件在 {path}，文件长 {count} 字符，{suggestion} 请阅读文件了解后，不要直接继续工作，而是向我汇报你的理解，然后等我做出指示。',
  reviewFileGenerated: '回顾文件已生成',
  generateFailed: '生成失败',
  generateReviewFailed: '生成回顾失败',
  fetchPreviewFailed: '获取预览失败',

  // ── 连接/上传（drag.ts / paste.ts / socket.ts） ──
  notConnected: '未连接',
  waitForConnectionBeforeUpload: '请等待服务器连接后再上传',
  uploadDisabled: '上传被禁用',
  uploadDisabledByAdmin: '已被管理员禁用上传功能',
  modelDoesNotSupportImage: '当前模型不支持图片',
  chooseImageModelMessage: '请选择支持图片输入的模型后再发送图片',
  modelDoesNotSupportVideo: '当前模型不支持视频',
  switchToVideoModelMessage: '请切换到支持视频输入的模型后再发送视频',
  workspaceBootstrapTitle: '尚未创建工作区',
  workspaceBootstrapMessage: '请点击侧边栏的「工作区」按钮创建第一个工作区',
  statusApiRequestFailed: '状态接口请求失败: {status}',
  hostModeFileTreeUnavailable: '宿主机模式下文件树不可用',
  dockerModeFilesChanged: 'Docker 模式下文件区已改为项目列表',

  // ── 上传（entries.ts / picker.ts / process.ts / quick.ts） ──
  noImagesFound: '未找到图片',
  noImagesInWorkspace: '工作区内没有可用的图片文件',
  loadImagesFailed: '加载图片失败',
  noVideosFound: '未找到视频',
  noVideosInWorkspace: '工作区内没有可用的视频文件',
  loadVideosFailed: '加载视频失败',
  uploadingTitle: '上传中',
  waitImageUploadDone: '请等待当前图片上传完成',
  waitFileUploadDone: '请等待当前文件上传完成',
  noFileReceived: '未获取到文件',
  noValidFileContent: '系统未返回有效的文件内容，请重试',
  limitReachedTitle: '已达上限',
  maxImagesMessage: '最多只能选择 {max} 张图片',
  maxFilesMessage: '最多只能附加 {max} 个文件',
  ignoredTitle: '已忽略',
  skippedNonImageFiles: '已跳过非图片文件',
  exceededTitle: '已超出数量',
  truncatedImagesMessage: '最多还能添加 {n} 张图片，已自动截断',
  truncatedFilesMessage: '最多还能附加 {n} 个文件，已自动截断',
  waitVideoUploadDone: '请等待当前视频上传完成',
  skippedNonVideoFiles: '已跳过非视频文件',
  tooManyVideosTitle: '视频数量过多',
  onlyOneVideoMessage: '一次只能选择 1 个视频，已使用第一个',

  // ── 系统/教程/终端/工作模式（system.ts / tutorial.ts / terminal.ts / workMode.ts） ──
  blankWelcomeDefault: '有什么可以帮忙的？',
  updateTutorialStatusFailed: '更新新手教程状态失败',
  tutorialSaveFailedMessage: '保存新手教程状态失败，请稍后重试',
  policyBlockedTerminal: '实时终端已被管理员禁用',
  workModeRunningMessage: '对话运行中，请等待当前任务结束后再切换运行模式',
  switchRunModeFailed: '切换运行模式失败',
  // TODO(common): 运行模式档位词（plan/ask/execute）候选公共词
  workModePlan: '计划',
  workModeAsk: '询问',
  workModeExecute: '执行',
  runModeUpdated: '运行模式已更新',

  // ── Git / 运行任务 / 输入草稿（git.ts / workspace.ts / composer.ts） ──
  loadGitChangesFailed: '加载 Git 变更失败',
  fetchRunningTasksFailed: '获取运行任务失败',
  saveInputDraftFailed: '保存输入草稿失败',
  fetchInputDraftFailed: '获取输入草稿失败',

  // ── 子智能体/后台命令完成标签（shared.ts） ──
  subAgentTaskDone: '子智能体{agentId} 任务完成',
  backgroundRunCommandDone: '后台 run_command 完成',
} as const;
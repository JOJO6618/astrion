// 文案命名空间：appCore（zh-CN 源语言）—— 第二波迁移组12（独占）
// 规范见 doc/frontend/i18n_spec.md。en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：static/src/App.vue、static/src/app/{bootstrap,computed,state}.ts
export default {
  // ── 拖拽上传 / 首屏加载（App.vue） ──
  dragUploadText: '松开鼠标上传图片',
  dragUploadHint: '支持拖拽图片、视频文件到此处',
  connectingServer: '正在连接服务器，请稍候',
  refreshPage: '刷新页面',

  // ── 头部下拉 / 移动端导航（App.vue） ──
  // TODO(common): 候选公共词
  model: '模型',
  runMode: '运行模式',
  collapseQuickDock: '收起快捷窗口',
  expandQuickDock: '展开快捷窗口',
  switchWorkspace: '切换工作区',
  conversationHistory: '对话记录',
  personalSpace: '个人空间',

  // ── show_html / show_file 卡片（bootstrap.ts） ──
  // TODO(common): 候选公共词
  refresh: '刷新',
  fullscreen: '全屏',
  cardActions: '卡片操作',
  imageUnsupportedPath: '无法显示图片：不支持的图片路径 {src}',
  imageMissingPath: '无法显示图片：缺少图片路径',
  imageLoadFailed: '图片加载失败',
  renderingContent: '正在渲染内容...',
  showFileMissingPath: '[show_file: 缺少 path 属性]',

  // ── 头部运行模式 / 模型（computed.ts） ──
  fastMode: '快速模式',
  fastModeDesc: '无思考，响应更快',
  thinkingModeDesc: '持续推理，适合复杂任务',
  modelNotSelected: '未选择模型',
  // 复数：英文用管道（调用方需同时传 {n} 与 {count}）；中文不区分
  backgroundCommandCount: '{n} 个后台指令',
  backgroundAgentCount: '{n} 个后台智能体',
  backgroundRunning: '运行中...',
  thinking: '思考中...',
  waitingApiResponse: '等待 API 响应...',

  // ── 空白页欢迎语（state.ts blankWelcomePool） ──
  welcomeHelp: '有什么可以帮忙的？',
  welcomeHot: '想了解些热点吗？',
  welcomeHomework: '要我帮你完成作业吗？',
  welcomeCode: '整点代码？',
  welcomeChat: '随便聊点什么？',
  welcomeOrganize: '想让我帮你整理一下思路吗？',
  welcomeTool: '要不要我帮你写个小工具？',
  welcomeContinue: '发我一句话，我来接着做。',
  welcomeDraw: '我来给你画幅画？',
  welcomeCat: '想看小猫吗？',
  welcomePuzzle: '有什么难题丢过来吧。',
  welcomeFun: '想不想看个有意思的？',
  welcomeNeed: '来，说说你的需求~',

  // ── 网络权限 / 工作模式 / 权限模式 / 执行环境选项（state.ts）
  // label 复用 appUi.* / input.* / personalization.*，此处仅补描述与缺失 label。
  networkRestrictedDesc: '仅允许本地回环访问，外部网络不可用',
  networkFullDesc: '允许所有出站和入站网络连接',
  workModePlanDesc: '只制定计划，批准后执行',
  workModeAskDesc: '先讨论确认，再开工',
  workModeExecuteDesc: '自行补全细节，直接开工',
  permissionApproval: '批准',
  permissionAutoApproval: '自动审核',
  // TODO(common): 候选公共词
  permissionUnrestricted: '无限制',
  permissionReadonlyDesc: '仅允许读取/搜索类工具，禁止修改工作区',
  permissionApprovalDesc: '对工作区文件进行修改的工具需用户批准后才会执行',
  permissionAutoApprovalDesc: '工作区内写入直通，高风险操作由后台审核智能体自动审批',
  permissionUnrestrictedDesc: '保持当前默认行为，不额外拦截',
  executionSandboxDesc: '所有指令会在系统沙箱中执行',
  executionDirectDesc: '所有指令会在宿主机直接执行',
} as const;
// 文案命名空间：workflow（zh-CN 源语言）
// 规范见 doc/frontend/i18n_spec.md。由迁移任务填充，en-US 同名文件必须保持 key 完全一致（tsc 强制）。
export default {
  // 顶栏
  backToLibrary: '返回工作流库',
  badgeBuiltin: '内置',
  badgeUser: '用户',
  unsaved: '未保存',
  checkIssues: '查看结构提醒与错误',
  autoLayout: '自动排版',
  addNodeTitle: '在画布中央添加节点（或双击画布空白处添加阶段）',
  addNode: '添加节点',

  // 添加节点类型
  stage: '阶段',
  stageDesc: 'AI 执行，单入单出',
  review: '审核',
  reviewDesc: '菱形，通过/驳回',
  branch: '分支',
  branchDesc: '多入多出，条件路由',
  start: '开始',
  startDesc: '入口，只能有一个',
  end: '结束',
  endDesc: '终点，可多个',

  // 工作流属性
  workflowProps: '工作流属性',
  nameLabel: '名称（唯一标识）',
  descLabel: '描述',
  descPlaceholder: '激活选择时靠它辨认',
  reviewCapability: '审核智能体取证能力',
  reviewReadonly: '只读审核',
  reviewActive: '可调命令取证',
  reviewModeHint: 'active 模式允许审核智能体执行只读命令核实成果',
  maxRoundsLabel: '单阶段最大轮数',
  endMethodLabel: '整体结束方式',
  endMethodPlaceholder: '例如：报告落盘且审核通过',
  globalNotes: '全局说明',
  globalNotesLabel: '工作方式 / 验证方式 / 结束方式',

  // 阶段属性
  stageProps: '阶段属性',
  stageNameLabel: '阶段名称',
  stageGoalLabel: '阶段目标（goal）',
  stageGoalPlaceholder: '这一阶段要达成什么',
  stageWorkLabel: '工作方式说明',
  stageWorkPlaceholder: '本阶段的具体工作方式（自然语言）',
  entryStageAriaLabel: '入口阶段',
  terminalStageAriaLabel: '终点阶段',
  stageGoalUnset: '未设置阶段目标',
  forwardRoute: '前进路由',
  removeRouteAriaLabel: '移除到 {name} 的路由',
  endStageEmpty: '终点阶段（无后续路由）',
  routeHint: '从节点右侧连桩拖线到目标节点；已有出线时再拉新线会替换旧线。想分叉请添加分支节点',

  // 删除节点（所有面板共用）
  deleteNode: '删除此节点',
  deleteNodeWarning: '删除后指向它的路由也会移除',
  confirmDelete: '确认删除',

  // 审核属性
  reviewProps: '审核属性',
  reviewNameLabel: '审核名称',
  reviewFocusLabel: '审核关注点',
  reviewFocusPlaceholder: '审核智能体检查什么（自然语言）',
  maxRejectsLabel: '驳回上限（次）',
  maxRejectsHint: '超过上限后升级给用户处理（demo 仅记录数值）',
  rejectLimitMeta: '驳回 ≤{n}',
  passRoute: '通过路由',
  removePassRouteAriaLabel: '移除到 {name} 的通过路由',
  passEndsWorkflow: '通过即结束工作流',
  passRouteHint: '审核通过后的去向，从菱形右侧连桩拖线（蓝线）',
  rejectRoute: '驳回路由',
  removeRejectRouteAriaLabel: '移除到 {name} 的驳回路由',
  rejectRequired: '必须连接驳回路由',
  rejectRouteHint: '审核不通过时的回退目标，从菱形顶部或底部连桩拖线（红线；上下出口语义相同，按目标方位自动选向）',

  // 分支属性
  branchProps: '分支属性',
  branchNameLabel: '分支名称',
  branchOuts: '出线（{n}）',
  branchOutMeta: '{n} 出',
  removeOutAriaLabel: '移除到 {name} 的出线',
  branchConditionPlaceholder: '条件：当……时走这条路',
  branchNoOuts: '无出线（死端）',
  branchRouteHint: '从节点右侧连桩逐条拖线；1 入 n 出 = 分线，n 入 1 出 = 并线。多条出线时每条都要写条件，AI 汇报时按条件选择去向',

  // 开始/结束节点
  startNode: '开始节点',
  endNode: '结束节点',
  boundaryNameLabel: '名称',
  entryRoute: '入口路由',
  disconnectEntryAriaLabel: '断开到 {name} 的入口路由',
  entryNotConnected: '未连接（从开始节点右侧连桩拖线到首个节点）',
  startOnlyHint: '工作流从这里启动；开始节点只能有一个',

  // 结构校验标签
  issueErrors: '{n} 个错误',
  issueWarnings: '{n} 个提醒',
  issueOk: '结构正常',

  // 瞬时常亮提示（flash）
  flashReplaceReject: '已替换原驳回路由（原驳回到「{name}」）',
  flashBranchRetarget: '已将该出线的流向改到「{name}」',
  flashReplaceRoute: '已替换原出线（原连到「{name}」）',
  flashFixErrors: '存在 {n} 个结构错误，请先修复',
  flashSaved: '已保存',
  saveFailed: '保存失败',

  // 外壳：加载/增删改失败提示（WorkflowDemoShell）
  loadListFailed: '加载工作流列表失败',
  loadFailed: '加载工作流失败',
  createFailed: '新建工作流失败',
  duplicateFailed: '复制工作流失败',
  deleteFailed: '删除工作流失败',

  // 工作流库列表页（WorkflowLibraryView）
  backToConversation: '返回对话',
  libraryTitle: '工作流',
  librarySubtitle: '把一套既定的工作方式、验证方式与结束方式存为流程，在对话中激活复用。',
  newWorkflow: '新建工作流',
  noDescription: '（无描述）',
  nodeCountLabel: '{n} 个节点',
  emptyTitle: '还没有工作流',
  emptyHint: '新建一个，或在对话中让 AI 帮你生成后归档。',
  // TODO(common): 候选公共词
  edit: '编辑',

  // API 错误包装（api.ts）
  httpError: '{msg}（HTTP {status}）',

  // 连线操作错误（workflowModel.ts：connectNext / replaceBranchTarget / connectRejectTo）
  connectSelf: '不能连接到自身',
  sourceMissing: '源节点不存在',
  endHasNoOut: '结束节点没有出线',
  targetMissing: '目标节点不存在',
  connectToStart: '不能连到开始节点',
  routeExists: '该路由已存在',
  targetIsRejectRoute: '该目标已是驳回路由（红线），先断开驳回路由',
  onlyBranchHasOuts: '只有分支节点有多出桩',
  emptySlot: '该连接点是空桩，拖线即新增出线',
  targetAlreadyOut: '该目标已在出线中',
  rejectToSelf: '不能驳回至自身',
  onlyReviewHasReject: '只有审核节点（菱形）有驳回路由',
  rejectToStart: '不能驳回至开始节点',
  targetIsPassRoute: '该目标已是通过路由（蓝线）',

  // 结构校验消息（workflowModel.ts：validateWorkflow）
  missingName: '工作流缺少 name',
  missingDescription: '工作流缺少 description（激活选择时靠它辨认）',
  needStartEnd: '至少需要一个开始节点和一个结束节点',
  missingStart: '缺少开始节点（顶栏「添加节点 → 开始」）',
  multipleStarts: '开始节点只能有一个（当前 {n} 个）',
  missingEnd: '缺少结束节点（顶栏「添加节点 → 结束」）',
  dupNodeId: '节点 id 重复：{id}',
  nodeMissingName: '节点 {id} 缺少名称',
  startNotConnected: '开始节点未连接（从右侧连桩拖线到首个节点）',
  startBadTarget: '开始节点指向不存在的节点：{target}',
  stageMissingGoal: '阶段「{name}」缺少目标描述',
  stageNotConnected: '阶段「{name}」未连接后续（连到下一节点或结束节点）',
  stageBadTarget: '阶段「{name}」指向不存在的节点：{target}',
  stageToStart: '阶段「{name}」不能连到开始节点',
  reviewMissingFocus: '审核「{name}」缺少审核关注点',
  reviewNoPassRoute: '审核「{name}」未连接通过路由（蓝线）',
  reviewBadTarget: '审核「{name}」指向不存在的节点：{target}',
  reviewNoRejectRoute: '审核「{name}」缺少驳回路由（红线必须连接）',
  reviewBadRejectTarget: '审核「{name}」驳回至不存在的节点：{target}',
  reviewRejectToStart: '审核「{name}」不能驳回至开始节点',
  reviewBadMaxRejects: '审核「{name}」驳回上限必须 ≥ 1',
  branchNoOutsNamed: '分支「{name}」没有出线（死端）',
  branchBadTarget: '分支「{name}」指向不存在的节点：{target}',
  branchToStart: '分支「{name}」不能连到开始节点',
  branchRouteMissingCondition: '分支「{name}」到「{target}」的出线缺少条件描述',
  nodeUnreachable: '节点「{name}」从开始节点不可达',

  // ── 新建工作流默认数据（创建时按当前语言固化到工作流定义，属数据快照） ──
  defaultBodyTemplate: '## 工作方式\n\n## 验证方式\n\n## 结束方式\n',
  defaultStartNode: '开始',
  defaultEndNode: '结束',
  defaultStageName: '阶段 {n}',
  defaultReviewName: '审核 {n}',
  defaultBranchName: '分支 {n}',
} as const;
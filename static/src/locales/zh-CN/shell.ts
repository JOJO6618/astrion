// shell 文案包（第二波迁移新增命名空间）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
// 使用方：static/src/components/shell/*、static/src/components/panels/*
export default {
  // TODO(common): 候选公共词——以下词多为通用动词/状态，可归并到 common.ts：
  //  运行(Run)/拒绝(Reject)/恢复(Restore)/下载文件/下载压缩包/加载更多/载入中.../搜索中.../已完成/默认/重命名/创建

  // ── ConfirmDialog：确认弹窗默认文案（调用方可覆盖） ──
  confirmOperation: '确认操作',

  // ── FileContextMenu：文件/文件夹右键菜单 ──
  downloadFile: '下载文件',
  downloadArchive: '下载压缩包',

  // ── QuotaToast / ToastStack：通知关闭按钮 ──
  closeNotification: '关闭通知',

  // ── FocusPanel：聚焦文件面板 ──
  focusFilesCount: '聚焦文件 ({n}/3)',
  closeFocusPanel: '关闭聚焦面板',
  noFocusFiles: '暂无聚焦文件',

  // ── GitChangesPanel：Git 变更面板 ──
  closeGitPanel: '关闭 Git 变更面板',
  loadingGitChanges: '正在加载 Git 变更...',
  noUncommittedChanges: '当前没有未提交变更',
  openFileWithApp: '用应用打开文件',
  dockerModeUnavailable: 'Docker 模式不可用',
  noAppsDetected: '未检测到可用应用',
  restore: '恢复',
  hiddenLines: '{n} 行未编辑的内容',
  unsetUpstream: '未设置云端分支',
  detectAppsFailed: '检测应用失败',
  openFileFailed: '打开文件失败',

  // ── TerminalPanel：终端面板 ──
  waitingTerminalSession: '等待终端会话...',
  closeTerminalPanel: '关闭终端面板',
  noOpenTerminals: '当前没有开启的终端',

  // ── ToolApprovalPanel：工具审批面板 ──
  closeApprovalPanel: '关闭审批面板',
  noPendingApprovals: '暂无待审批操作',
  pathLabel: '路径：',
  renameLabel: '重命名：',
  toolLabel: '工具：',
  summaryLabel: '说明：',
  approvalTitle: '工具审批',
  collapseApprovalPanel: '收起审批面板',
  restoreApprovalPanel: '展开审批面板',
  requestReason: '申请原因',
  parameters: '参数',
  singleFullAccess: '单次完全访问',
  allow: '允许',
  allowFullAccessExecution: '允许本次完全访问权限执行',
  reviewRecords: '审核记录',
  noApprovalRecords: '无记录',
  reviewStages: {
    start: '审核开始',
    modelCall: '调用审核模型',
    runCommand: '执行审核命令',
    complete: '审核结束',
    decision: '审核结论'
  },
  autoReviewStatus: {
    pending: '等待自动审核',
    reviewing: '自动审核中',
    approved: '自动审核通过',
    rejected: '自动审核拒绝'
  },
  finalDecision: {
    approved: '已允许执行',
    rejected: '已拒绝执行',
    expired: '已过期',
    cancelled: '已取消',
    timeout: '已超时'
  },
  humanDecision: {
    approved: '人工已允许',
    rejected: '已拒绝'
  },
  run: '运行',
  reject: '拒绝',
  // 工具名标签映射（映射表存 key，使用处 t() 解析）
  toolRunCommand: '执行命令',
  toolTerminalInput: '终端输入',
  toolCreateFile: '创建文件',
  toolCreateFolder: '创建文件夹',
  toolDeleteFile: '删除文件',
  toolRenameFile: '重命名文件',
  toolWriteFile: '写入文件',
  toolEditFile: '编辑文件',
  pendingApproval: '待审批操作'
} as const;

// 文案命名空间：commandBlocking（zh-CN 源语言）
// 规范见 doc/frontend/i18n_spec.md。en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：个人级指令拦截功能 ——
//   - settings/tabs/GeneralTab.vue（设置 → 通用：开关行 + 管理规则入口）
//   - overlay/CommandBlockingDialog.vue（规则编辑窗口）
//   - stores/commandBlocking.ts（错误文案配合展示）
export default {
  // ── 设置行（GeneralTab） ──
  settingsTitle: '指令拦截',
  settingsDesc: '阻止智能体执行命中拦截规则的命令',
  manageRules: '管理规则',

  // ── 规则管理窗口（CommandBlockingDialog） ──
  title: '指令拦截',
  statusOn: '已开启',
  statusOff: '已关闭',
  loading: '加载中...',
  summary: '{status}。规则对你的所有工作区生效，指令包含任一规则（不区分大小写）即被拦截。',
  rulesLabel: '拦截规则',
  rulesDesc: '哪些命令内容会被拦截',
  rulesPlaceholder: '每行一条规则，例如：rm -rf',
  useRecommended: '添加推荐规则',
  recommendedHint: '已追加 {n} 条推荐规则，保存后生效。',

  // ── 错误提示（保留并展示，不用 alert/confirm） ──
  loadFailedHint: '加载失败，暂时无法保存',
  toggleFailed: '更新开关失败',
  saveFailed: '保存失败',
  invalidResponse: '服务器返回的指令拦截配置无效，请重试'
} as const;

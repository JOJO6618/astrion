// chatActions 文案包（第二波迁移新增命名空间，组6：chat 动作区组件）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
// 注：与 chat.ts 完全同义的 key（targetFile/appendDone/appendSuccess/appendFailed/appendWarning/
//     linesCount/bytesCount/thinking/thinkingRunning）直接复用 chat.*，本包不重复定义。
//
// TODO(common): 候选公共词（语义通用、可能在多个命名空间复用，由主任务归并到 common）：
//   - apply「应用」/ run「执行」/ downloadFile「下载文件」/ targetFile「目标文件」
export default {
  // —— 动作摘要（ActionSummary） ——
  applyModify: '应用修改',
  appendContent: '追加内容',
  apply: '应用',
  run: '执行',
  downloadFile: '下载文件',

  // —— 文件追加补充（AppendPayloadAction；主文案复用 chat.*） ——
  appendFailedRetry: '{path} 写入失败，请按提示重新尝试。',

  // —— 修改详情 / 修改摘要（ModifyDetailsAction / ModifySummaryAction） ——
  modifyRecord: '修改记录',
  entryTitle: '修改 {n}',
  modifyTotal: '· 共 {n} 处',
  modifyCompleted: '· 已完成 {n} 处',
  modifyDone: '· 完成 {n} 处',
  modifyRemaining: '· 未完成 {n} 处',
  modifyProcessed: '已处理 {path}',
  modifyWarning: '未检测到结束标记，系统已自动处理。',

  // —— 加载动画池（loaders/index.ts） ——
  loaderPoolEmpty: 'loaderPool 不能为空',
} as const;
// 文案命名空间：storageStats（zh-CN 源语言）
// 使用方：static/src/components/settings/tabs/StorageTab.vue 及 tabs/storage/ 子组件（设置页「存储空间」）
export default {
  // ── 状态 ──
  loading: '正在统计…',
  loadFailed: '统计失败：{error}',
  refresh: '刷新',
  scanning: '统计中…',
  neverScanned: '尚未统计',
  lastScan: '最后统计 {time}',
  unsupported: '存储统计仅在桌面／宿主机模式下可用。',
  empty: '没有可统计的数据。',
  // ── 总览 ──
  totalUsed: 'Astrion 已占用',
  // ── 类别 ──
  catBackups: '备份与快照',
  catLogs: '日志',
  catMcpProfiles: '浏览器 / MCP',
  catAgentConversations: 'Agent 对话',
  catMedia: '对话媒体',
  catSubAgents: '子智能体',
  catChat: 'Chat 对话',
  catOther: '其他',
  // ── 清理 ──
  cleanupTitle: '清理',
  cleanupDesc: '删除前会再次确认；只清理日志与旧对话备份，其余类别仅统计。',
  backupRowTitle: '对话备份与快照',
  backupRowDesc: '按对话整份删除，不影响对话内容，只失去历史回滚能力。',
  logRowTitle: '日志',
  logRowDesc: '清空数据目录下的全部日志文件，正在写入的日志会被截断。',
  keepLabel: '保留最近',
  keepDays: '{n} 天',
  keepCustom: '自定义…',
  keepDaysUnit: '天',
  customPlaceholder: '天数',
  customApply: '确定',
  customHint: '1–3650 天',
  willRemove: '将删除 {count} 份备份 · 约 {size}',
  nothingToRemove: '没有超过该时间的备份',
  cleanButton: '清理',
  cleanLogsButton: '全部清空',
  cleaning: '清理中…',
  // ── 确认弹窗 ──
  confirmBackupTitle: '清理旧对话备份',
  confirmBackupMessage: '将删除 {count} 份最后更新超过 {days} 天的对话备份，释放约 {size}。',
  confirmBackupWarning: '删除后无法恢复；对话正文与历史消息不受影响，失去的是这些对话的历史检查点。',
  confirmLogsTitle: '清空日志',
  confirmLogsMessage: '将清空全部日志文件，释放约 {size}。',
  confirmLogsWarning: '正在写入的日志会被截断，不会中断后端运行。',
  confirmButton: '确认删除',
  cancelButton: '取消',
  // ── 结果 ──
  cleanBackupDone: '已删除 {count} 份备份，释放 {size}',
  cleanLogsDone: '已清空日志，释放 {size}',
  cleanFailed: '清理失败：{error}',
  cleanPartial: '有 {count} 项未能删除'
};

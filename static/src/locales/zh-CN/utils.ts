// 文案命名空间：utils（第二波迁移，zh-CN 源语言）
// 规范见 doc/frontend/i18n_spec.md。en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：static/src/utils/formatters.ts、static/src/utils/showHtmlFullscreen.ts、
//        static/src/composables/useMarkdownRenderer.ts、static/src/composables/useLegacySocket.ts
export default {
  // ── show_html 全屏预览（showHtmlFullscreen.ts） ──
  fullscreenPreview: '全屏预览',
  // TODO(common): 候选公共词
  refresh: '刷新',
  closeEsc: '关闭（Esc）',
  closeFullscreenPreview: '关闭全屏预览',

  // ── Markdown 渲染（useMarkdownRenderer.ts） ──
  copyCode: '复制代码',

  // ── 配额（formatters.ts） ──
  quotaTypeThinking: '思考模型',
  quotaTypeSearch: '搜索',
  quotaTypeFast: '常规模型',
  quotaExhaustedResetIn: '{type} 配额已用完，将在 {time} 重置',

  // ── WebSocket 事件（useLegacySocket.ts） ──
  contextTooLong: '上下文过长',
  contextNearLimit: '当前对话上下文接近上限，建议使用压缩功能。',
  videoReading: '视频读取中',
  videoReadingSlow: '读取视频需要较长时间，请耐心等待',
  unknownFile: '未知文件',
  subAgentItem: '子智能体{id} ({summary})',
  noDescription: '无描述',
  waitingForSubAgents: '⏳ 等待 {n} 个后台子智能体完成：{list}',
  // TODO(common): 候选公共词（列表分隔符）
  listSeparator: '、',
  unknownErrorOccurred: '发生未知错误',
  requestRecord: '请求记录: {path}',
  endpoint: '接口: {endpoint}',
  model: '模型: {model}',
  conversationIdLabel: '对话ID: {id}',
  taskIdLabel: '任务ID: {id}',
  apiError: 'API 错误',
  apiErrorWithCode: 'API 错误 {code}',
  systemStatus: '系统状态:\n{data}',
  commandFailed: '命令失败: {message}',
} as const;
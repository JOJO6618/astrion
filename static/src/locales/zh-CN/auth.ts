// 文案命名空间：auth（zh-CN 源语言）—— 第二波迁移组12（独占）
// 规范见 doc/frontend/i18n_spec.md。en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：static/src/auth/LoginApp.vue
export default {
  password: '密码',
  hostModeNoLogin: '宿主机模式（免登录）',
  hostLoginTitle: 'Host 登录',
  hostLoginSubtitle: '输入 Host 密码继续访问工作台',
  hostLogin: '进入 Host',
  hostPasswordRequired: '请输入 Host 密码',
  serviceUnavailable: '服务暂时不可用，请稍后重试',
  // TODO(common): 候选公共词
  networkErrorRetry: '网络错误，请重试',
  resourceBusy: '资源繁忙，请稍后再试',
  hostModeUnavailable: '宿主机模式不可用'
} as const;

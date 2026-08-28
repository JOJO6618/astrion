// 文案命名空间：auth（zh-CN 源语言）—— 第二波迁移组12（独占）
// 规范见 doc/frontend/i18n_spec.md。en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 使用方：static/src/auth/LoginApp.vue、static/src/auth/RegisterApp.vue
export default {
  loginTitle: 'Astrion 登录',
  loginSubtitle: '使用账号继续访问工作台',
  // TODO(common): 候选公共词
  email: '邮箱',
  password: '密码',
  login: '登录',
  hostModeNoLogin: '宿主机模式（免登录）',
  noAccount: '还没有账号？',
  signUp: '点击注册',
  emailAndPasswordRequired: '请输入邮箱和密码',
  serviceUnavailable: '服务暂时不可用，请稍后重试',
  loginFailed: '登录失败',
  // TODO(common): 候选公共词
  networkErrorRetry: '网络错误，请重试',
  resourceBusy: '资源繁忙，请稍后再试',
  hostModeUnavailable: '宿主机模式不可用',
  // TODO(common): 候选公共词
  createAccount: '创建账号',
  registerDoneHint: '完成注册后即可登录使用',
  username: '用户名',
  usernamePlaceholder: '仅限小写字母/数字/下划线',
  inviteCode: '邀请码',
  requiredPlaceholder: '必填',
  register: '注册',
  haveAccount: '已有账号？',
  backToLogin: '返回登录',
  fillAllFields: '请完整填写所有字段',
  registerFailed: '注册失败',
} as const;
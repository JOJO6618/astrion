// 文案命名空间：adminCustomTools（zh-CN 源语言）
// 规范见 doc/frontend/i18n_spec.md。由迁移任务填充，en-US 同名文件必须保持 key 完全一致（tsc 强制）。
// 覆盖：CustomToolsApp.vue / CustomToolsGuideApp.vue / SecondaryGate.vue（4 个 admin 页共享的二级校验门）。
export default {
  gateDescription: '输入管理员二级密码后可管理自定义工具。',
  // —— SecondaryGate（共享二级密码门，4 个 admin 页接入）——
  gate: {
    notConfiguredTitle: '尚未设置二级密码',
    notConfiguredDesc:
      '管理面板的敏感操作（邀请码、密码管理、策略配置等）由二级密码保护。当前服务端尚未配置二级密码，请先完成设置：',
    step1Text: '在服务器上生成密码哈希：',
    step1Code:
      'python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash(\'你的二级密码\'))"',
    step2TextPre: '将输出写入 settings.json 的',
    step2TextPost: '段：',
    step3Text: '重启服务，然后点击下方「重新检测」。',
    recheck: '重新检测',
    rechecking: '检测中...',
    title: '请输入管理员二级密码',
    passwordPlaceholder: '二级密码',
    verifying: '校验中...',
    confirmEnter: '确认进入',
    defaultDescription: '为保护敏感数据，需二级校验后查看。',
  },
  // —— CustomToolsGuideApp（开发指南页）——
  guide: {
    title: '开发指南',
    subtitle: '如何编写、组织与调试自定义工具的完整说明。',
  },
  renderFailed: '渲染指南失败',
  // —— CustomToolsApp（工具列表 + 编辑器 + 弹窗）——
  backToList: '返回列表',
  loadFailedWith: '加载失败：{message}',
  main: {
    title: '自定义工具管理',
    subtitle: '每个工具一个文件夹，三层文件独立存放；仅管理员可见并可调用。',
    newTool: '新建工具',
    refreshList: '刷新列表',
    viewGuide: '查看开发指南',
    backToMonitor: '返回监控',
    policyConfig: '策略配置',
    toolList: '工具列表',
    toolCount: '共 {count} 个',
    emptyTools: '暂无自定义工具',
    noDescription: '暂无描述',
    params: '参数：{count} 个',
    timeout: '超时：{seconds}s',
    noReturnLayer: '（无返回层）',
    toolNotFound: '未找到工具 {id}',
  },
  editor: {
    noDescription: '未填写描述',
    hint1Pre: '提示：execution.py 中的字典/集合需要用',
    hint1Post: '包裹，避免被模板替换。',
    hint2: '保存后无需重启，系统会自动 reload 自定义工具。',
    loadingTool: '正在加载工具...',
  },
  create: {
    title: '创建新工具',
    toolIdLabel: '工具 ID（小写/下划线）：',
    descriptionLabel: '描述：',
    creating: '创建中...',
    create: '创建',
    idRequired: '请填写工具 ID',
    idInvalid: '工具 ID 需以字母开头，可包含字母/数字/_/-',
    failed: '创建失败',
  },
  delete: {
    title: '确认删除',
    confirmPre: '确定删除工具',
    confirmPost: '吗？该操作不可恢复。',
    deleting: '删除中...',
    failed: '删除失败',
  },
  save: {
    failed: '保存 {name} 失败',
  },
} as const;
// update 文案包（桌面端「软件更新」入口与弹窗）
// 规范见 doc/frontend/i18n_spec.md。zh-CN 与 en-US 的 key 结构必须完全一致（tsc 强校验）。
// 使用方：static/src/stores/desktopUpdate.ts、
//   static/src/components/sidebar/SoftwareUpdateDialog.vue、ConversationSidebar.vue
export default {
  // 侧边栏入口
  entryTitle: '软件更新',
  newVersionBadge: '有新版本',

  // 弹窗
  dialogTitle: '软件更新',
  checking: '正在检查更新…',
  currentVersion: '当前版本',
  latestVersion: '最新版本',
  upToDate: '当前已是最新版本',
  newVersionAvailable: '发现新版本',
  releaseNotes: '更新说明',
  noReleaseNotes: '暂无更新说明',
  publishedAt: '发布于 {date}',
  updateNow: '立即更新并重启',
  later: '稍后',
  recheck: '重新检查',

  // 安装进度（state 与 Rust 控制桥对齐）
  downloading: '正在下载更新…',
  installing: '正在安装，应用将自动重启…',
  restarting: '安装完成，正在重启…',
  updateFailed: '更新失败',

  // 检查失败
  checkFailed: '检查更新失败',
};

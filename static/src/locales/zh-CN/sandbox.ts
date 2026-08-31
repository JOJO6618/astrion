// 文案命名空间：sandbox（zh-CN 源语言）
// 沙箱环境检测与一键安装向导（Windows 宿主机模式）。
// 使用方：static/src/components/overlay/SandboxSetupDialog.vue
//        static/src/components/personalization/tabs/GeneralTab.vue
//        static/src/stores/sandboxSetup.ts
export default {
  // ── 弹窗框架 ──
  setupTitle: '安装沙箱环境',
  setupAriaLabel: '沙箱环境安装向导',

  // ── 说明区 ──
  introWhat: '沙箱是一个隔离的命令执行环境（基于 WSL2）。AI 执行的所有终端命令都在沙箱内运行，与您的系统隔离。',
  introEffect: '未安装沙箱时，需要隔离执行的命令将无法运行，工具调用会直接报错。',
  introDisk: '将下载约 3MB 的 Alpine 迷你系统并安装到 {path}（含工具链总占用约 100~300MB）。',
  introUninstall: '可随时通过命令 wsl --unregister {distro} 完全卸载。',
  introSecure: '安装的是专用沙箱发行版（已关闭 Windows 互操作），不能使用已安装的 Ubuntu 等日常发行版代替。',

  // ── 状态描述（检测分级） ──
  stateWslMissing: '检测到系统尚未启用 WSL2。安装过程需要先启用 WSL2（系统将弹出管理员授权，授权后可能需要重启电脑）。',
  stateDistroMissing: '检测到 WSL2 已就绪，但尚未安装沙箱专用发行版。点击安装即可自动完成。',
  stateBwrapMissing: '检测到沙箱发行版存在，但缺少 bubblewrap 组件。点击安装将自动修复。',
  stateChecking: '正在检测沙箱环境…',

  // ── 阶段与步骤 ──
  phaseEnablingWsl: '正在请求管理员授权以启用 WSL2（请在系统弹窗中确认）…',
  phaseInstallingWsl: '正在下载并安装 WSL2 组件，可能需要几分钟…',
  phaseVerifying: '正在验收安装结果…',
  phaseDone: '安装完成，沙箱环境已就绪。',
  phaseNeedsReboot: 'WSL2 已启用，但需要重启电脑后才能继续。请重启后重新打开本向导完成安装。',
  phaseError: '安装失败',
  stepCheckWsl: '检查 WSL 环境',
  stepCheckDistro: '检查发行版',
  stepDownloadRootfs: '下载 Alpine 系统',
  stepImportDistro: '导入 WSL2 发行版',
  stepWriteConfig: '写入沙箱配置',
  stepInstallTools: '安装沙箱工具链',
  downloadProgress: '已下载 {size}',
  logLabel: '安装日志',

  // ── 按钮与选项 ──
  installNow: '立即安装',
  later: '暂不安装',
  neverAgain: '不再提示',
  rebootDone: '我已重启，继续安装',
  uacCancelledHint: '已取消管理员授权。启用 WSL2 需要管理员权限，请点击重试并在系统弹窗中选择"是"。',

  // ── 个人空间 → 通用：沙箱环境区块 ──
  sectionTitle: '沙箱环境',
  sectionReady: '已就绪',
  sectionMissing: '未安装',
  sectionChecking: '检测中…',
  sectionUnavailable: '当前环境不适用',
  sectionDesc: 'Windows 宿主机模式下，命令在基于 WSL2 的沙箱中隔离执行。',
  openWizard: '打开安装向导',
  recheck: '重新检测',
  neverAgainSet: '已选择"不再提示"',
  resetNeverAgain: '恢复提示',
};

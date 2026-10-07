// 更新安装期状态：由控制桥（bridge.js）在触发 quitAndInstall() 前打开，供窗口层查询。
//
// 存在理由：electron-updater 的 quitAndInstall() 是「先关闭所有窗口、之后才给 app
// 发 before-quit」——与正常退出顺序相反。历史故障即由此而来：
//   window.js 的 close 拦截条件依赖 appQuitting（只在 before-quit 置位），
//   于是关窗那一刻它仍为 false，配合「快捷对话已开启」把关闭改成了 hide()，
//   应用永远退不出去。现象：窗口消失、Dock 图标还在、点一下又弹回来，
//   而 ShipIt 一直等不到「无运行实例」，最后以 SQRLInstallerErrorDomain -9
//   "App Still Running Error" 放弃安装。
//
// 因此安装期必须靠显式标记放行关闭，不能依赖事件顺序。状态放在独立模块中，
// window.js / quick/controller.js / bridge.js 三方共用，避免循环 import。

let updaterInstalling = false;

/** 进入或退出「正在安装更新」状态。true = 放行窗口关闭、禁止重建/重新唤起界面。 */
export function setUpdaterInstalling(value) {
  updaterInstalling = !!value;
}

/** 是否正处于更新安装流程（quitAndInstall() 之后到进程真正退出之前）。 */
export function isUpdaterInstalling() {
  return updaterInstalling;
}

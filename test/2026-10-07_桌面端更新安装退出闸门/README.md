# 桌面端更新安装退出闸门（2026-10-07）

## 问题

macOS Electron 桌面端（0.5.2）应用内更新到 0.5.3：下载正常，界面卡在「正在安装，应用将自动重启…」；窗口消失、程序坞图标仍在、点击可恢复页面，重启后仍是旧版本。

## 根因（已取证）

1. `quitAndInstall()` 先关闭所有窗口、之后才发 `before-quit`（electron-updater 自带注释）；
2. 主窗口 `close` 只判断 `appQuitting`（只在 `before-quit` 置位），此时仍为 `false`，而快捷对话已开启（`~/.astrion/quick-entry.json` 的 `enabled: true`）→ `preventDefault()` + `hide()`；
3. 应用因此无法自行退出，ShipIt 一直等不到「无运行实例」；
4. 用户手动退出后，若在 ShipIt 安装窗口（约 5～13 秒）内重新打开应用，安装同样以
   `SQRLInstallerErrorDomain -9 "App Still Running Error"` 放弃（日志中三次均为 Dock 图标点击发起）。

现场证据：`~/Library/Caches/com.astrion.desktop.ShipIt/ShipIt_stderr.log`、
`~/Library/Caches/astrion-desktop-electron-updater/pending/`（0.5.3 包 sha512 与 `latest-mac.yml` 一致，包未损坏）。

## 本次改动

- 新增 `desktop-electron/src/updater-state.js`（安装期开关，三方共用，避免循环 import）
- `src/window.js`：主窗口 close 闸门加安装期放行；安装期 `focusMainWindow()` 直接返回
- `src/quick/controller.js`：快捷窗 close 闸门加安装期放行；安装期不唤起快捷对话
- `src/main.js`：安装期忽略 `activate` / `second-instance`
- `src/bridge.js`：`update-downloaded` 先开闸再 `quitAndInstall()`；新增 8 秒重试 `app.quit()`、
  11 秒清理后端 + `app.exit(0)` 的兜底退出阶梯；`error` 事件撤销开闸

## 测试范围

`test_updater_exit_gate.py`（离线，不启动服务/不启动 TUI）：

- `updater-state.js` 开关语义（node 直接执行）
- 主窗口 / 快捷窗 close 闸门均带安装期放行；不存在漏改的 close 闸门
- `focusMainWindow` / `activate` / `second-instance` / `showQuickEntry` 安装期短路
- 桥：先开闸后 `quitAndInstall()`、`error` 撤销开闸、兜底退出阶梯存在且显式收后端

运行：

```bash
python3 -m unittest discover -s test/2026-10-07_桌面端更新安装退出闸门 -p 'test_*.py'
```

## 尚未验证（需真机）

本用例只守代码形状，**不能**证明真实安装成功。真机验证步骤（0.5.3 → 0.5.4）：

1. 应用内触发更新，预期：窗口与程序坞图标**自行消失**（无需手动退出）；
2. `~/Library/Caches/com.astrion.desktop.ShipIt/ShipIt_stderr.log` 应出现
   `Beginning installation` → `Moving bundle` → `Installation completed successfully`
   → `Successfully launched application`，且**不再出现** `SQRLInstallerErrorDomain -9`；
3. `/Applications/Astrion.app/Contents/Info.plist` 的 `CFBundleShortVersionString` 变为 `0.5.4`；
4. 安装窗口期内点击程序坞图标不应把应用拉起来（0.5.4 起的行为）。

# 全屏浮窗与程序坞身份回归

`setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })` 默认允许 Electron 变换整个应用的进程类型；macOS 下必须传 `skipTransformProcessType: true`，快捷窗和截图覆盖层统一使用生产模块 `workspace-visibility.js`。

## 逻辑回归

```sh
node --test test/2026-10-05_程序坞全屏浮窗/*.test.mjs test/2026-10-05_程序坞与窗口截图/*.test.mjs test/2026-10-05_快捷对话与通知恢复/quick_controller.test.mjs test/2026-10-05_快捷对话与通知恢复/task_replay.test.mjs test/2026-10-04_快捷模式/quick_gateway.test.mjs
```

本机22用例通过，含全屏可见参数、跳过进程变换，以及之前启动、遮挡、通知恢复回归。

## 真实 Electron 身份检查

```sh
./desktop-electron/node_modules/.bin/electron test/2026-10-05_程序坞全屏浮窗/dock_identity_smoke.cjs
```

在独立运行目录创建两个隐藏浮窗，使用真实生产策略配置跨桌面/全屏可见性。检查两窗均为 all-workspaces、Dock仍可见，并用 lsappinfo 校验当前测试进程仍为 Foreground。已通过；无真实截图、无TCC授权修改、无视觉截图验收。

## 用户验收

本地签名包0.5.1-test.3。完全退出旧版再安装，首次打开主窗口后观察程序坞运行点是否持续显示；继续打开/隐藏快捷窗、框选截图和窗口截图，确认运行点没有因浮窗切换而消失。全屏应用上唤起快捷对话和跨Space可见效果由用户实测。

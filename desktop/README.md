# Astrion 桌面共享资源

当前桌面应用使用 Electron。macOS 在 `desktop-electron/` 开发和构建，Windows 构建与安装见 [Windows Electron](../docs/windows_electron.md)。

此目录保留 Electron 需要的 Rust 后端暂存脚本、Windows 原生辅助程序共享模块、应用图标和 Chrome Cookie 导出扩展。旧 Tauri 源码仍可供历史原生探针引用。Tauri 应用启动、构建打包和发布入口已退役。

Windows 从旧 Tauri 版本升级仍需为 Electron 安装器生成原 Minisign 签名。`npm --prefix desktop ci` 安装签名工具，具体补签、兼容命名和官网下载清单见 Windows Electron 发布说明。

# Windows Electron 顶栏回归（2026-11-13）

Windows 0.10.1 修复 `desktop-electron/src/preload-chrome.cjs` 漏注入 `__ASTRION_PLATFORM__`，导致独立顶栏的 Windows 设置与最小化、最大化/还原、关闭按钮缺失。主视图已有标记不能代替独立 chrome 视图注入。

## 最小回归

Windows 原生 Node，在工作区根目录运行：

```cmd
node --test test\2026-11-13_Windows顶栏\chrome_preload.test.cjs
```

VM 执行真实 preload，覆盖 win32/darwin 的桌面、chrome、平台三种标记。修复前两项失败，修复后 2/2 通过；证据为 `output/windows-electron/topbar-before.log`、`topbar-after.log`。

## 真实包内验证

使用已经构建的 Windows 0.10.1 目录包：

```cmd
node test\2026-11-13_WindowsElectron\packaged_smoke.mjs
```

脚本启动 `output/windows-electron/build/win-unpacked/astrion-desktop.exe`，使用独立资料、设置、工作区和随机 CDP 端口。17 项检查包括顶栏 DOM 可见、设置进入/返回、最大化/还原、真实最小化/恢复、关闭与包内后端回收，同时验证浏览器、密码与快捷对话。最新完整结果为 `output/windows-electron/packaged-smoke-1794560835643/results.json`（17/17，rendererErrors 为空）；便捷指针为 `packaged-smoke-latest.json`。

`minimize_probe.ps1` 由包内脚本在点击真实顶栏按钮后调用。它先验证 shell PID 的可执行文件路径，再用 `EnumWindows` 仅检查该 PID 的窗口。以可见性和 `GetWindowPlacement.rcNormalPosition` 的主窗正常尺寸（至少 960×600）排除隐藏快捷窗，要求唯一匹配；使用 `IsIconic` 验证实际最小化，仅恢复同一 PID 的同一 HWND，并确认恢复完成。失败只输出测试 PID 内的句柄、尺寸、可见性及最小化状态，不读取其他用户窗口标题。

不要用 `Get-Process.MainWindowHandle` 识别多窗口 Electron 主窗：浏览器弹出/收回和快捷窗创建后，可能选到隐藏窗口。也不要以最小化后的 `GetWindowRect` 图标矩形判断主窗尺寸。

本测试是用户明确要求的顶栏功能验收；视觉、动画、混合 DPI 与长期使用仍由用户实际确认。

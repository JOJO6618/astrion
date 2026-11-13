# Windows 独立浏览器标题栏配色（2026-11-13）

0.10.2 修复独立浏览器 Window Controls Overlay 使用内容区底色的问题。前端从 `--chrome-bar-bg` 读取 `chromeColor`，theme/layout 两条载荷均传递；新窗口使用缓存的标签栏色和文字色，已打开的所有独立窗口在主题变化时同步更新。普通网页和内容区底色继续使用原有表面色，macOS 保持红绿灯。

## 自动回归

Windows 原生 Node，在工作区根目录运行：

```cmd
node --experimental-vm-modules --test test\2026-11-13_Windows浏览器标题栏配色\theme.test.mjs test\2026-11-13_WindowsElectron\migration_contract.test.mjs
```

新增6项主题回归，加原23项迁移回归，共29项通过。证据：`output/windows-electron/0.10.2-theme-tests.log`。

新增覆盖：

- 经典/浅色/深色主题更新全部存活独立窗口，跳过已销毁窗口，主窗不使用独立窗口控制区 API。
- 内容表面色不变、仅标签栏或文字色改变时仍更新；等值主题不重复调用。
- 新窗口从缓存获取标签栏色，不能回退到内容底色。
- 非法颜色不污染缓存。
- macOS 不调用 Windows overlay API，红绿灯参数保留。
- 执行真实前端 `useBrowserSurface.ts` 编译结果，确认只改变标签栏 token 也会通过主题桥上报。

这些测试验证真实生产模块的逻辑与调用参数，原生窗口和Vue生命周期依赖使用受控替身，不等于实际视觉验收。

共享前端构建含 TypeScript、stylelint、Vite；本轮两个 TS 文件做定向 ESLint（仅内存归一原有 CRLF）。完整包使用既有隔离 `packaged_smoke.mjs` 做功能回归，并通过 `verify_package.mjs` 检查生产源码和前端 manifest；不使用截图或视觉自动化代替用户验收。

## 用户视觉验收

1. 启动0.10.2，在对话里将浏览器弹出为独立窗口。
2. 右上角三大键背景应与标签栏连续，不能显示成对话内容底色。
3. 独立窗口保持打开，分别切换经典、浅色、深色主题，检查背景与图标均跟随。
4. 已打开多个对话独立窗口时，都应更新；主题切换后新弹出的窗口也应一致。
5. 检查最小化、最大化/还原、关闭和收回行为正常。

本轮不覆盖正式安装，不发布更新源；0.10.1试用目录和资料保留。

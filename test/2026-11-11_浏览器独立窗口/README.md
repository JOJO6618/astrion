# 内置浏览器「单独窗口显示」前置实验（2026-11-11）

目的：功能实现前，验证 macOS Electron 35 中把网页 `WebContentsView` 在两个 `BaseWindow` 之间移动是否安全，作为“每个对话可弹出一个独立浏览器窗口”方案的依据。

运行：

```bash
cd <仓库根>
./desktop-electron/node_modules/.bin/electron test/2026-11-11_浏览器独立窗口/popout_view_move.mjs
```

- 只用本地 HTTP 合成页面，userData 隔离到 `_experiments/browser-popout-20261111/userData`，不连后端、不碰真实资料。
- 会在屏幕上短暂打开几个测试窗口，约 30 秒后自动退出。
- 结果写入 `_experiments/browser-popout-20261111/results.json`。

## 结果（Electron 35.7.5，arm64，2026-11-11）

52/52 通过。覆盖：主窗口 → 弹出窗口 → 收回 → 再弹出并关窗，以及两个弹出窗口并存。

- 移动 `removeChildView` + `addChildView` 后 webContents id 不变、不重载，JS 状态与表单输入保留，缩放 1.25 保留。
- `webContents.debugger`（CDP）全程保持连接，`Runtime.evaluate`、`Input.dispatchMouseEvent` 可用；`sendInputEvent` 点击与键盘输入送达；焦点随窗口正常。
- `setBounds` 后视口立即跟随新窗口尺寸；弹出窗口改尺寸后同样跟随。
- 弹出窗口内网页 `window.open` 用 tabs.js 同款 `createWindow` 接管为视图，保留 opener；可随父页一起挪回主窗口。
- 隐藏视图与最小化窗口：`capturePage` 正常，CDP 截图超时（与现有文档结论一致，驱动已走 capturePage）；CDP 输入可送达；定时器被后台节流到约 1 次/秒（与现有主窗口隐藏标签相同）。
- **关闭 `BaseWindow` 不会销毁子视图的 webContents**：未挪出就关窗，视图成为孤立视图，仍可执行脚本、可再挂到别的窗口。实现上关窗时必须显式把视图挪回主窗口，dispose 时必须显式关闭，否则泄漏。
- `titleBarStyle: 'hidden'` 下 `setTitle` 生效（窗口菜单 / Mission Control 用）。

## 未覆盖

真实 `NativeBrowser` 集成、弹出窗口内 DOM 浮层叠层（stacking/input 转发）、右键菜单挂到弹出窗口、输入法、多显示器、Dock/调度中心行为、长时间运行。这些需在实现后回归与实机验收。

## 功能实现后的回归（2026-11-11）

```bash
# 生产 NativeBrowser + 两个生产 preload + 合成页面：弹出/占位/固定归属/布局投影/标题/多窗口/记忆/关窗收回/dispose
./desktop-electron/node_modules/.bin/electron test/2026-11-11_浏览器独立窗口/popout_integration.mjs
# 真实构建前端（需先 npm run build）打开独立窗口并截图；参数 classic|light|dark
./desktop-electron/node_modules/.bin/electron test/2026-11-11_浏览器独立窗口/popout_visual.mjs classic
```

- 集成回归 27/27 通过，证据 `_experiments/browser-popout-20261111/integration-*/results.json`。
- 视觉脚本确认标签栏位于窗口最顶端并为红绿灯留出左侧空白、窗口标题为对话标题。整窗（含系统红绿灯）截图依赖 `screencapture -l`，终端无屏幕录制权限时只输出 renderer 截图。
- 重构后同步适配的旧回归（宿主化 API：`manager.main.*`、`snapshot(host)`、`contextMenu.build(tab, params, host)`、叠层夹具改为宿主形状）：内置浏览器集成 native-smoke（顺带把已过期的 `/conv_native_fixture` 夹具路由改为合法对话 ID）、无模型渲染（需先重建夹具 renderer）、标签独立生命周期、密码与 Chrome 导入 26 项、回收 14 + 后端 10 + 4、搜索与快捷方式 20、AI 浏览器工具 51、纯文本主题 7、叠层状态模拟、深色背景同步 4，全部通过。

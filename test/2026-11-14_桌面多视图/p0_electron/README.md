# P0 实验：隐藏 WebContentsView 与跨窗口迁移

运行（macOS，Electron 35.7.5，约 40s，自动退出）：

    cd desktop-electron && node_modules/.bin/electron ../test/2026-11-14_桌面多视图/p0_electron/p0.cjs

## 结果（2026-11-14 实测，darwin）

(a) `BaseWindow` 内 `view.setVisible(false)` 15s，页面 `setInterval(100ms)`：

| webPreferences | visibilityState | visibilitychange | 实际速率 |
|---|---|---|---|
| 默认 | `hidden`（`document.hidden=true`），`setVisible(true)` 后恢复 `visible` | 触发（`['hidden']`） | 15 次/15s = **1 Hz**（期望 10 Hz，被节流到 1/10） |
| `backgroundThrottling:false` | 始终 `visible` | 不触发 | 150 次/15s = 10 Hz（不节流） |

(b) 视图 `removeChildView` → 另一个 `BaseWindow.addChildView`：页面计数器与定时器持续累加
（14 → 28 → 42 → 48），`did-start-loading` 0 次（无重载）；原窗口 `destroy()` 后视图仍存活并继续计时；再迁移第三个窗口同样成功。

## 结论与可见性策略

- 默认配置下隐藏视图的 `visibilityState` 可靠地变为 `hidden`，定时器被节流为 1Hz，正好契合文档 §4.5「降到约 1s」的目标，不需要关闭 backgroundThrottling（关闭后隐藏视图仍 10Hz 全速，也不会触发 visibilitychange）。
- 仍按 §4.5 由壳层经 `astrionShell.onVisibility` 发送显式 `{visible}`，页面以它为准（不依赖 visibilityState，避免窗口最小化/遮挡等其他触发源干扰）。
- 视图跨窗口迁移不重载、状态保持，P2 拖出/放回方案可行。

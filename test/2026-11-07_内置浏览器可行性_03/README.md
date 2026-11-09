# Electron 原生右栏浏览器实验（第三批）

日期：2026-11-07。用户授权继续Electron路线隔离实验，并允许参考/复用合适的开源项目。没有修改正式桌面壳、后端、生产前端或真实用户数据。

## 当前验证状态

同一份代码在两个新profile下连续完整通过，各15项观测，进程exitCode=0：

- `_experiments/native-browser-feasibility-20261107/electron-panel/run-1794066219834/results.json`
- `_experiments/native-browser-feasibility-20261107/electron-panel/run-1794066316324/results.json`

手动入口也做过启动、主题操作和正常关闭冒烟：
`_experiments/native-browser-feasibility-20261107/electron-panel/manual-smoke.json`。

这些是有限的功能实验，不能等同长跑、人工视觉、真实IME、真实登录、公网Docker或Windows验收。

## 运行

使用项目已有Electron35.7.5、Chromium134.0.6998.205与原版playwright-core1.61.1；不安装额外Chromium或新的npm依赖。Sass只编译现有应用token供原型使用。

在仓库根目录执行完整自动化实验：

```bash
node test/2026-11-07_内置浏览器可行性_03/run-prototype.cjs
```

手动操作独立GUI：

```bash
./desktop-electron/node_modules/.bin/electron test/2026-11-07_内置浏览器可行性_03/demo.cjs
```

关闭手动原型窗口会退出app并关闭其本机夹具服务器。该窗口是独立实验，没有接入Astrion正式聊天或真实模型。

需要启动GUI及localhost监听。当前智能体沙箱不具备这两项能力，智能体执行时应走单次完全访问申请；不要降低沙箱或改正式配置来绕过。

## 可以操作什么

- 对话A/B各自的浏览器标签、Cookie/localStorage及下载记录。
- 地址栏、前进/后退、刷新、新标签、关闭标签。
- 原生网页中的Message/Apply与上传控件（本批没有单独重测上传，第一批测过）。
- 普通新窗口链接和脚本window.open，均进入右栏标签。
- 普通下载、同名下载、慢速下载取消及下载记录。
- 网页请求位置权限，原型自定义提示可拒绝；其他权限当前默认拒绝。允许位置按钮存在，但正向授权和系统权限链没有验收，定位也可能依赖额外服务配置。
- 收起/展开右栏、拖拽分隔线、分隔线键盘左右调整、三主题。

本批自动化测试了键盘分隔线和窗口尺寸；鼠标拖拽代码存在，尚未独立完成人工或自动化拖拽验收。

## 文件结构

| 文件 | 职责 |
|---|---|
| `main.cjs` | 独立BaseWindow、可信UI视图、窄IPC、实验生命周期 |
| `tab-registry.cjs` | 原生页面view、标签/会话归属、popup接管、可见性、布局、销毁 |
| `session-features.cjs` | session级下载、唯一保存路径、取消、权限请求和对话归属 |
| `preload.cjs` | 仅可信shell加载的command/state桥 |
| `shell.html` / `shell.css` / `shell.js` | 可信控件、事件记录、下载/权限面板、原生view遮挡协调 |
| `fixture-server.cjs` | 本机静态测试网页、下载流、既有theme token编译 |
| `scoped-automation.cjs` | Page工具实验门面，检查当前owner、generation和已登记tab |
| `run-prototype.cjs` | 原版Playwright驱动、断言、结果记录、清理 |
| `demo.cjs` | 供人工操作的自带本机夹具的Electron入口 |

## 15项观测

1. 一个原生窗口中的右栏WebContentsView；offscreen=false。
2. 有作用域的Page工具与Electron原生输入修改同一页面。
3. 拒绝rawCDP、file导航和未登记页面。
4. 地址栏及前进后退保持同一原生tab。
5. 普通target=_blank进入右栏，保留Chromium默认noopener语义。
6. 脚本window.open进入右栏，保留opener，无shell/Node桥。
7. 两个同名下载保存到不同路径，文件正文核对；下载面板打开时原生view隐藏。
8. 正在传输的慢下载可取消。
9. 自定义位置权限拒绝到达网页，未授予系统位置权限。
10. 不可信网页没有process、require、实验桥或Astrion桌面桥。
11. 对话B存储为空、无A下载；旧owner、A→B→A旧generation、跨对话tab均被拒。
12. 收起/恢复、多个窗口尺寸、原生矩形与实际窗口/DOM一致、键盘分隔线。
13. 三主题控件状态切换，未代替视觉验收。
14. 关闭标签后旧工具lease失效。
15. 正常关闭窗口后进程exitCode=0。

## 本轮踩坑

- `setWindowOpenHandler.createWindow(options)`必须接管`options.webContents`，不能另建无关联的WebContents。构造普通tab时也不能把`webContents: undefined`显式传进去。
- target=_blank的默认noopener与脚本window.open不同，不能用同一个opener断言。
- Grid/Flex子容器应设置min-height:0与内部滚动，避免记录增长把网页原生矩形撑出窗口。
- macOS可能限制请求的窗口高度；测试对照实际getContentBounds，而不是假设setContentSize请求一定原样生效。
- Playwright对象退役后不再调用electronApp.process()，启动时保存ChildProcess句柄供最终状态读取。
- 原生view在DOM浮层上方；本原型打开下载/权限面板时隐藏页面，关闭后恢复。正式应用弹窗与设置页需统一接入同样的可见性协调。

## 边界

工具门面是实验，不是完整授权系统。它把原始CDP/Electron句柄留在可信runner，按稳定targetId找Page并在await前后复核owner/generation。若切换发生在操作已发给Chromium之后，后置检查只能拒绝旧结果，不声称能撤销已发生的网页操作。

本批没有真实模型请求、正式MCP接线、导航bootstrap联动、后台恢复、长跑/资源回收量化或真实跨用户认证。Docker沿用第一批本机控制链证据，本批未新增Docker/公网测试。Windows原生适配仍未测。

## 开源参考

首选Min的viewManager、download和permissionManager作为模块级参考/后续移植候选；其源码已使用WebContentsView，但不是可直接安装的浏览器组件。mamezou的MIT示例适合最小布局参考。Wexond当前README明确禁止使用代码/素材，不作为源码复用候选。

本批没有复制第三方功能模块或添加第三方浏览器依赖。后续移植具体Min代码时需固定版本、保留Apache许可证与适用声明、标记修改，并改造其窗口全局状态以适配Astrion对话/actor作用域。详细源文件与许可见报告。

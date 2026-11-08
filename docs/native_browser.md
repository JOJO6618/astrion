# macOS 内置浏览器集成验证版

本轮将已确认的 `demo/browser-component/` 设计接入 Astrion macOS Electron 桌面壳，验证用户手动浏览真实网页。Agent / Chat 共用侧栏入口、组件与状态。当前没有智能体操作、MCP 或 Playwright 控制通道；Windows、Docker 和普通网页端尚不提供此能力。

## 结构和职责

- `desktop-electron/src/browser/index.js`：管理器、可信身份连接、IPC、布局与生命周期。
- `policy.js`：网页安全选项、URL、作用域、sender 与矩形校验。
- `profile.js`：用户资料、历史、下载元数据和持久存储。
- `tabs.js`：真实 `WebContentsView` 标签、网页事件、popup 接管、快捷键与原生矩形。
- `features.js`：下载、站点权限与待处理请求回收。
- `preload-main.cjs`：只向可信主 renderer 暴露最小 `astrionBrowser` 桥。
- `static/src/components/browser/`：标签栏、导航栏、管理页、菜单、权限弹窗和原生表面同步。
- `static/src/stores/nativeBrowser.ts`：共享界面状态、连接与布局序号。
- `AppShell.vue` 共用挂载；`ConversationSidebar.vue` 仅在 macOS 桌面桥存在时显示入口。

网页使用同一 `BaseWindow` 的原生视图，不使用 iframe、截图或视频流替代。网页无 Node、无可信 preload，开启 sandbox、contextIsolation 和 webSecurity。

## 身份、标签和生命周期

`connect()` 从可信后端 `/api/session-status` 获取已登录用户名，不接受 renderer 自报用户。相同用户跨工作区、Agent / Chat 共用网站资料、历史和下载记录；不同用户名派生不同 profile。

浏览器标签 owner 由 profile 与 `[mode, workspaceId, stableDesktopTabKey]` 共同决定。消费 `conversationTabs` 的权威稳定标签 key，不改写桌面导航协议，也不用新建后会变化的对话 ID 作标签身份。

**手动浏览不依赖模型配置（2026-11-08 空白修正）**：面板框架和布局占位共用页面可见条件，标签栏/地址栏不等待 scope 才挂载。模型未配置可导致对话初始化失败、active_key 为空；在本 renderer 已 hydrate/attach、明确新建路由、导航 error、无 active_key、nav.key 对应仍存在的同模式 new 标签时，手动浏览只读取这个稳定目标作为 owner，不调用 commit/activate/create，不伪造已提交对话。有 workspace_id 的 URL 必须与目标相符；pending、旧 renderer、已关闭目标、历史/独立路由不得借用该归属。等待或归属不可用时保留 chrome 并显示准确等待态，连接控件禁用；切换状态清除旧连接错误。导航观察器在完整 Pinia patch 后消费。

IPC 只接受指定主 webContents 的顶层可信来源及聊天路由。每次连接产生新随机 lease，命令和布局检查 lease；布局另有单调 sequence。renderer 全量重载和离开聊天路由退役旧连接，同文档 `/new` 到 `/conv_*` 不丢标签。前端异步响应同时检查连接 epoch、lease 与 revision。

**等值快照不能触发重连或重置（2026-11-08 稳定性修正）**：连接侦听使用 Vue 多来源 `watch([() => open, () => serializedScope], ...)`，菜单/地址/查找重置使用 `watch([() => activeId, () => activeUrl], ...)`。禁止写成返回新数组的单来源 getter；轮询会替换对象，即使字段值相同，单来源新数组仍触发回调。保持完整 patch 后消费，不能用停用桌面轮询掩盖问题。真实归属/网址变化仍应重新连接或更新页面状态。用户要求“…”菜单无阴影，`BrowserMenu.vue` 本体不设 box-shadow。复现及持续更新回归见 `test/2026-11-08_浏览器轮询稳定性/README.md`。

普通地址导航仅允许 HTTP、HTTPS 和 `about:blank`，禁止带用户名密码的 URL。搜索输入转成搜索地址。网页新窗口进入浏览器标签，保留 Chromium opener / noopener 语义，不给新网页附加可信桥。

## 资料路径与持久化边界

两类资料使用不同根目录：

| 资料 | 位置 |
| --- | --- |
| 历史、标签恢复和下载元数据 | `<desktopDataRoot>/browser/<profileHash>/metadata.json` |
| Chromium Cookie、cache 和网站存储 | `<app.getPath('sessionData')>/browser/<profileHash>/chromium/` |
| 实际下载文件 | `~/Downloads/Astrion/`，重名自动追加编号 |

默认桌面运行数据根是 `~/.astrion/astrion-desktop`；正式应用的 sessionData 默认通常为 `~/Library/Application Support/Astrion`，显式 `--user-data-dir` 会改变它。

**macOS Electron 35 的 Network Service sandbox 仅授权 sessionData 父目录。** 将 `session.fromPath()` 指向任意运行数据根时，即使内存 Cookie 读写和 `flushStore()` 返回成功，也可能没有 Cookie 数据库并伴随 cache 创建失败。本实现将 Chromium 资料放在 sessionData 内；不得通过关闭 sandbox、webSecurity 或加密来掩盖持久化问题。

应用内迁移运行数据目录目前只迁移 desktopDataRoot，不自动搬迁 Electron sessionData 中的网站资料；更换 Electron userData/sessionData 后也不会自动继承旧 Cookie。清理资料只作用于当前用户 profile。

## 原生网页与 DOM 浮层

原生网页层高于主 renderer 的 DOM。浏览器菜单、内部管理页、站点权限弹窗、调宽，以及与网页矩形相交的应用浮层打开时，隐藏原生网页；关闭后恢复同一网页实例。查找和设备工具栏占布局行，网页矩形相应缩小。

**验证版打开 DOM 菜单时网页会暂时隐藏。** 后续新增浮层应标记 `data-native-browser-obscures`，或纳入 `useBrowserSurface.ts` 的遮挡选择器；不能只调 DOM z-index。

## 当前能力

- 多标签、新建、关闭、切换、拖动排序和标签搜索；每个 owner 最多恢复 12 个标签，最多 48 个活跃标签。
- 地址输入与搜索、前进后退、刷新/停止、页面查找、独立缩放、设备尺寸模拟。
- 真实历史与下载管理、下载取消、访达定位、页面打印和保存截图。
- geolocation、notifications、media、clipboard-read 的自定义许可交互；其他权限默认拒绝。许可只保留在本次运行，过期或归属变化时拒绝待处理请求，系统级授权仍由 macOS 决定。
- 三主题、自定义面板宽度和当前用户浏览资料清理。

密码保存、密码自动填充、从本机浏览器导入密码/Cookie/历史尚未接入。管理页如实显示此状态，不模拟导入成功。设备模式是 Chromium 尺寸模拟，不代表真实手机、网络或传感器环境。

## 验证和运行

脚本和命令见 `test/2026-11-08_内置浏览器集成/README.md`。

已验证：

- 真实 Electron 原生模块功能回归 24 项通过，证据 `_experiments/browser-native-integration/1794079811008/results.json`。
- 两个独立 Electron 进程之间持久 Cookie、历史恢复通过，证据 `_experiments/browser-native-integration/persistence-1794080073/results.json`。
- 根前端构建通过（tsc / stylelint / Vite）；本次前端文件定向 ESLint 零警告；Vue 模板未绑定引用检查通过。
- 包内 8 个 Electron 源文件与源码一致，manifest 和 567 个前端资源引用与本次构建一致。
- 打包应用隔离启动通过：Electron userData/sessionData 隔离、自带 Python 后端、健康接口、主页面和哈希入口资源均通过。证据 `_experiments/browser-native-integration/packaged-1794081857102/results.json`，测试进程回收记录同目录 `cleanup.json`。

全量 lint 仍有其他存量文件的格式警告；额外 vue-tsc 在工具启动阶段与现有 TypeScript 不兼容，未更换依赖。上述正常构建与定向校验不能代替真实视觉验收。

本地验证应用：`_experiments/macos-browser-20261108/mac-arm64/Astrion.app`。ARM64，Electron 35.7.5，未作正式签名/公证，不发布，不覆盖已安装应用。

建议用同目录上一级的 `启动内置浏览器验证版.command` 验证：它明确设置独立运行数据根和 Electron userData，资料保存在 `macos-browser-20261108/validation-profile/`，首次需配置验证环境。脚本已做 Bash 语法检查；相同隔离参数已通过打包启动冒烟，脚本的 Finder 双击交互尚需用户验收。

若直接双击 `Astrion.app`，会使用现有 Astrion 的默认/已设置运行数据根与默认 Electron 资料目录。先退出旧桌面版，以免单实例锁只激活旧应用。直接打开 app 并不会自动隔离真实资料。

界面视觉、真实网站登录、鼠标键盘、输入法、长期运行、打印和系统媒体权限仍需实机验收；本轮自动回归不等于完整浏览器产品验收。

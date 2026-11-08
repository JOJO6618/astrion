# 浏览器无模型空白回归

## 触发与根因

隔离试用资料中未配置任何模型，创建新对话返回“尚未配置任何模型，请先在设置页的「提供商」中添加”。桌面导航因此为 `error`、`active_key` 为空。旧 NativeBrowserHost 只有 scope 有效才挂载整个 BrowserPanel，而 AppShell 在 open 时就加右侧 padding，产生空白区域且没有任何浏览器 chrome。

## 修正

- 面板 chrome 和 AppShell 占位共用页面可见条件，不把 scope 当作整个 UI 的挂载条件。
- `browserScope.ts` 在已 hydrate/attach、当前 renderer 有所有权、无 active_key、导航 error、有明确现存同模式 new 目标时允许手动浏览；URL 指定 workspace 时必须相符。支持桌面默认 `/` 新建入口、`/new` 和 `/chat/new`，不修改桌面标签、导航提交或模型配置。
- pending、旧 renderer、关闭目标、历史 URL 和独立页面不能借用失败目标。已有 activeTab 但导航失败时保持暂停，防止误用旧页面。
- 无连接时地址栏/新标签/导航操作禁用，显示标签等待态；suspend 清掉旧连接错误。导航观察器采用 post，避免消费半个 Pinia patch。

## 运行

在项目根执行：

```bash
node test/2026-11-08_浏览器无模型渲染/build.mjs
./desktop-electron/node_modules/.bin/electron test/2026-11-08_浏览器无模型渲染/native.cjs
```

原生命令需要本机窗口和 loopback 监听；命令沙箱拒绝时申请单次完全访问，不降低沙箱配置。使用真实 Vue 组件、Pinia、i18n、生产 preload 和生产 NativeBrowser，资料和本机 HTTP 夹具全部隔离在 `_experiments`；不启动用户后端、不读凭据、不截图、不代替视觉验收。夹具构建目录为 `_experiments/browser-renderer-regression/`。

最终通过证据：`_experiments/browser-native-integration/no-model-1794109827329/results.json`，17 项。

覆盖真实标签栏/地址栏/新标签页渲染、无模型生产 IPC 连接、地址表单提交至实际原生网页、Vue 菜单避让、pending/committed/foreign/settings 生命周期，以及根路由、工作区不匹配、历史路由、关闭目标、残留 active key 和跨模式的归属守门。断言全程没有请求模型或 Chat API，Vue 运行错误为零。

最初一次夹具因 Vite library 模式保留 `process.env.NODE_ENV` 而未挂载；已在夹具构建明确生产环境常量，不能把该夹具故障算成生产代码错误。未改项目 Vite 配置或依赖。

## 修正版产物

`_experiments/macos-browser-20261108-fix1/mac-arm64/Astrion.app`，本地 ARM64 未正式签名/公证目录包。新入口 `_experiments/macos-browser-20261108-fix1/启动浏览器修正版.command` 沿用旧隔离试用资料 `macos-browser-20261108/validation-profile/`，用户须先退出旧试用版。不会覆盖旧 app 或安装版。

根前端构建和本次浏览器组件/状态/文案/AppShell 定向 ESLint 通过。包内 8 个 Electron 源文件和 manifest/567 个前端资源引用与当前源码/构建一致。修正版打包启动另使用独立临时资料，通过五项检查，证据 `_experiments/browser-native-integration/packaged-1794110200352/results.json`，同目录 `cleanup.json` 记录回收完成。

```bash
BROWSER_STARTUP_BUNDLE="$PWD/_experiments/macos-browser-20261108-fix1/mac-arm64/Astrion.app" node test/2026-11-08_内置浏览器集成/packaged-startup.cjs
```

17 项 Vue/native 回归与打包启动不等于用户的完整视觉、鼠标键盘或真实站点验收。用户正在运行的旧试用版未被本次测试关闭或替换。

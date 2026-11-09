# Windows 内置浏览器测试版 r2 · 2026-11-09

基于远程 `40d3c5a9` 加 Windows Tauri + WebView2 适配。这一版增加密码 CSV、Chrome Cookie 授权迁移和本地 file 网页。Agent 与 Chat 共用界面，AI 工具只向 Agent 主对话开放。未安装、未发布到更新通道。

## 启动和隔离

完整解压 ZIP 到可写目录，双击 **Start-Browser-Test.cmd**。请使用脚本启用全部隔离参数。首次没有模型时仍可手动浏览；工作区引导请选择测试目录内的工作区。

仓库中已解压目录的 CMD 指令：

```cmd
"E:\astrion\agent-Specialization\output\Astrion-Windows-Browser-Test-20261109-r2\Start-Browser-Test.cmd"
```

r2 使用独立 identifier 和目录，不覆盖 r1。后端数据、网站 profile、主页面 WebView2、默认工作区、快捷对话设置都位于本包 `validation-profile/`，不继承正式版或 r1 的资料。不要复制真实浏览器 profile 或正式版配置进测试包。

手动下载仍保存到系统 `Downloads/Astrion/`；AI 下载默认保存到本测试工作区 `.astrion/browser_downloads/`。密码库使用当前 Windows 用户 DPAPI，不可作为跨机器或跨用户的迁移文件。本包是 debug GUI 构建，不用于衡量 release 性能。

## 优先验收：密码和 Cookie

### 密码 CSV

1. 在 Chrome 打开 **Google 密码管理工具 → 设置 → 导出密码**，下载 CSV。
2. 在 Astrion 浏览器 **导入 → 导入导出文件 → 选择密码 CSV** 选择文件。
3. 核对可导入、跳过、失败数量；已有密码不同时，明确选择保留哪一版。
4. 点击“导入可用记录”。在密码管理页核对账号，再用测试网站检查填充。

支持 UTF-8/BOM、带引号的逗号和换行、中文；预览不显示密码。最多 4 MiB、10,000 条。CSV 是明文密码文件，迁移后请删除，无需发送给 AI 或开发者。

### Cookie 授权迁移

1. 打开包内 **chrome-cookie-migration/README.md**，按说明在保存登录状态的 Chrome 用户资料中加载已解压扩展。
2. 从扩展菜单打开 **Astrion Cookie Migration**，填写要迁移的域名（包含其子域名）。主动同意后，点击“授权并导出”，处理 Chrome 网站权限提示。
3. Astrion 选择 **导入导出文件 → 选择 Cookie 迁移文件**，选择下载的 `astrion-cookies-*.json`。
4. 预览后明确选择保留或覆盖已有 Cookie，再确认导入。分区 Cookie 保留分区键，普通和不同分区的同名 Cookie 分别处理。
5. 退出重启后检查测试站点。迁移结束后删除含凭据的 JSON，可移除扩展并关闭开发者模式。

扩展不上传数据，不访问其它未指定网站。Cookie 写入后会原生回读核对，失败不会计为成功。迁移不包含 localStorage、IndexedDB、设备密钥或网站服务器状态，因此 Cookie 成功写入不保证每个网站仍然登录；登录流程涉及多个网站时，需要填写相关域名。

### 浏览记录与旧版资料

此来源继续只读 Chrome 数据库，不修改来源。v20 应用绑定加密仍不能直接读库解密；新版密码和 Cookie 请用以上主动导出方式。浏览记录的非 HTTP/HTTPS 地址、无效地址、缺失/无效时间分开统计，不再统一显示“记录格式无法导入”。

## 本地 file 网页

直接在浏览器地址栏输入本机文件 URL，例如 `file:///E:/your-folder/index.html`（替换为实际文件）。路径支持中文和空格，网页可以加载相邻的 JS/CSS、图片和相对链接，可在本地页与普通网页之间后退/前进。macOS 源码也已支持 `file:///Users/your-name/folder/index.html`。

本地网页没有 Astrion/Tauri 可信桥，不安装密码适配器。手动 file 浏览不会扩展 AI 工具权限：本地标签当前只供用户手动使用。拒绝远程主机/UNC 的 file URL、凭据及非法编码路径；远程网页不能用跳转或 popup 打开任意本地文件。保留 Chromium 原有安全限制，依赖跨源 fetch、ES modules 或服务端路由的项目仍可能需要本地 HTTP 服务。

Windows 已做真实 WebView2 本地文件回归。macOS 本轮只做生产 JS 模块的策略/事件回归，Mac 实际加载与视觉仍需实机验证，未打包 Mac 应用。

## 其它回归建议

- 两个网站、多标签、新窗口、前进/后退、刷新、查找、缩放、设备尺寸、Ctrl+L/F/T/W。
- 菜单、账号提示和其它 DOM 浮层不被原生网页遮住；改变窗口尺寸和 Windows 100%/125%/150% 缩放，试中文输入法和跨显示器。
- 保存/更新测试账号密码，刷新自动填充、多账号选择；密码复制未被改动时约 30 秒后清理。
- 下载进度/取消/定位，关闭标签后继续下载，打印/截图另存、相机/麦克风权限。
- Agent/Chat、工作区与对话间的标签归属；当前用户共享登录资料。
- AI 浏览器需在测试环境配置模型，并在 **Agent 输入栏“+ → 浏览器”** 开启。测试拒绝审批、停止、对话隔离及网页对话框。

## 自动验证证据和边界

包内 `verification/` 保存实际报告。最终生产模块写入/重启恢复两进程通过 **39/43 项**，涵盖 CSV 特殊字符、Cookie 分区键、回读、覆盖、重启恢复及 file 相对资源、导航和远程阻断。Rust 12 项、AI 工具原生行为 29 项、导出解析 9 项、旧导入 4 项、共享 UI/桥 22 项通过；前端 ESLint/tsc/stylelint/Vite 通过。

独立 Chromium 149 的扩展回归 7 项通过，生产脚本确实调用 Chrome Cookie API 导出分区数据；测试夹具只预授权合成 example.test 网站，**真实 Chrome 的权限确认弹窗仍由用户验收**。Electron 生产模块使用原生视图替身的 9 项策略回归通过，不等同 Mac 原生实机验收。

包内实际启动、隔离、页面资源、窗口、关闭和后端回收的结果见 `verification/package-smoke.json`。上述测试未读取真实 Chrome 密码或 Cookie，不代替真实网站、真实模型、输入法、长期运行、多 DPI 和系统对话框验收。

## 反馈

请提供操作顺序、错误/统计、去掉个人参数的网站地址、Windows 缩放比例与是否跨显示器。可附截图或短录屏，不要发送 CSV、Cookie JSON、整个 validation-profile 或密码库。后端日志位于 `validation-profile/host/logs/`。

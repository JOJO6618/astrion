# Rust 宿主文件与系统交互迁移交接

用户要求暂停开发和测试。以下是当前工作树的落盘状态、暂停前获得的证据和恢复后待验范围。八条路由的功能与现有调用以 `server/status/file_open.py`、`static/src/utils/downloadFile.ts`、`static/src/utils/openExternal.ts` 和 `static/src/components/panels/GitChangesPanel.vue` 为依据。会话导出保存的响应契约来自 `server/conversation_transfer.py`。

## 已落盘

- `backend-rust/src/native_actions/routes.rs` 写入八条路由的处理器。工作区路由包含 `POST /api/project/open-in-file-manager`、`GET /api/project/file-open-apps`、`POST /api/project/open-file-with-app`、`POST /api/system/open-file` 和 `POST /api/system/save-file`。`GET /api/project/app-icon`、`POST /api/project/pick-folder`、`POST /api/system/open-external` 无需工作区。两个路由组合都检查 `BrowserSession`。外层 Host Web 认证、请求来源和 CSRF 检查仍取决于共享装配。
- `scope.rs` 写入工作区及对话执行根解析、存在性和文件类型校验。源码包含历史 `/workspace/` 别名和 Markdown 绝对路径恢复、缺失路径的最近存在祖先检查，以及符号链接越界检查。Git 文件使用 Git 根解析相对路径，结果仍需位于注册工作区。打开文件管理器的目录限制在注册工作区内。验证工作树的相对路径使用既有 `Worktree::execution_root`，失效绑定按无效路径处理。以上行为尚未经测试。
- `platform.rs`、`macos.rs`、`macos_workspace.js` 和 `windows.rs` 写入平台实现。macOS 使用系统 `osascript`、`open` 和 AppKit `NSWorkspace`，JXA 源码经 `include_str!` 嵌入。Windows 使用原生 COM 文件对话框、`ShellExecuteW` 和 Shell 文件关联。其他平台通过 `xdg-open` 打开路径与链接，原生选择两项按平台不支持处理。当前实现没有增加桌面壳文件、运行时依赖或打包脚本改动。
- 选应用时会按请求目标文件重新枚举候选并逐字匹配 `app_id`。macOS 应用图标 URL 带进程内签发的证明，端点仅处理带有效证明的候选 ID，PNG 成功响应显式声明 `image/png`。现有前端消费候选中的 `icon_url`。旧接口曾直接接受应用包绝对路径，直接请求行为有变化。
- `save.rs` 写入 `save_file(source: &Path, suggested_name: &str, english: bool) -> Result<Option<PathBuf>, SaveError>` 和 `save_file_from(file: File, original: &Path, suggested_name: &str, english: bool) -> Result<Option<PathBuf>, SaveError>`。调用方先验证身份和源文件访问权，在阻塞线程调用。`Some(path)` 表示保存，`None` 表示取消。`SaveError` 区分 `Unsupported`、`Dialog(io::Error)` 和 `Copy(io::Error)`。会话传输智能体已获接口签名，可按旧格式返回 `{"saved": result.is_some()}`。工作区保存路由另返回 `cancelled: true` 或实际路径。保存实现固定源文件句柄，先在目标目录写临时文件，成功后替换用户选择的条目。
- 独立测试目录只有合成文本 `test/2026-11-26_Rust宿主交互/fixtures/example.txt`。`mod.rs` 已声明测试模块，指向同目录下尚未创建的 `native_actions.rs`。`routes.rs` 留有用于替换平台适配器的测试入口，访问级别及测试注入方式尚未核对。

## 共享装配

当前 `backend-rust/src/lib.rs` 与 `backend-rust/src/host_web.rs` 均未出现 `native_actions` 引用。主侧负责如下接线。

1. 在 `lib.rs` 导出 `pub mod native_actions;`。
2. 在 `host_web::routes` 复制 `paths` 并构造 `let native = astrion_headless::native_actions::NativeActions::new(gateway.runtime.clone(), data.to_owned(), move || native_paths.catalog());`。
3. 将 `native.workspace_router()` 合并到应用 `workspace_scope` 之前的 `scoped`，将 `native.system_router()` 合并到 `api`，两组均由现有 `auth.protect_api(api)` 保护。路由本身不应由 Gateway Bearer 路由直接暴露。
4. 路由清单工具的生产挂载锚点需要登记这两个构造器，并按项目既有规则核对 GET 的 HEAD 和 Python 自动 OPTIONS 的处理范围。

## 暂停前的验证证据

- 在本机 Darwin arm64 运行系统 `osascript` 的 JXA 参数探针，`--` 后含空格和中文的输入按原值进入 `argv`。
- JXA `apps` 动作用合成文本 `fixtures/example.txt` 实测返回合法 JSON `[]`。当前沙箱会话没有得到该文件的 LaunchServices 关联结果，候选枚举的非空结果、排序、默认应用和 fallback 的 HTTP 表现待验。
- JXA `icon` 动作用系统自带 TextEdit 作为只读图标探针，返回以 `iVBORw0KGgo` 开头的 base64 数据。这只核对了 AppKit 输出前缀。尚未解码完整 PNG，也未验证路由 MIME 和图标签发流程。探针未启动该应用、未更改系统应用及真实用户安装。
- Rust 模块尚未通过编译，未运行自动化回归、真实打开动作或原生对话框，未构建或启动本模块对应的桌面包。当前只发现 `aarch64-apple-darwin` Rust 目标，未发现 Windows 交叉工具链。此前发出的保存模块补丁在工具结果回填前中断，暂停前已只读确认补丁实际写入。

## 恢复后待验

1. 先补独立测试并完成共享装配，再跑 Rust 格式及编译检查。使用平台替身和隔离数据，验证八条 HTTP 路由的状态码与响应、无会话/无 CSRF/仅 Bearer 拒绝、无工作区时的三个系统路由、未知或显式工作区 ID、跨工作区对话 ID 和无效工作树。补查只有空工作区时，外层 `workspace_scope` 的响应是否符合 Python 的 `no_workspace` 契约。
2. 用合成目录覆盖相对路径、绝对路径、Markdown 少根斜杠、旧 `/workspace/`、`..`、内外符号链接以及缺失子路径、文件/目录差异和硬链接差异。重点核对注册在仓库子目录时 Git 根行为，以及外置已授权工作树的边界。现实现同时接受工作区与验证通过的活动执行根。Python 默认打开另做了工作区包含检查，保存仅做路径解析。两处兼容和收紧选择需经测试固定。检查 Windows 盘符相对路径、UNC、设备路径和 NTFS 数据流表示。
3. 核对任意 URL 只允许 HTTP(S)，运行时验证使用本地测试地址。验证候选只来自系统、过期/伪造/换文件候选拒绝、图标证明和 `image/png`，以及取消、选择、同一源文件、复制错误、外部目标和目标符号链接的保存行为。当前保存通过替换目标目录项消除符号链接重定向，和 Python `shutil.copy2` 可能跟随目标链接的行为有差异。检查覆盖保存时的文件权限与时间戳。
4. 在隔离 macOS GUI 实测选择、取消、打开系统文件管理器、默认程序、指定应用、图标和回环 HTTP(S)；在 Windows 环境核验 COM vtable、取消码 `0x800704C7`、Windows Shell 关联及文件打开、关闭和保存。Windows 实现以现代 COM 为入口，暂未移植 Python 旧式文件夹对话框回退。Linux 验收空候选、不支持对话框以及 `xdg-open` 分支。应用启动接口接收路径，需复核路径验证和系统打开之间的并发替换风险。
5. 包体验收与主侧打包计划同步。现有 `npm --prefix desktop-electron run build:rust:app` 可构建本地未签名目录包。检查包内 `runtime/rust/astrion-headless`、无 Python 运行时、资源可读、应用实际启动该二进制，再在隔离的 `ASTRION_DATA_ROOT` 和 Electron 用户资料目录中复验。真实用户安装保持原样。

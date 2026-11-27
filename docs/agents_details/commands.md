# 启动 / 构建 / 打包命令（完整）

> 本文由 `AGENTS.md` §2 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 2) 当前可用启动/构建命令（已按代码核对）

### Host Web

- 构建 Rust 后端：`cargo build --locked --release --manifest-path backend-rust/Cargo.toml`。
- Host Web 通过 `./start.sh` 或 `start.bat` 启动 Rust 服务，沿用 `.env`、`settings.json` 和环境变量。
- 可选参数：`--path ./project --port 8091 --thinking-mode`。未指定监听地址时使用 `127.0.0.1`。`--bind`、`WEB_SERVER_HOST` 或 `server.host` 可显式指定 IP。`--debug` 为 Rust 进程启用诊断。
- 自备的二进制和资源通过 `ASTRION_RUST_BACKEND_BIN` 和 `ASTRION_RUST_RESOURCE_ROOT` 配置。资源目录包含已构建的 `static/dist`。
- 独立 headless 使用 `astrion-headless --workspace DIR --data-dir DATA_DIR --port 8092`，与 Web 共用相同数据目录时只运行其中一个进程。
- 旧 Flask 对照测试需要相应历史版本的测试依赖及源码，可用提交 `fb0a27032` 作为参考；源码版和安装包均不加载该实现。

### Frontend（根目录）
- 安装依赖：`npm install`
- 构建：`npm run build --silent 2>&1 | tail -n 5`（默认只看最后 5 行，减少 Vite 构建资源列表等无关输出）
- 开发监听（当前脚本是 build watch）：`npm run dev`
- Lint：`npm run lint`

### CLI（opentui / bun）
- 运行环境：bun ≥1.3（opentui FFI；node ≥26.4 亦可，本机 node v24 不可用）
- 开发启动：`bun cli/src/main.tsx`（或 `npm --prefix cli run dev`）
- 类型检查：`cd cli && ./node_modules/.bin/tsc --noEmit`
- 可执行命令名：`astrion`（已装到 `/opt/homebrew/bin/astrion` → `cli/bin/astrion` symlink，bun wrapper 支持 symlink 解析；`--version`/`--help` 快速退出不启 TUI；后续 `bun build --compile` 单文件分发）
- CLI 自启动默认使用 Rust。源码安装先运行 `cargo build --locked --release --manifest-path backend-rust/Cargo.toml`；CLI 与 Host Web 共享源码 `.env`、部署设置和环境变量的解析规则，默认使用源码资源。可通过 `ASTRION_RUST_BACKEND_BIN` 和 `ASTRION_RUST_RESOURCE_ROOT` 指定二进制和资源。显式连接已有 Gateway 不加载源码 `.env`；Rust 实验模式仍使用隔离数据目录和经 `node desktop/scripts/prepare_rust_backend.mjs` 准备的 staging 资源。
- 依赖安装：沙箱网络受限时不跑 npm/bun install，从 `cli-redesign-demo/node_modules` 铺平（版本以 `cli/package.json` 为准）

### Desktop（`desktop-electron/`，macOS 与 Windows 共用 Electron/Chromium）
- **引擎迁移**：macOS 自 2026-09-28 起使用 Electron（commit b53e8c3a）；Windows 迁移于 2026-11-14 以 `7cb8f769` 合入 main，同日正式发布 **0.10.0**。此前 0.10.0–0.10.3 是迁移测试包编号，历史产物与验证报告保留原编号。原 Tauri/WebView2 代码保留作旧版和原生模块来源。**web 版只支持 Chromium 系浏览器**（Safari 不再兼容）。正式身份真实界面更新验收入口见 `test/2026-11-14_Windows更新实机/README.md`：独立标准用户中原版 0.9.3 经正式源升至 0.10.0，以及 Electron 0.10.0 经本地测试源升至未发布 0.10.1 均通过；不等于原账户历史故障已根治。
- **Windows 构建**：Windows x64 原生环境执行 `node desktop-electron/scripts/build_windows.mjs`，`--dir` 仅生成目录包，已有本次前端及 helper 可加 `--skip-frontend --skip-native`。目录包与 NSIS 均内嵌 Rust Host 后端，固定 `--publish never`。版本与说明独立于 macOS，权威为 `desktop-electron/windows-release.json`。NSIS、Job 回收、一次性 stdin 凭据与隔离烟测见 `docs/windows_electron.md`。
- 形态：Electron 主进程 + 内嵌 Rust 后端 sidecar。BaseWindow + **双 WebContentsView**（chrome 46px 独立标签条常驻不闪动 + main 加载 `http://127.0.0.1:<动态端口>/`，前后端同源）；原生控制桥只负责版本、更新、窗口及数据目录等能力，标签导航走后端命令与版本快照，见 `docs/desktop_navigation.md`；壳经 preload + contextBridge 注入 `window.__ASTRION_DESKTOP__`/`__ASTRION_CHROME__`
- 开发：`cd desktop-electron && npm install && npm run dev`（装依赖需 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`）；前端需先 `npm run build`，后端需先构建 Rust。桌面后端固定使用 Rust。
- macOS 的 `npm run build` 与 `npm run build:dir` 从相同 staging 目录打包 Rust 后端。本地目录包用 `npm run build:rust:app`，输出 `dist-rust/mac-arm64/Astrion.app`，包含原生 Quick Chat 组件与更新策略界面资源。该入口使用 ad-hoc 签名并运行严格校验。
- **构建编号与更新策略**：Windows 构建入口与 macOS `npm run build` / `npm run build:dir` 为每次构建生成独立 UUID，写入打包元数据 `astrionBuildId`；Windows 构建回执同时记录 `build_id`。可用 `ASTRION_BUILD_ID` 显式指定 1–128 位安全 ASCII 标识，不同构建应保持唯一。macOS 必须经 `scripts/run_builder.mjs`（npm 脚本已接线）传入编号。修改壳、策略客户端或内嵌后端后必须完整构建，禁止仅用旧 `win-unpacked --prepackaged` 重包安装器来交付这些修改。启动门槛、官网独立策略服务与先服务后客户端的部署顺序见 `docs/desktop_update_policy.md`；旧客户端不具备新策略能力。
- 关键差异：窗口拖拽走 `app-region` CSS（_tab-strip.scss）；红绿灯 `trafficLightPosition` 一行配置；菜单不占 Cmd+W/Cmd+T（留给前端标签条，关窗=Cmd+Shift+W）；子进程 `ASTRION_IGNORE_DOTENV=1`（防仓库 .env 强制覆盖数据根）；Ctrl+Enter/拖放原生正常（WebKit 特有问题全消）
- 打包发布：`electron-builder`（dmg+zip+latest-mac.yml，签名=Apple Development 证书）；`bash desktop-electron/scripts/release_mac.sh [版本]`；安装后 ~458MB（Chromium 244MB 为换引擎固有代价）。详细坑与决策见 `desktop-electron/README.md` 与项目记忆 `desktop_electron_shell`
- **macOS 快捷对话（0.5.0）**：独立设置分区 `/settings/quick-chat`，仅 macOS Electron 展示。快捷默认工作区/模型留空分别继承 host 工作区目录的 `default_workspace_id` 与个性化 `default_model`，每次唤起刷新；模型须支持图片，不可用时提示，不擅自换模型。启用后保留现有单一 Host 后端与菜单栏；**显式启动应用自动打开主窗口，启动期间的程序坞激活意图保留到后端就绪**，关闭主窗口隐藏而不退出。macOS 快捷窗及截图覆盖层不设置 `skipTaskbar=true`，避免将整个应用转为 UIElement；主应用保持 regular 身份。当前必须先启动应用，登录自启动延后实现。原生 Swift 监听与截图辅助程序由 `build:quick-native` 编译并随包签名，快捷对话来源为 `metadata.quick_entry=true`，快捷菜单按工作区仅查询该来源且先过滤后分页。详细说明见 `desktop-electron/QUICK_ENTRY.md`。
- **macOS 浮窗跨桌面身份（2026-10-05）**：快捷窗与截图覆盖层统一调用 `desktop-electron/src/quick/workspace-visibility.js`，`setVisibleOnAllWorkspaces` 必须同时设置 `visibleOnFullScreen: true` 和 `skipTransformProcessType: true`。只禁用 `skipTaskbar` 不足以阻止 Electron 将应用切换为 UIElement；默认全屏兼容变换也会隐藏程序坞并影响运行点。真实 Electron 的 Foreground 身份检查见 `test/2026-10-05_程序坞全屏浮窗/`。

### Desktop（`desktop/`，旧 Tauri 2 桌面壳及保留的原生模块）

旧版 Tauri 程序与打包入口已退役。Electron 继续复用此目录的 Rust staging、图标、Windows 快捷键和 WGC 原生模块。跨壳更新补签命令与官网下载清单见 `docs/windows_electron.md`。以下为旧壳实现记录。
- **自动更新体系（2026-09-26 新增）**：`tauri-plugin-updater` 无感更新（下载→验签→安装→自动重启）。关键架构：WebView 是 External URL 拿不到 Tauri JS API，壳侧开 **localhost 控制桥**（`src/bridge.rs`：GET /version、POST /update/install、GET /update/progress，手写最小 HTTP），经环境变量 `ASTRION_DESKTOP_BRIDGE_PORT`/`ASTRION_DESKTOP_VERSION` 传给后端；**前端只同源调后端代理**（`server/status/desktop_update.py`：`/api/desktop/update/check|install|progress`），非桌面环境返回 `code:"not_desktop"`。更新清单与安装包托管在 `https://astrion.cyjai.com/downloads/`（`latest-{{target}}-{{arch}}.json` 模板端点，mac/win 清单互相独立）；服务器侧 `regen_manifest.py` 扫描目录重生成 `latest-*.json` + 官网下载页用的 `downloads.json`。前端入口=左下角个人菜单内「软件更新」项（帮助之下、退出登录之上，仅桌面壳渲染；个人按钮与菜单项双红点）+ `SoftwareUpdateDialog.vue` + `stores/desktopUpdate.ts`；启动静默检查一次只亮红点不弹窗
- **双 WebView 对话标签条（2026-10-23 重构）**：顶部 46px 标签条仍为独立 chrome webview，同源加载 `/chrome`；主页面不挂载标签条组件。Tauri `window.add_child` 需 `unstable`，宿主窗口用 `get_window("main")`，主 webview `set_auto_resize(false)`；`layout_webviews` 在 Resized 时按 chrome=(0,0,w,46)、main=(0,46,w,h-46) 手动布局。标签唯一写者改为 `modules/desktop_navigation/`，双页提交 `/api/conversation-tabs/commands` 幂等命令、消费版本快照；主页面协调器复用权威 bootstrap，在提交显示前验证 renderer/navigation 所有权。移除 eval 标签桥、内存派发队列、全列表 PUT、乐观 TTL 与双页计数器。稳定 key 贯穿新建、绑定、关闭；删除与恢复核验联动，设置/工作流/Git 往返由同一管理器处理。完整约束见 `docs/desktop_navigation.md`。⌘T/⌘W 双 webview 各自注册；关闭立即发命令，动画仅为展示。窗口拖拽仍走原生桥。**红绿灯定位**：`traffic_light_position` 在 unstable 下失效（tauri #14072），`position_traffic_lights` 用 objc2 定位，创建及 Resized 时调用。**on_navigation 回环全放行**（任意端口），供预览 iframe 使用，聊天回环链接由前端 capture 进入预览。WebKit 关闭补位后保留 pointer-events 脉冲重算 hover。
- **Windows 无边框模式与平台标记（2026-09-28 新增）**：Windows 下 `create_main_window` 用 `decorations(false).shadow(true)` 去原生标题栏——标签条即标题栏（左侧设置齿轮→`open-settings` 意图→主页面整跳 /settings；右侧自绘最小化/最大化/关闭）；tao 的 `MARKER_UNDECORATED_SHADOW` 保留阴影/Win11 圆角/四边缩放/拖边贴靠；已知限制=悬停最大化键不弹 snap layouts 浮层（自绘按钮拿不到系统悬停识别）。壳双 webview 的 `initialization_script` 注入 `window.__ASTRION_PLATFORM__`（backend.rs `PLATFORM_MARKER`），标签条按它做平台差异化（mac 左侧留 84px 红绿灯位；Windows 收窄改放设置齿轮 + 右侧三大键；快捷键提示修饰键 ⌘/Ctrl 由 i18n `{mod}` 参数注入）。三大键链路：`POST /api/desktop/window/control`（action 白名单 minimize/maximize-toggle/close）+ `GET /api/desktop/window/state`（供最大化/还原图标切换，chrome 侧 1s 轮询收敛贴靠等外部状态变化）→ 桥 `/window/control` `/window/state`。双击空白=最大化/还原；拖拽用 4px 位移阈值模式（按下即触发的 start_dragging 会吞掉双击序列）。**关键坑：桥内窗口句柄一律 `app.get_window("main")`**——tauri 的 `get_webview_window` 内部要求窗口所有 webview 与窗口同 label（`is_webview_window` 用 `.all()` 判定），主窗口挂 chrome 子 webview 后恒返 None，曾致三大键/拖拽整体失效且 mac 上长期未被发现
- **Ctrl+Return 拦截器（2026-09-28 新增）**：macOS Sequoia（15+）把 Ctrl+Return 变成系统级「打开上下文菜单」快捷键，按键在 AppKit 层被消费、DOM keydown 收不到——输入栏 Ctrl+Enter 发送失效且弹系统右键菜单（页面内 preventDefault 拦不住系统快捷键）。根治在原生层：`backend.rs::install_ctrl_return_send_interceptor` 注册 NSEvent 本地监视器（block2 + objc2-app-kit NSEvent feature），命中 ctrl+return（keyCode 36/76，无 cmd/option）即吞掉原生事件并向主 webview eval 注入合成 ctrl+Enter keydown（派发到 `document.activeElement`，输入栏 ProseMirror 既有 handler 收到后走正常发送链路）
- **运行数据目录与启动前迁移（2026-09-29 起）**：壳侧设置文件 `~/.astrion/rundata_settings.json`（`ASTRION_DESKTOP_DATA_ROOT` 优先），设置页「通用」可换目录并选择是否迁移。迁移统一在**下次启动、spawn 后端之前**执行，避免后端运行期继续写源目录造成复制校验失败或数据撕裂。Windows Tauri 0.4.2 有本机实测（10 次失败 9 次）；macOS Electron 现也采用相同时机（`desktop-electron/src/rundata.js::runPendingMigration`）。待迁移时先显示进度窗口，再迁移并启动后端：Tauri 由 `backend.rs::start_backend_and_create_window` 建桥端进度页；Electron 由 `lifecycle.js::startBackendAndCreateWindow` 建 BrowserWindow 并轮询桥端进度。迁移失败会回退原目录并保存 `last_error`；若回退状态无法安全落盘，则不启动后端。Windows 命令环境由 `shell_env.rs::shell_path_additions` 读取 HKLM/HKCU 注册表 PATH 并只增量追加。平台判定统一走 `static/src/utils/desktopPlatform.ts`（Electron 注入 `darwin`/`win32`，Tauri 注入 `macos`/`windows`）；Windows Tauri 目录选择器复用 `/api/project/pick-folder`。迁移是破坏性操作，保留成功与失败回滚覆盖。

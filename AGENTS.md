# Repository Guidelines (Code-Verified)

> 最后核对代码：2026-10-08 ｜ 压缩整理：2026-10-08
> **本文只放硬约束与「改代码必须知道」**。机制细节、历史沿革、逐条清单、完整命令已下沉到 `docs/agents_details/`（已在 `.gitignore` 中取消忽略、纳入版本控制，见 `docs/README.md`）。
> **细则与本文同级**：`docs/agents_details/` 里的每一条都是**硬约束**，不是可选背景资料；**改动某机制前必须先读对应细节文档**（触发条件见下方文档地图）。细则与本文冲突时以细则为准（细则是原样下沉）并回来同步。
> **文档位置（2026-10-08 整理）**：本文直接引用的开发契约文档（`docs/runtime_contract.md`、`docs/providers_api.md`、`docs/dynamic_tool_loading_plan.md`、`docs/运行对话显示快照契约.md`、`docs/conversation_api_costs.md`）已保留在追踪目录 `docs/` 下；其余原 `docs/`、`doc/` 文档已整体移到未追踪目录 `_experiments/docs_archive/`（其下 `docs/`、`doc/` 与原结构同构）。**本文及代码注释中指向已归档文档的引用一律保留原路径写法**，实际文件需加 `_experiments/docs_archive/` 前缀。
> 与代码冲突时一律以代码为准，并回来同步本文与对应细节文档。
> **章节号边界**：主节号（§0–§22）刻意保持与压缩前一致（项目记忆与既有文档按号引用），仅修正了顺序与标题层级，不要重排。**但 17 个编号子标题已下沉**（§1.5.1–1.5.5、§13.1–13.3、§14.1–14.3、§15.1–15.2、§17.1–17.4 等）——按 `§13.2` 这类子号查找时请转到对应细节文档。
> 和用户交流默认使用中文。

## 文档地图（细节去哪查）

> 改下面任一领域前，**必须先读**对应细节文档的完整约束。

| 主题 | 细节文档 | 改什么前必读 |
|------|---------|------------|
| 项目结构 | `docs/agents_details/project_structure.md` | 加/改目录、模块 |
| 运行态数据目录与路径变量 | `docs/agents_details/runtime_data_paths.md` | 路径解析、数据根、日志轮转 |
| 启动 / 构建 / 打包命令 | `docs/agents_details/commands.md` | 启动入口、打包链路 |
| 测试现状与执行方式 | `docs/agents_details/testing.md` | 写测试、跑回归 |
| 前端设计风格统一规范 | `docs/agents_details/frontend_design_style.md` | 写/改任何 UI |
| 独立 Chat 与消息树 | `docs/chat_mode.md` | Chat 页面、分支、九工具、无工作区运行、媒体记忆或 Agent 交接；前端必须复用 Agent 组件 |
| 对话导出／导入与迁移 | `docs/conversation_transfer.md` | 内部直接迁移、跨机器 ZIP、历史不改写、媒体／摘要映射、迁移回执及默认交接工作区 |
| Android 发布联动 | `docs/agents_details/android_release.md` | 发 Android 版本 |
| 权限模式 / 路径授权 / 平台沙箱 | `docs/agents_details/permission_and_sandbox.md` | 权限、沙箱、平台隔离 |
| 运行模式（work_mode） | `docs/agents_details/work_mode.md` | plan/ask/execute 链路 |
| 对话级目标模式 | `docs/agents_details/goal_mode.md` | 目标开始／继续／关闭、暂停、审核及重试 |
| 统一子智能体 | `docs/agents_details/multi_agent.md` | 命名实例、恢复、轮次与通知 |
| 主任务门闸与单写者不变量 | `docs/agents_details/main_task_gate.md` | 新增主任务入口 |
| 工具动态加载 | `docs/agents_details/tool_loading.md` | 动 tool_loading |
| MCP 对话运行态与浏览器隔离 | `docs/agents_details/mcp_runtime_scope.md` | MCP 连接归属、stdio 启动适配、Playwright profile |
| 网页提取与搜索 | `docs/agents_details/web_extract_and_search.md` | 提取链、搜索服务商 |
| 设置页与提供商体系 | `docs/agents_details/settings_and_providers.md` | 设置页、提供商 catalog |
| 预览面板 | `docs/agents_details/preview_panel.md` | 预览服务器、代理 |
| 代码索引 codegraph | `docs/agents_details/codegraph.md` | 使用/重建索引 |
| 子智能体固定权限 / 单次完全访问 | `docs/agents_details/sub_agent_access.md` | 子权限、审批面板 |
| 运行中对话显示快照 | `docs/agents_details/display_snapshot.md` | 会话显示、恢复链路 |
| 桌面标签导航 | `docs/desktop_navigation.md` | 标签身份、关闭、恢复、页面往返及命令协议 |
| Linux host 沙箱 | `docs/agents_details/linux_host_sandbox.md` | Linux 沙箱 |
| Host 网页密码 | `docs/agents_details/host_password.md` | 认证/密码 |
| 主／子 API 花费 | `docs/agents_details/api_costs.md` | 费用统计、定价 |
| CLI 交互与分层 | `docs/agents_details/cli_workflow.md` | 改 CLI 交互/分层 |
| 项目 Git 记录与评审 | `docs/project_git.md` | 仓库扫描、历史 diff、评审启动及工作区深链 |
| CrossPet 本机接入 | `docs/crosspet_integration.md` | 桌宠状态输出、任务观察、模型角色及接入协议 |

## 0) 项目名称

本项目（仓库 `agents`）的**暂定**产品名为 **Astrion**：`Astr-`（希腊语 *astron*，星星/天体）+ `-ion`（粒子/状态后缀）。中文参考「阿斯特里恩」，简称可考虑 **星子**。

- **状态**：**暂定名**，后续若变更需同步更新 `AGENTS.md`（及本地 `claude.md`，未随仓库发布）。
- 压缩时删除了构词细节、意象（星之子／星际粒子）与两层寓意（传递意图／星尘聚合成恒星）；需要追溯时见压缩前的 git 历史版本。

## 1) 当前项目结构（按代码现状）

- **入口**：`main.py`（统一入口，默认 Web + thinking_mode）；`server/app.py`（推荐 Web 入口，转发 `server/app_legacy.py`）；`web_server.py`（deprecated 兼容）；`server/headless_app.py`（CLI 自启动用，无站点路由面）
- **`server/`**：Flask 业务主线，已拆子包 `chat/`、`status/`、`tasks/`、`context/`。**REST 任务轮询是唯一实时通道**（Socket.IO 2026-09-11 整体移除，见 §12.5）
- **`server/runtime/`**：RuntimeContext 三层模型 + RuntimeService（create_task/cancel/guidance/queue/get_task_events），契约 `docs/runtime_contract.md`
- **`core/`**：终端与工具编排；`main_terminal_parts/` 已拆 base + mixin 子包
- **`modules/`**：可复用能力模块（terminal / file / memory / sub_agent / upload_security / user 等；多个子包已拆分）
- **`config/`**：配置拆分（api/limits/terminal/paths…），由 `config/__init__.py` 聚合并加载 `.env`
- **`utils/`**：api_client、日志、上下文与对话工具（多个子包，原入口保留兼容）
- **前端**：`static/src/`（Vue 3 + TS）；`cli/src/`（opentui + React 19，2026-09-11 起替代 Ink 版；旧实现存档 `cli/legacy-ink-src/`）
- **子项目**：`android-webview-app/`、`desktop-electron/`（macOS 现行壳）、`desktop/`（Tauri，Windows 沿用）、`easyagent/`（旧版 Node 子智能体，已不用）、`_experiments/`（本地实验归档，不入 git，见 §7）
- 完整目录职责与监控动画核心文件见细节文档 `project_structure.md`

## 1.5) 运行态数据目录与路径变量（2026-06 更新）

设计目标：运行态数据（对话、用户工作区、日志）默认在 `~/.astrion/astrion/`，**不落在源码树内**。实现见 `config/paths.py`。

- 数据根默认 `~/.astrion/astrion`，可用 `ASTRION_DATA_ROOT` 整体搬迁；其下按 `TERMINAL_SANDBOX_MODE` 分流：host → `host/`，其它 → `web/`
- 路径解析优先级：具体目录变量（`DATA_DIR`/`LOGS_DIR`/`USER_SPACE_DIR`/`API_USER_SPACE_DIR`）> `ASTRION_DATA_ROOT` > 兜底 `~/.astrion/astrion/<mode>`
- **`config/*.json` 分两类**：程序能力类（`docker_risk_markers`/`skill_hints`/`command_blocking_recommended`）锚定源码树；部署级配置（`custom_models`/`host_workspaces`/`auto_approval`/`goal_review`/`host_sandbox_policy`）外置到 `DEPLOY_CONFIG_DIR`，读取走 `config.resolve_deploy_config(name)`
- host 模式同时加载 `host/` 与 `web/` 的用户/工作区（`IS_HOST_MODE`/`WEB_DATA_DIR`/`WEB_USER_SPACE_DIR`），写操作一律落 host
- 日志：API dump 默认关闭（`AGENT_API_DUMP_ENABLED`）；混合轮转见 `utils/log_rotation.py`
- 迁移脚本 `scripts/migrate_runtime_data.py`（先 `--dry-run`）

> 细节（含个人指令拦截 `command_blocking` 语义）见 `docs/agents_details/runtime_data_paths.md`。

## 2) 启动 / 构建 / 打包命令（已按代码核对）

| 用途 | 命令 |
|------|------|
| 依赖 | `pip install -r requirements.txt` ／ `npm install` |
| Web 启动（推荐） | `python -m server.app`（可加 `--path ./project --port 8091 --debug --thinking-mode`） |
| Headless 启动（CLI 自启动） | `python -m server.headless_app --path <cwd> --port 8091 --thinking-mode` |
| 前端构建 | `npm run build --silent 2>&1 \| tail -n 5` |
| 前端 lint | `npm run lint` |
| CLI 开发 | `bun cli/src/main.tsx`（需 bun ≥1.3）；类型检查 `cd cli && ./node_modules/.bin/tsc --noEmit` |
| 桌面壳（macOS，现行） | `cd desktop-electron && npm install && npm run dev`；发布 `bash desktop-electron/scripts/release_mac.sh [版本]` |
| 桌面壳（Windows，Tauri） | `cd desktop && npm install && npm run dev`；发布 `bash desktop/scripts/release_mac.sh [版本]` |

**硬约束**：

1. **绝不能 full 与 headless 双进程并存**——RuntimeService 单例、门闸、审批 Map 都是进程内单例，两者端口/数据目录相同，只是挂载的路由面不同。
2. Desktop 依赖安装需 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`；壳加载的是后端 serve 的 `static/dist`，改前端必须先 `npm run build`。
3. Windows 发布在 Win 机构建、需同一 minisign 私钥；mac/win 的更新说明与版本号自 0.4.2 起**按平台分开维护**（`desktop/` 一套、`desktop-electron/` 一套）。
4. 构建/安装/lint 因沙箱权限失败时的处置见 §5「构建沙箱已知问题」，**禁止改构建配置绕过**。
5. **前端产物带内容哈希（2026-10-15 起）**：`vite.config.ts` 输出 `assets/[name]-[hash:8].(js|css|ext)`；`static/*.html` 仍写固定名，由 `server/entry_assets.py` 在响应时按 `static/dist/.vite/manifest.json` 注入真实文件名（映射来自构建产物，`npm run build` 后才有）。**新增 HTML 出口必须用 `serve_entry_html`，不要再用 `send_static_file` 直发 HTML**；HTML 响应一律 `no-store`。装配样式 `static/dist/style-<token>.css` 由 `scripts/generate_entry_styles.mjs` 生成（**不再写回源码树的 `static/style.css`**）。桌面壳 quick 窗口各自读同一 manifest 注入（Electron `src/quick/entry-styles.js`、Tauri `src/quick/assets.rs`），改哈希规则时必须同步。部署注意 dist 要清理后解压，见 `test/2026-10-15_前端产物hash化/README.md`。

> 完整形态说明、headless 额外挂的路由、Python 解释器探测顺序、打包链路见 `docs/agents_details/commands.md`。

## 3) 测试现状（不要再写过时命令）

- **测试目录规范（强制）**：每次新增测试都在 `test/` 下新建 `YYYY-MM-DD_功能/` 目录（如 `test/2026-10-04_对话压缩/`）；**禁止**把测试文件直接放在 `test/` 根目录或项目根目录。同日同功能的独立批次用 `_02`/`_03` 后缀，不得覆盖已有目录；配套辅助文件放对应测试目录内。
- 无法确认日期的存量测试集中在 `test/历史测试_日期不明/`，不按 mtime 猜日期；移动时必须修正相对路径、辅助文件引用与运行命令。
- **执行方式**：pytest 可按分类目录收集；unittest 必须用具体分类目录作 `discover` 起点，**不能**依赖 `discover('test')` 自动递归（这些目录不是包）。
- 当前仓库**没有** `pytest.ini`/`pyproject.toml`/`tox.ini`，不要把 pytest 当作唯一入口（仅作便捷运行器）。
- 已知坑：标准 Windows Python 上 `python -m unittest test.test_server_refactor_smoke` 会因 stdlib 的 `test` 包遮蔽本地 `test/` 而失败，需用 `discover` 形式。
- 改动 `server/chat/`、`server/status/`、`server/tasks/` 等后端接口时，至少补 `python3 -m py_compile` 与相关回归目录。

> 具体冒烟用例、CLI 最小验证命令见 `docs/agents_details/testing.md`。

## 4) 代码修改约定（实用版）

- **Chat / Agent 尽可能共用代码（强制）**：两者是功能和设计不同的两种模式，尽可能复用前后端代码、组件、样式与通用机制，禁止因为模式不同就维护两套通用实现。已确认的差异通过模式配置、能力边界和必要的专属适配表达；共有 UI 使用同一组件与同一份样式，共有智能体／工具循环使用同一套逻辑，通用修复应同时作用于两种模式。具体差异与共享边界见 `docs/chat_mode.md`。
- **根治优先**：能根治的应告知用户方案与影响范围并询问是否执行；一定范围内的重构可接受，尽量避免打补丁式临时修改。
- **精确读取**：**禁止整文件通读**。先用搜索工具（grep / 符号查找 / 关键字定位）锁定行段，只精确提取相关片段。
- **编辑方式**：优先 `write_file`/`edit_file` 等原生编辑工具，不要用 bash/python 脚本批量改文件（除非原生工具明显不适合）。
- **改动优先级**：后端先改 `modules/`、`server/` 内对应模块，最后才动入口；前端按 `static/src` 现有分层（`app/`、`stores/`、`components/`、`composables/`）；CLI 按 `cli/src/` 分层（入口编排 / 主界面 / Gateway 客户端 / 会话运行时 / 菜单状态机 / 渲染分发 / 通用件 / 数据层 / i18n），新交互先定归属层再动手。
- 涉及 monitor 动画／事件联动时，至少同步检查 `static/src/components/chat/monitor/MonitorDirector.ts` 与 `static/src/stores/monitor.ts`。

## 5) 风格与质量

### 文件长度与拆分

- **单文件最好不超过 1000 行**；超过时优先考虑合理拆分，但**行数不是硬指标**——拆不拆取决于**可维护性**。
- 超约 500 行时评估拆分价值：职责是否单一、是否存在清晰边界（按工具类型／资源／生命周期阶段）、拆后是否提高复用/可读/可测性。
- **不要为了拆分而拆分**：边界模糊、耦合紧密、改动频率低的文件保持现状更划算。Vue SFC 拆 `.vue` 风险高，优先抽 composable 与子组件。

### 语言与验证

- Python：4 空格、`snake_case` 函数、`PascalCase` 类，新增公共函数尽量补 type hints。Vue/TS 保持现有风格，不做无关清洗。CLI React/TS 保留现有渲染与光标修正逻辑，不轻易重写输入框定位。
- 日志优先复用现有 logger/日志路径，不引入大量临时 `print`。
- 提交前至少做与改动相关的最小验证（输出或手工步骤可复现）。
- **视觉验证默认由用户完成**：除非用户特别说明，需要视觉确认的修改（尤其动画/过渡）在构建/lint 通过后交由用户亲自查看，**不要**用 Playwright 截图**或浏览器自动化**代替用户验收。
- 根目录前端构建默认用 `npm run build --silent 2>&1 | tail -n 5`。

### 构建沙箱已知问题（2026-09 实测）

vite 构建的 PostCSS 配置搜索会向上冒泡读工作区外文件，宿主机沙箱内被 EPERM 拒绝导致 build 失败（tsc/stylelint 前置正常）。**确认属于该问题后，禁止通过修改构建配置规避权限限制**；可对本次命令申请完全访问（`run_command` 的 `request_full_access=true` + `full_access_reason`，直接发起申请，无需在聊天里重复询问），或请用户加入路径授权（仅可读）。申请不可用或被拒绝时再与用户讨论路径授权／执行环境。**禁止以任何未经授权的方式规避沙箱限制。**

**通用前置（适用于所有构建/安装/lint 类命令，如 `npm run build`、`npm install`、`npm run lint`）**：因沙箱权限限制失败时（`EPERM` / `Operation not permitted` / 沙箱拒绝写入），**先核对命令和路径，并向用户说明原因**，再走上面的完全访问申请或路径授权流程。

### CLI 当前交互约束（2026-09-11 重写）

- **全部走 Gateway host Bearer 通道**（token 在 `host/data/host_api_token`，工作区经 `X-Astrion-Workspace-Id` 绑定）；**禁止** Web 会话通道（Cookie/CSRF/host-login）。
- `astrion` 启动指令 cwd = 工作区，运行内不可切换；未注册工作区先提示并询问创建。
- 服务自启动：探测无服务才 spawn；**绝不 kill/重启已在运行的进程**；401 自动重读 token 重试一次。
- 审批面板收到新 ID 的 `tool_approval_required` 即自动弹出；等服务端 `tool_approval_resolved` 终态后清理。
- i18n 读 OS 语言设置，启动时定死；所有文案**禁止**按字符长度硬编码布局。Enter=发送、Shift+Enter=换行。
- 不要在未获用户要求时运行交互式 TUI 压测或长时间模拟输入（刷屏占上下文）。

> CLI 完整分层映射（各层对应文件）、运行命令、验证方式与交互细节（含按键、token 路径、spawn 入口）见 `docs/agents_details/cli_workflow.md`。

## 5.5) 前端设计风格统一规范（强制）

> 适用所有前端 UI（`static/src` 为主，`cli/src` 视觉可类比处同样适用）；写/改 UI 必须遵守，遇到存量违规在最小改动内顺手修正。
> 配色基建：两层 token 定义在 `static/src/styles/base/_tokens.scss`，切换见 `utils/theme.ts`；`.stylelintrc.cjs` 三条防回退栏杆，`build` 含 stylelint。
> **配色作用域**：颜色 Token 与字面量限制用于应用本体源码。回复内 `show_ui`／`show_html` 可视化内容遵循 `prompts/show_ui.txt`／`prompts/show_html.txt`：基础界面适配主题，状态、分类、数据系列与事物固有颜色允许使用有意义的自定义配色；不要将源码配色限制扩展为内容只能使用几种主题色。

1. **禁止边缘光晕**：不用任何 glow／外发光／彩色光晕（大范围彩色 `box-shadow`、`filter: drop-shadow` 光晕、模糊伪元素）。只允许中性克制的投影（沿用 `--shadow-*` token），仅在用户明确允许时才可用光晕。
2. **禁止原生浏览器组件**：`<select>`、`<input type=checkbox/radio/range/date/color>`、`alert/confirm/prompt`、原生右键菜单、原生 tooltip 一律换成项目统一的自定义组件。
3. **禁止圆角套娃**：不允许「圆角矩形套圆角矩形套圆角矩形」；容器层级要扁平，优先用分隔线／留白／底色区分。
4. **图标外框对齐**：同组图标按钮的外框（热区/容器）必须统一对齐与尺寸，不能一会居中一会左/右对齐。
5. **图标视觉对齐而非理论对齐**：按视觉重心对齐，对偏移图标（三角/播放/放大镜）微调，使其「看起来居中」。
6. **颜色走三模式 + 两层 Token**：禁止写死颜色字面量（裸 hex/`rgb()`/`hsl()`/`var(--x,#hex)` 兜底），一律用中性语义变量（`--surface-*`/`--text-*`/`--border-*`/`--accent*`/`--state-*`）。三主题：经典=Claude 亮色盘、浅色=ChatGPT 亮色盘、深色=自研中性灰阶；亮色靠灰阶拉层次，不许全塌成白；primary 克制（CTA-only）。半透明 tint 用 `color-mix(in srgb, var(--token) N%, transparent)`。`--claude-*`/`--theme-*` 是过时别名，勿用。新增颜色必须三主题（含首屏回退）补齐。
7. **带文字容器固定高度**：含文字的选项/按钮/标签/容器固定高度，不被文字撑高；过长用省略号或内部滚动。
8. **窗口设最大尺寸 + 内部滚动**：弹窗/面板/列表设 `max-height`/`max-width`，超出由内部容器滚动；滚动条隐藏或做样式适配。
9. **实体面板禁止半透明**：实体 UI 容器背景必须不透明，且不加 `backdrop-filter` 磨砂。唯一例外：遮罩 scrim（`--overlay-scrim`）与刻意玻璃质感装饰。
10. **主题变体与基础定义同文件（禁止跨文件补丁覆盖）**：组件的 dark/light/classic 变体写在该组件自己的样式文件里；`!important` 只准用于遮盖第三方库样式，禁止用于压过项目自有定义。审计脚本 `python3 cache/style_audit.py` + `python3 cache/theme_diff.py`（本地脚本，未随仓库发布）。
11. **大面积色块禁止纯黑/近黑**：窗口 hover、头部、背景等大面积色块禁止 `#000` 及肉眼近黑（`#0a0a0a`/`#0f0f0f` 一档），深色模式同理；深色层次靠中性灰阶（hover 用 `--hover-bg`，面板头部优先用分隔线而非深色衬底）。纯黑只允许小范围强调（如行内代码衬底）。

> 逐条细则与配色示例见 `docs/agents_details/frontend_design_style.md`。

## 5.6) 工具结果显示规范（强制）

适用所有用 `tool-result-meta`／`tool-result-content` 渲染的工具结果。

1. **meta 只放参数**：状态、ID、路径、任务描述、超时等元信息。**禁止**在 meta 放执行结果、统计摘要、输出内容。
2. **content 只放结果**：文件内容、命令输出、搜索命中、执行统计、最终回复摘要等。**禁止**在 content 重复展示工具参数。

## 5.7) 调试输出规范（强制）

### 前端调试日志

- **统一筛选词（强制）**：所有临时 `console.log` 必须用**同一个**前缀（如 `[route-debug]`），方便用户在控制台一次筛选。同一次调试任务**严禁**多个前缀；多模块用同一前缀、靠日志对象字段区分，不用发明多个前缀增加筛选成本。前缀优先与「用户可见现象」对齐，而非内部模块名。
- **控制数量与时机**：禁止上百上千条刷屏；只在关键路径（入口/分支/状态变化/导航）打，避免循环、高频事件、每帧渲染输出；大量结构化数据优先 `console.group`/`console.table` 或对象快照。
- **用完即清**：定位后及时删除或注释临时日志，不长期留在代码里。

### 后端调试日志

- **必须写入文件**：禁止直接 `print` 到终端刷屏。
- **复用现有 logger**（如 `utils/logger.py`、`modules/multi_agent/debug_logger.py`），按模块落 `~/.astrion/astrion/<mode>/logs/`。
- **关键状态转换必打**：多智能体、子智能体、任务轮询等复杂链路在关键状态转换点写结构化日志。

## 5.8) 前端多语言（i18n）文案规范（强制，2026-08 新增）

> 详细规范：`doc/frontend/i18n_spec.md`（已归档为 `_experiments/docs_archive/doc/frontend/i18n_spec.md`），约束级别与 §5.5 同级。

- **引擎**：vue-i18n v10（Composition 模式）；文案唯一定义在 `static/src/locales/{zh-CN,en-US}/<namespace>.ts`，**中文为源语言**。
- **使用**：模板用 `$t('ns.key')`（`static/src/locales/index.ts` 已开 `globalInjection`，模板侧**全局注入且响应式**）；SFC script／纯 TS 用 `import { t } from '@/locales'`（调用时求值、非响应式；需响应式用 `useI18n()` 或读 `currentLocale`）。
- **公共词**唯一来源是 `common` 命名空间，禁止各域重复定义。
- **key 奇偶强校验**：en-US 聚合器（`static/src/locales/en-US.ts`）用 `DeepString<typeof zhCN>`（`static/src/locales/zh-CN.ts`）约束，缺/多 key 直接 tsc 报错；新增命名空间须在两个聚合器同步注册。
- **防回退栏杆**：`npm run lint` 先跑 `scripts/i18n_audit.mjs`（剥注释查裸中文），只跑审计用 `npm run lint:text`；存量文件列在 `scripts/i18n_baseline.txt`，**迁移完一个删一行**，删后该文件永久受保护。
- **语言切换**：个人空间 → 外观 → 界面语言；默认 zh-CN（不跟随浏览器），持久化 key `agents_ui_locale`。
- **边界**：后端下发文字（API error、通知、工具结果摘要）不做多语言，前端原样显示；CLI 独立成体系（读 OS 语言，不跟随 web 个人空间）。

## 6) Git 工作流（开发 + Review）

### 6.1 核心原则

- 默认直接在 `main` 上开发，不强制新建功能分支；每个功能**建议**在 `main` 上保持一个清晰 commit，历史干净可回滚（原文为「建议」，非硬约束）。
- 使用 **Conventional Commits**：`feat:` `fix:` `refactor:` `chore:` `docs:` `test:` `perf:`。
- 个人项目，不设 PR 与 Review 流程；用户临时要求隔离开发时再按需建分支。

### 6.2 工作流

**AI 执行以下流程时，每一步都要向用户说明在做什么。**

```
0. 检查工作区状态（git status）
   有未提交改动：无关 → 提醒用户先 commit 或 discard；有关 → git stash push -m "WIP: <描述>"
1. 确认在 main 并同步最新代码：git checkout main && git pull origin main
2. 开发并提交：git add <文件> && git commit -m "feat(scope): 描述"（多轮迭代可 --amend 保持一个 commit）
3. 推送远程：git push origin main
```

**重要约束**：

- AI 必须在本地验证修改能正常运行，再建议用户提交。
- **禁止 AI 在未经用户确认的情况下执行 `git commit` 或 `git push`。**
- **commit / push 授权均为一次性**：用户说「可以 commit/push」只代表允许这一次；后续需再次主动同意。
- **允许 commit 不等于允许 push**，两者授权相互独立。
- 汇报按改动规模：大修改说明核心变更点与验证结果；小修改只说明核心变更点。
- 不要求每次输出 diff；用户要求 Review 时再用 `git diff` / `git diff HEAD~1..HEAD`。
- 提交必须带共同作者尾注：格式 `Co-authored-by: {智能体名} powered by {具体模型名} <{邮箱}>`，**模型名取提交时实际使用的模型**；完整规范（邮箱、多作者、自检命令）见 `docs/coauthor_trailer.md`。commit 时就写对，不要等 push 再补。

### 6.3 常用命令速查

| 操作 | 命令 |
|------|------|
| 暂存未完成工作 | `git stash push -m "WIP: xxx"` |
| 修改最近 commit | `git commit --amend` |
| 同步 main | `git checkout main && git pull origin main` |
| 同对话内 Review diff | `git diff` / `git diff HEAD~1..HEAD` |
| 查看提交历史 | `git log --oneline -10` |

## 7) 安全与仓库卫生

- **严禁提交真实密钥**（`.env`、token、cookie、用户隐私）。
- 运行态数据默认在 `~/.astrion/astrion/<mode>/`，不在源码树内；`.gitignore` 仍忽略源码树内的 `logs/`、`data/`、`users/`、`api/`、`project/` 等以防污染。分享前需脱敏。
- **`_experiments/`**：归档需要保留但不属于当前主线、又不想直接删的零散文件（调试记录、旧变更日志、模型测试脚本、翻译资料等）。该目录**不纳入 git**；不要散落在根目录。
- **不要提交本地构建产物**（`static/dist/`、`node_modules/` 等）。
- **文档目录约定**：`docs/` **纳入版本控制**，只放 AGENTS.md 相关文档与开发文档（当前含 `agents_details/`）；`doc/` 仍不追踪。旧 `docs/`、`doc/` 的内容已归档到未追踪目录 `_experiments/docs_archive/`（详见 `docs/README.md`）。

## 8) Android App 发布联动要求（重要）

修改前端并需要发布 Android WebView App 时**必须同步**：

1. 同时更新版本信息：`android-webview-app/app/build.gradle.kts` 递增 `versionCode`、更新 `versionName`
2. 同时更新更新说明：`android-webview-app/APP_CHANGELOG.md` 顶部新增当前版本说明
3. 完成后提醒用户运行 `bash ./upload_android_apk.sh`（本地私有脚本，未随仓库发布；它还会同步 `build.gradle.kts` 与 `APP_CHANGELOG.md` 到服务器）

> 禁止只改前端代码而不更新版本号/更新说明。服务端 `/api/app/version` 的版本号是从**服务器文件系统上的 `build.gradle.kts`** 解析（`server/status/docker.py`），不是从 APK 包解析——只传 APK 不同步元数据会导致 App 检测不到新版本。

## 9) 给 Agent 的硬性要求

- **先读当前代码再执行**，不依赖历史文档记忆。
- 输出结果按改动规模：大修改必须含核心变更点 + 验证结果（已执行命令／未执行原因）；小修改只需核心变更点。
- 不要求每次列出所有修改文件，也不要求每次展示 diff；仅在用户要求 Review、改动复杂或有助于说明时使用。
- **如果发现本文档过时，直接更新 `AGENTS.md`（及对应细节文档）并在结果中说明依据。**

## 10) 宿主机权限模式与沙箱执行机制（2026-05 更新）

**改权限 / 沙箱 / 平台隔离前必读**：`docs/agents_details/permission_and_sandbox.md`（含平台实现细节与实测记录，与本文同级）。

系统有两套**独立但叠加**的控制：**权限模式**（`readonly`/`approval`/`auto_approval`/`unrestricted`）× **执行环境**（`sandbox`/`direct`，仅宿主机模式可切）。

### 10.1 权限模式

- **`readonly`**：`run_command` 只能在只读沙箱执行，写入由系统拒绝；读取同样受限（可读边界见 §10.8），**读不到属预期边界不是故障**；`write_file`/`edit_file` 直接拒绝；持久终端可创建与输入但以只读身份运行（写入 EPERM 由系统强制）；权限跨界切换时现有终端直接销毁重开。
- **`approval`**：`run_command` 先走只读沙箱 → 出现权限拒绝（`Operation not permitted`/`Permission denied`）触发前端审批 → 通过后仅该次命令以可写沙箱重试，工具结果返回重试后的最终结果。**审批只授予该次命令的工作区内写权限，不放大读取**；重试后仍被文件权限拒绝时工具结果附 `read_scope_hint`。**执行环境锁定为沙箱**（direct 下无沙箱兜底会让审批承诺被架空，故与 direct 硬互斥）。
- **`auto_approval`**：`write_file`/`edit_file` 工作区内直接执行、工作区外进审批；`run_command` 先只读沙箱→权限拒绝后由后台审批智能体自动审核；拒绝时返回「被拒绝+理由」并继续主循环（不强制结束任务）；执行环境同样锁定沙箱。
- **`unrestricted`**：权限层不拦截（工作区内读写自由、命令免批准）；是否沙箱由执行环境决定。**「无限制」只管工作区内**——宿主机沙箱下读边界仍是白名单，范围外读取需路径授权；只有 direct 才真正无边界。

### 10.2 执行环境

- `sandbox`（默认）：OS 沙箱执行（macOS `sandbox-exec`／Linux `bwrap+seccomp`／Windows `WSL2`）。
- `direct`：宿主机直接执行（高风险），**仅 `unrestricted` 可选**；受限权限档与 plan 模式下后端 `set_execution_mode` 硬锁拒绝 direct，进入受限档时若已是 direct 会被联动压回沙箱（记录 `pre_readonly_execution_mode`，切回 unrestricted 恢复）；存量「受限档+direct」对话加载时自愈矫正。
- 切换后一直生效，**无自动回退机制**（2026-07 已移除原 TTL 自动回退）。

### 10.3 路径授权语义

- 两类：**可读可写**、**仅可读**。可读集合 = 可读可写 + 仅可读；可写集合 = 可读可写。
- 配置来源收敛为两个：`config/host_sandbox_policy.json`（UI 唯一读写目标）+ 真·环境变量 `HOST_SANDBOX_MACOS_WRITABLE_PATHS`（部署通道，合并去重）。settings.json 的 `terminal.macos_writable_paths` 映射已移除（历史值一次性失效）。
- **工作区级授权（2026-09-27）**：弹窗内分「作用域」（当前工作区/全局，默认当前工作区）与「类型」；工作区级条目存 `workspaces` 字典，合并语义**只增不减**（只能在全局基础上追加，deny 清单仍仅全局级）。工作区移动/重命名后其条目自然失效（fail-safe）。该模块锁为 `threading.RLock`（save 持锁期间会回调 load/save policy，普通 Lock 会自死锁）。

### 10.4 维护约束

不要在提示词里暴露内部实现细节（如「命令文本猜测」等）；文案应面向用户能力边界与操作建议，不描述内部判定算法。

### 10.5 审核智能体配置与调试

- 统一在设置页「审核智能体」分区配置，存 `personalization.json` 的 `review_agents` 键。三个审核智能体：`auto_approval`／`goal_review`／`workflow_review`；字段 `model`（留空=自动规则）/`thinking`/`timeout_seconds`/`max_rounds`/`max_command_timeout`。
- 解析入口 `modules/review_agent_config.py::resolve_review_agent_config`（复用 `aux_model_resolver`）。旧的独立 json（`config/auto_approval.json`/`goal_review.json`/`workflow_review.json.example`）与 `sub_agent_models.json` 库均已彻底废除，**无向后兼容**。
- 调试开关：`modules/approval_agent.py` 的 `DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT`，开启后写 `logs/approval_agent/`。

### 10.6) 运行模式（work_mode）系统（2026-08-13 新增）

运行模式（`plan`/`ask`/`execute`）控制**与用户的交互节奏**，与权限模式（限制能力）正交。命名注意：`run_mode` 已被 fast/thinking 思考模式占用，**严禁混用**。

- **plan**：只制定计划并讨论；权限**锁定只读**且执行环境**锁定沙箱**（UI 禁用 + 后端双重强制）；唯一写例外是 `.astrion/plan/*.md`；完成后调 `submit_plan` 提请批准，**批准后自动切 execute**（恢复进入 plan 前的权限与执行环境）。
- **ask**：先讨论后开工；**禁用 `ask_user` 工具**（开放式讨论必须直接写在回复里）；与 execute 无执行层差异。
- **execute**：自行梳理计划、脑补细节直接开工；仅硬阻塞才提问。

**实现要点（改代码必须知道）**：

- 后端核心 `core/main_terminal_parts/tools_policy.py`（WORK_MODES、`get/set/switch_work_mode`、plan 锁、`RESTRICTED_PERMISSION_MODES` 与受限档⇄沙箱联动）；执行环境锁在 `core/main_terminal.py::set_execution_mode`（只拦 direct）。
- **Web 新对话初始化（2026-10-16）**：所有新建按钮只进入 `/new`，删除立即创建空文件的个人选项；首条消息才创建。工作区 terminal 独立缓存个人默认模型、思考模式、推理强度、工作模式、权限模式；进入 `/new` 经 `POST /api/new-conversation/settings` 装配。执行环境/网络只继承**刚离开的同工作区正在查看的对话**；跨工作区、直接打开或来源缺失固定 `sandbox + restricted`，禁止扫描最近对话。推理强度 `null` 是有效默认。空页切换只改前端，首消息提交完整 `settings`，统一由 `server/context/new_conversation.py` 校验创建；创建时不再覆盖个人默认。状态轮询不得覆盖空页选择。CLI/Gateway 与工作流创建尚保留各自入口，不据此宣称已统一。**任何创建路径不得在 plan 锁之前调用非只读 set**（曾因此 500）。
- 提示词：`prompts/work_mode.txt` + `mode.py::_build_work_mode_rules`（三档规则唯一来源）；冻结注入在 `messages.py`；切换走 drift 机制（`_RUNTIME_MODE_KINDS` 第四种）。
- API：`GET/POST /api/work-mode`；**仅空闲可切换，运行中 409**。
- 前端：输入栏 `work-mode-switcher`（streaming 时锁定）；plan 下权限/执行环境两组选项禁用+锁定标记（**网络权限组保持可调**，不整体锁死菜单）。

> 完整实现清单见 `docs/agents_details/work_mode.md`。

### 10.7) 后台命令只读沙箱修复（2026-08-13）

后台 `run_command`（`run_in_background=true`）此前在所有环境绕过只读权限，已修复：`create_background_command` **必须透传 `sandbox_write_access`**，宿主机路径按它选只读/可写计划，docker 路径按它决定是否以非特权 uid 执行。**这是链路不变量，后续改动不得回退。**

### 10.8) 平台级只读强制（2026-08-30）

只读的强制由各平台原生机制兜底；`config/limits.py` 的命令文本白名单只是**审批决策的启发式**，**不再是安全边界**（已知可绕过，如 `find . -delete`；绕过后果只是多走一次审批）。

- **docker/web 模式 = 非特权 uid 执行角色 + Landlock 进程级只读域**（`modules/docker_readonly_exec.py`）：容器主进程保持 root；只读通道以 `-u 10001:10001` 运行。第一层强制力是内核 DAC（工作区属主为宿主 root，非属主无写权）；第二层 Landlock（`modules/landlock_launcher.py`）封死「工作区内历史遗留 777/o+w 路径仍可写」的残留洞。launcher 自检失败自动降级纯 DAC 并 warning 标注；`DOCKER_READONLY_LANDLOCK=0` 可整体停用。**注意不能写「/ 授全量 + ro 授空」的交集规则**（空授权规则被内核拒绝）。macOS Docker Desktop 双重不适用，仅 Linux 宿主生效。
- **macOS 宿主机 = Seatbelt 白名单读模型**（`modules/host_sandbox_runner.py`）：只读/可写两个 profile **共用同一白名单读模型**，唯一区别是写权限。要点：缺祖先目录 literal allow 会让进程 exec 直接 Abort trap；**deny 规则必须位于所有 allow 之后**（Seatbelt 后规则覆盖先规则，曾致工作区 `.env` 实际可读）；`/usr/bin` 下 python3/git/clang 是 xcselect 垫片，需放行 `MACOS_BASE_MACH_RULES` 与 `/Library/Preferences`；可写 profile 已白名单化（代价：祖先目录顶层文件名可列出，内容仍被拒）；原生文件工具读 roots 与沙箱白名单同源（`file_manager/path_mixin.py`）。
- **Linux 宿主机 = 管理员安装的受限助手**：bwrap 最小文件系统 + seccomp + AppArmor + systemd/cgroup IP 过滤，不挂载宿主 `/`；最后降到真实普通 UID 并清能力。见 §20。
- **Windows = WSL2 最小根文件系统（白名单）**，命名空间内只有系统目录 + 工作区，天然符合。

> 平台细节、常量清单与实测记录见 `docs/agents_details/permission_and_sandbox.md`。

## 11) 统一子智能体（2026-10-22）

所有主对话只用普通 ConversationManager；取消对话类型、角色模板和第二存储目录，列表／搜索／创建／bootstrap／快速对话没有类型分流。旧多智能体主对话仅由离线脚本迁入主目录，**禁止在业务代码加入双目录回退或旧传统子任务恢复**。迁移说明 `docs/sub_agent_migration.md`。

- **实例容量与入口（2026-10-23）**：设置→子智能体可配置“允许同时存在的子智能体数量”，用户级`sub_agent_max_instances`默认20、仅接受正整数（JSON安全整数）；创建时读取最新值，无需为设置变更重启。默认值与校验唯一来源`modules/sub_agent_settings.py`，移除旧`SUB_AGENT_MAX_ACTIVE`环境变量入口；按`status != terminated`计数，idle、running及异常failed均占名额，只有terminate释放。不另限制running数量，已有实例续接不占新名额；创建检查与登记共用注册表锁。输入栏显示同口径的“X 个子智能体”，点击展开桌面／移动端快捷窗口并滚动到子智能体卡片，不再触发批量停止；形象仅把running子实例计入后台工作。
- **名称和工具**：`modules/sub_agent/definitions.py` 为唯一签名来源，`named.py` 分派。公开 name/names 寻址，同对话casefold唯一、1–80字符、终结后不复用；内部编号不对模型暴露。主侧6工具=create/send/status/stop/terminate/answer；子侧ask_master/ask_other_agent/answer_other_agent/status。统一get_sub_agent_status：names省略查询全部，传入非空列表按名称查询，统一results数组；删除list_active_sub_agents。模型工具结果不返回task_id/conversation_id，前端运行视图保留。模型用intelligence选；删除角色工具、finish_task、强制交付文件、子等待sleep和公开run_in_background。
- **默认保留与复用（2026-10-23）**：完成后默认保留idle，后续优先复用已有实例及其上下文；本轮完成、暂时无任务或准备最终回复都不是终结理由，不例行清理或预防性释放名额。仅在用户明确要求、经核实实例发生无法恢复且无法继续使用的错误，或严重违反明确指令且纠正后仍继续偏离时，才可终结；实际达到实例上限、已有实例无法复用且确需新建时，也可终结不再需要的空闲实例。发现正在或即将执行未经授权的破坏性操作时，立即stop_sub_agent，不必等待纠正；无法可靠继续协作时再terminate_sub_agent。普通报错、可恢复故障、理解偏差或意见分歧不构成终结理由。停止和终结不能撤销已发生的操作。提示词、工具描述与源技能`agentskills/sub-agent-guide/SKILL.md`保持一致，运行时skill副本由系统同步、不手动维护。
- **轮次边界**：全部后台，自然最终回复进入idle，保留上下文。timeout_seconds可省略，新轮省略即不限时；**预算到期必须完成当前模型响应和整批工具并真实回填后，才阻止下一次模型请求**。显式stop即时取消执行worker：半截响应不提交，已提交调用的缺失结果补写“被主智能体取消”（界面停止为“被用户取消”），已完成结果保留；停止后idle、end_reason=stopped且可继续。terminate为吸收态并优先于stop。运行中消息安全入队，不能插到assistant/tool中间；idle靠Event等待，跨线程唤醒。idle不算后台运行。
- **持久化与恢复**：`persistence.py`按工作区data_dir共享tasks/instances/running/states注册表，查询不得覆盖活动记录。新状态仅`sub_agent_runtime.json`（version=1），上下文在`sub_agent_tasks/<task_id>/conversation.json`。不读旧sub_agents.json。保存pending_work、pending_master_messages及work_paused；stop暂停排队工作的自动消费，后续新消息再解除暂停，恢复时同样遵守。恢复校验原模型、固定权限、项目身份和工作目录，不隐式重跑旧任务。默认工作目录`.astrion/sub_agent_work/agent_N[_suffix]/`。
- **通知与单写者**：普通中间输出留子记录，每轮只将最终回复作为user消息通知主；主子／子子问答保留，子子通信同时报告主。生产端只入队；主运行中在完整工具批次后按顺序消费，主空闲时poll预占门闸并经RuntimeService移交token派发notice。**有消息立即消费，不等running全部退出**，避免ask_master互锁。受理失败回队；前置消息认领门闸后才持久化；逐条写失败只回队未写尾段。目标暂停策略不被自动通知解除。
- **显示契约**：`auto_message_type=multi_agent_*`、visibility=chat、starts_work=false仍是协作消息协议。持久化和事件共用message_id，恢复只走显示快照。传输字段has_running_multi_agent表示running子实例／待消费消息，不是对话类型；后台命令／工作流另用通知池。禁止恢复子任务waiting阻塞输入。
- **MCP与权限**：每子实例独立MCPClientManager，复用父注册表及可见映射，不共享活动客户端。默认Playwright profile加入稳定actor_id；未配置cwd时使用子working_dir，显式配置优先。MCP不套内置目录写权限沙箱。四档内置权限见§18。
- **显示和分类**：主formatter在agent_context.py并注册dispatch；子formatter在toolkit.py；前端toolRenderers.ts委派subAgentResults.ts，备用ToolAction同用。meta只参数、content只结果。新增工具同步tool_config类别、工具系列／只读清单和动态加载注册。
- **调试与验证**：ma_debug沿用multi_agent_loop.log，记录轮次／恢复／pending／门闸／跨循环问答。统一回归`test/2026-10-22_子智能体统一/`；相关快照、工作区、固定权限、MCP回归按影响执行。真实模型、UI和各原生平台另需实机验收。

> 完整契约及执行器职责拆分见 `docs/agents_details/multi_agent.md`。

## 12) 对话级主任务门闸与单写者不变量（2026-08-12）

> 事故背景：并发两个主聊天任务交叉写入 `conversation_history`，产生 `assistant→assistant→tool→tool` 乱序段，最终 API 400 `tool_call_id is not found`、通知永久丢失。

### 12.1 单写者不变量（核心约束）

**一个 WebTerminal（≈ 一个打开的对话）同一时刻只允许存在一个主聊天任务。** 主任务包括用户消息任务、后台完成通知派发任务、多智能体 idle 派发任务等一切会向 `conversation_history` 追加消息并请求模型的执行体。**宁可拒绝/推迟新任务，也绝不并发写入。**

### 12.2 门闸本体

实现 `server/main_task_gate.py`：**进程内全局锁 `_LOCK` + terminal 实例属性 `_main_task_gate_token`**（不是以 terminal_id 为 key 的中央字典）。四个真实 API：

- `try_acquire_main_task_gate(terminal)`：非阻塞获取，空闲则发新 token 并返回，被占用返回 `None`。
- `acquire_adopted_main_task_gate(terminal, token)`：认领派发方预占的 token；**判定依据是 token 相等**，被其他 token 占用返回 `None`，空闲则退化为竞争获取。
- `release_main_task_gate(terminal, token)`：仅 token 匹配时清空；**不匹配/重复/空 token 均为无操作**（不抛异常）。
- `is_main_task_gate_busy(terminal)`：只读探测。

获取/认领与释放必须**成对且带 token**。⚠️ 历史文档写的 `acquire_or_claim_main_task_gate(terminal_id, owner_desc)` **在代码中不存在**，勿引用。

### 12.3 门闸入口与 token 移交

- **门闸接入点共 6 处（另 1 处外层兜底释放）**：聊天 `process_message_task`（`server/chat_flow.py:139`，`:259` finally 释放）；通知派发预占（`server/chat_flow_task_main.py:1023`）；手动压缩（`server/tasks/compression.py:14`）；深度压缩 REST（`server/conversation.py:1756`）；工作流激活与通知派发（`server/workflow_runtime_api.py:125` / `:240`）；兜底释放 `server/tasks/models.py:1155`。**新增任何主任务入口必须走门闸**，chat/notice/compression 的受理检查统一按对话互斥。
- **压缩运行语义（2026-10-23）**：空闲手动压缩是 `task_type="compression"` 的主任务；运行中点击经 `server/tasks/compression_requests.py` 在当前主任务内排队，完整响应及整批工具结果回填后以 `mode="requested"` 压缩并续接，保留工作段锚点；重复请求合并，停止取消待执行请求，禁止另起并发写入者或从事件0重放。自动压缩同属原主任务。压缩均算智能体运行（输入保持可用，运行中发送直接作为引导，停止精确取消当前任务）。`server/compression_commit.py` 一次原子提交历史标记/压缩记录/引导语/上下文统计；生成失败或提交前取消保留旧历史。`server/tasks/queue_state.py` 保留未消费引导和旧提前输入来源，停止/失败后连同附件恢复到所属对话草稿，保存成功后确认移除，不自动续发；压缩续接在原任务锁内重新开放引导受理。
- **通知链移交**：轮询器派发前先预占门闸，token 经 **`RuntimeContext.directives.main_task_gate_token`**（`server/runtime/context.py`）→ `TaskRecord.directives` → `run_chat_task_sync(..., main_task_gate_token=...)` 移交下去（持 token 认领，不重复获取）；派发失败要释放门闸并回滚已打的通知标记（`_rollback_completion_notice_marks`）。⚠️ 历史文档写的 `session_data["main_task_gate_token"]` **代码中不存在**。
- **兜底释放**：`server/tasks/models.py::_run_chat_task` 外层 `finally` 兜底，防异常路径门闸泄漏。

### 12.4 改代码注意事项（硬性）

1. **新增任何主任务入口必须走门闸**；chat/notice/compression 的受理检查统一按对话互斥。
2. **不要在 return 分支手写 `_tool_loop_active` 恢复**：`execute_tool_calls` 已改为守护包装（try/finally 复位，内层 `_execute_tool_calls_impl`），新增提前返回路径无需也不应手动操作该标志。
3. **不要依赖 build_messages 防御层掩盖并发问题**：孤儿 tool 消息剥离只是止血层，出现剥离 warning 应按事故排查。

### 12.5 Socket.IO 已整体移除（2026-09-11）

轮询是**唯一**实时通道，Socket.IO 及其依赖已全量移除。**事件出口唯一权威 = 任务事件流**（`TaskRecord.events`，`_append_event`）。

原 socket 功能替代：`status_update` → 空闲 5s 轮询 `/api/status`（运行期由事件流驱动）；`quota_*` → `quota_exceeded` 本就在事件流；会话列表/切换类 → 发起方操作后主动刷新（多标签页被动同步退化为轮询间接感知，属刻意取舍）；`system_ready` → `loadInitialData` 一次性初始化；终端实时输出 → REST 轮询；连接状态 → `/api/health` 心跳。

**硬性约束**：

1. 新增任何「实时通知」一律写 `_append_event` 或扩 `/api/status` 载荷，**禁止**重新引入 socket 推送。
2. `server/extensions.py` 只剩 `run_background`，不要再往里加推送类函数。
3. `WebTerminal.broadcast()`/`terminal_manager.broadcast` 调用点保留但回调恒为 None（判空安全），新代码不要依赖它们产生用户可见效果；`make_terminal_callback(username)` 同理是兼容空操作，保留仅为不炸历史 import 路径。

## 13) 工具动态加载（tool_loading，2026-09 新增）

> 注册表与状态辅助唯一权威：`core/tool_loading.py`。设计文档 `docs/dynamic_tool_loading_plan.md`。

**机制一句话**：低频工具在顶层 `tools` 数组只以 stub（名称+引导式短描述+空参数）常驻；模型需先调 `load_tools(tool_names=[...])` 拿回完整定义再调用；执行守门按对话 metadata 放行已加载工具。

**硬约束（改代码必须知道）**：

1. **对话文件是唯一权威**：`metadata.tool_loading` 在**创建对话时快照一次**（所有新主对话统一快照，无类型例外）。之后个人空间改动只影响新建对话；执行链路**禁止**为该功能读 personalization／Flask session。
2. **老对话无字段 = 未启用**：读侧一律走 `get_tool_loading_state()` 防御性解析，**不做迁移**；字段损坏同样按未启用处理。
3. **tools 数组对话级稳定（stub 常驻）**：deferred 工具不整个剔除，而是替换为 stub——模型只敢调用「出现在工具列表里」的工具，纯 tool result 塞定义曾导致所有测试模型反复 load 死循环。stub 只由完整定义决定、与加载状态无关，数组每轮逐字节一致（前缀缓存不受影响）。**禁止**把 loaded 工具追加回 tools 数组。
4. **prompt 目录走现有冻结机制**（key=`frozen_tool_loading_prompt`），目录**不**随 loaded 状态变化（system prompt 是缓存首段，动了全破）。
5. **压缩必须重置**：深压缩、浅压缩、手动压缩三处钩子统一 `reset_state_after_compression`（loaded 清空、pending 回满）；不回滚守门会放行无定义调用。
6. **守门**：`tools_execution.py` 分发链前置，`deferred_set − loaded` 的工具被调用时返回引导错误（不执行）；`load_tools` 本身在 `_READONLY_ALLOWED_TOOLS` 中。
7. **`load_tools` 的 formatter 必须保留完整 JSON 定义**（`agent_context.py::_format_load_tools`），只做摘要会导致历史重建后模型失去参数结构。
8. **`close_sub_agent` 已彻底删除**，勿恢复；终止子智能体统一用 `terminate_sub_agent`。
9. **重复加载直接拒绝**：请求的工具全部已 loaded 时返回 `success=False`（`tools_exec.load_tools_all_already`），不再重复返回定义；配套 prompt 与 formatter 强调「不进 tools 数组也可直接调用」。

**新增可延迟工具 checklist**：① `DEFERRABLE_REGISTRY` 注册；② 确认后端 formatter 与前端 renderer 已覆盖；③ 个人空间勾选 UI 自动出现（类目标签 i18n key `personalization.toolLoadingCat.<key>` 需双语补齐）；④ 跑 `test/历史测试_日期不明/test_tool_loading.py`。

> 完整清单见 `docs/agents_details/tool_loading.md`。

## 14) 网页提取与搜索

> 实现唯一权威：`modules/webpage_extractor.py`；搜索适配器在 `modules/search_providers/`。

**提取链**：白名单直提（本机、免费、**唯一自动回退层**）→ 用户在设置页选定的提取商（Jina 默认／Tavily／Exa／Parallel）。**选定商失败即报错，不再向其他家回退**（避免用户不知情时消耗多家付费额度）。

1. **统一入口 `extract_single_url()`**：两个工具调用点都走它，不要绕过单写某一层路径。
2. **直提层内部降级链**：GitHub blob → jsDelivr CDN → GitHub API contents（备用，匿名限 60 次/时）→ trafilatura → 转交选定提取商。**不要用 raw.githubusercontent.com**（国内 DNS 污染，实测不可达）。
3. **trafilatura 是可选依赖**：import 失败时通用直提静默失效（GitHub 直链仍可用），白名单不报错。
4. **blob URL 的 branch 按单段解析**（`[^/?#]+`）；含斜杠分支名解析失败会自然降级，属预期行为。
5. **域名匹配 = 精确或子域名后缀**；用户输入经 `_normalize_domain` 清洗。
6. **提取商凭证复用搜索同源解析**（`resolve_search_credential()`），零新增配置项；选定 tavily/exa/parallel 而无 key 时返回明确错误，**不静默换家**。
7. **新增 personalization 键必须双注册**（`DEFAULT_PERSONALIZATION_CONFIG` + `sanitize_personalization_payload`），否则保存后被静默丢弃。
8. 返回格式沿用 `🌐 网页内容 (N 字符)` 骨架 + `[提取方式: ...]` 标注，模型侧无感知。
9. **read-before-edit 已读白名单**含 `recall_project_memory`（返回全文视为已读）；`search_project_memory` 只回片段，刻意不标记。
10. **配置分离（2026-09-27）**：`personalization.json` 只存 UI/行为偏好；搜索/提取服务配置存 `service_config.json`；所有 API 密钥存 `secrets.json`（0600、原子写）。三文件由 `personalization_manager.py` 统一读写。

**搜索多服务商（2026-09-26）**：`search_engine.py` 只做参数校验/归一化、凭证解析、结果包装；各服务商的请求构造与解析在 `modules/search_providers/` 适配器包。适配器出参 `{"ok","results","answer","dropped"}`；**`dropped` 必须透传**到 `filters.provider_dropped` 展示，禁止静默吞掉。前端 `filters`/`results[]` 结构对所有服务商保持一致。

> 提取商契约（Exa/Parallel 端点与响应形状）、新增服务商 checklist 见 `docs/agents_details/web_extract_and_search.md`。

## 15) 设置页与提供商体系（2026-09-24 新增）

> 后端 API 契约 `docs/providers_api.md`。

**设置页拆分**：原个人空间抽屉（13 Tab）拆成「个性化 + 账户」留抽屉，其余迁 `/settings` 全屏页（5 组 14 分区）；路由沿用 workflows 已验证的手写 History API 分支（**不引 vue-router**）。

1. **路由必须前后端双登记**（曾因只登前端导致整页跳转 404）：后端 `server/auth.py` 注册 `/settings` 与 `/settings/<path:section>`；前端 `route.ts` 登记 `settings`、`settings/*`。**进出都是整页跳转，不做 SPA 内 pushState**（pinia store 每次重建，`activeSection` 永远从 `general` 开始）。
2. **挂载在 App.vue 的 Agent 加载门槛之前**，以 `settingsRoute` 独立互斥显示；组件在 `app/components.ts` 异步注册。设置页自己加载账号／偏好／分区数据，生命周期不启动 Agent 连接、工作区运行态或轮询；禁止用 `isConnected` 阻挡挂载。
3. URL 深链由 SettingsShell onMounted 解析（先 `await fetchSessionStatus()` 确保管理员判定就绪）；非法分区或非管理员访问管理员分区时回默认。
4. **SettingsShell 与 PersonalizationDrawer 禁止同时可见**（两者都 provide 同一份 `personalizationDrawer` context）。
5. **新增设置分区三处同步**：SettingsShell 的 NAV_GROUPS + SECTION_META、`stores/settings.ts` 的 SettingsSection 类型；文案走 `settings` 命名空间。
6. **管理员分区**：`SETTINGS_ADMIN_SECTIONS` = codex/admin/providers；导航过滤 + 运行时降级回退 general 由 SettingsShell 处理。

**提供商体系**：

1. **目录与存储**：`config/providers_catalog.json`（静态目录；**双站拆分原则=一个 base_url 一条目**；编程订阅套餐条目 badge=「编程订阅」，套餐 Key 与按量 Key 不通用；`static_models` 仅当 GET /models 失败/为空时回退——**在线发现永远优先**）。凭证存 `<DATA_DIR>/providers.json`（0600 原子写，**不纳入 git**）。`modules/provider_manager.py` 是唯一权威。
2. **协议 = Chat Completions + Responses 双协议**：`/models` 不标注协议，裁决走混合策略（catalog `protocol_map` 最高优先 → models.dev `npm` 字段（须有 `protocol_source` 门控）→ 默认 chat）。Responses 统一走 `utils/api_client/responses/`。**Anthropic Messages / Google 原生协议不做适配**：模型正常注册显示，`chat()` 明确报错 + 配置页 tooltip 提示。`_fetch_models` 在 fetch 层剔除非聊天模型，**解析层 `parse_models_payload` 保持纯净**（兼容 `{data:[]}`/裸数组/`{models:[]}`）。
3. **注册表三路合并**：手写 custom + provider 同步 + Codex；provider 模型 key = `{provider_id}/{model_id}`，带 `api_protocol` 字段；get_profiles() 同时用 models.dev enrich 四字段，查不到回退默认。
4. **权限=管理员级**：`server/providers.py` 全部端点 `@api_login_required + @admin_api_required`；用户级 key 是后续预留方向，**改动深，勿擅自做**。
5. **推理强度滑块绑思考能力**：`supports_reasoning_effort` 在所有路径统一跟随 `supports_thinking`（provider 同步／手写 custom／codex 三方一致）。
6. **custom_models.json UI 化**：`/api/custom-models` CRUD 直接读写部署目录；`_sanitize_custom_model` 白名单校验，写入始终落 `DEPLOY_CONFIG_DIR`。
7. **模型可见性 = 用户级**：`personalization.hidden_models`，后端只注册字段 + sanitize，**过滤在前端** `stores/model.ts` 做；与提供商管理（管理员级）是两条独立权限线，**不要混**。
8. **Codex 即 openai-codex**：OpenAI 拆 openai-api（api key）与 openai-codex（ChatGPT 订阅 OAuth）。**连接管理单一入口**：连接/断开/刷新模型只在「提供商」分区操作（登录唯一实现 = `settings/tabs/providers/useCodexLogin.ts` + `CodexConnectDialog.vue`）；设置页 Codex 分区只保留状态展示与用量/重置额度/代理等高级管理，**禁止**再内联登录/断开/刷新按钮。
9. **图标**：`static/icons/providers/`（来自 lobehub/icons，按 catalog 的 icon 字段引用）；新增提供商先补图标再补目录。
10. **`ollama`/`lmstudio` 是 auth=none**：本地服务无需 key，connect 直接探 `/models`。
11. **models.dev 数据源**：`config/modelsdev_snapshot.json`（`scripts/update_modelsdev_snapshot.py` 生成）+ 在线缓存 6h 节流刷新；`protocol_source` 门控必须存在（无脑按 npm 裁决会误判单协议 provider）。
12. **Responses 互切锁 = 协议级二分**：chat↔responses 跨协议切换 409；responses 跨 provider 自由切（加密 reasoning 块按 `_same_responses_provider()` 判定，非同 provider 时剥离回插）；老对话 profile 查不到（`api_protocol=None`）不锁放行。
13. **codex 身份已彻底泛化（无向后兼容）**：`provider_type="codex"` 消亡，身份标识改 `responses_auth="codex_oauth"`；老对话 `codex/xxx` model_key 失效需重选。

> 完整分区清单与目录字段见 `docs/agents_details/settings_and_providers.md`。

## 16) 预览面板（Preview Panel，2026-09-27 新增）

> 类 Claude Code Browser 面板的 MVP：智能体创建的 HTML 文件、启动的 dev server、输出的 localhost 链接自动进入右侧 dock 的「预览」窗口。

### 16.1 检测与存储

- **检测唯一权威**：`modules/preview_targets.py`。**两类捕获源（2026-09-29 简化，原为三类）**：① `write_file`/`edit_file` 的 `.html`/`.htm`；② 模型流式输出的 localhost 链接（滚动缓冲 120 字符增量扫描，防 URL 跨 chunk 断裂）。**run_command/terminal 输出检测已删除**（实测是脏数据主来源，且放行条件几乎恒真）。
- **URL 识别规则**：路径为 ASCII 白名单字符集（`* ' " ( ) [ ] :` 与非 ASCII 一概不粘）；裸主机不记录；favicon 等静态资源噪音不记录；尾部 `/` 归一后去重。`_normalize` 对所有读取出口生效，老对话读出自愈、无需迁移。
- **`0.0.0.0` 归一化**：监听地址不是访问地址（Chrome 已禁访问），扫描时统一映射 `127.0.0.1`。
- **存储**：对话 `metadata.preview_targets`；服务器按**完整 URL** 去重（同 origin 不同 path 保留多条，属刻意设计）；同 URL 重复命中不写不广播。广播与 edited_files 同一链路。

### 16.2 预览渲染与安全隔离

**安全模型 = 换源隔离**：主应用同源端点 + `sandbox="allow-scripts allow-same-origin"` 对同源内容等于无沙箱，业界全部用换源。

- **localhost 目标 = 独立预览服务器**（`server/preview_server.py`）：只绑 127.0.0.1 的随机高位端口迷你 Flask；路径带每对话随机 token；两道关卡 = Host 头白名单（防 DNS rebinding）+ token 注册表反查（注册表存会话身份三元组，不存 terminal 引用——对话级 terminal 会被回收）。下发链路 `build_preview_runtime()` 在 targets/bootstrap/messages 三处响应里带 `preview_base` + `preview_token`。
- **docker/web 模式 = 预览整体禁用**（红队演练确认 docker 是唯一把端口暴露到公网的形态）。**唯一判定 = `is_preview_enabled()`（=IS_HOST_MODE）**，四处门控：记录入口／端点／序列化／prompt。前端无目标自然隐藏。
- **远端域名访问（host 模式经反向代理的边角场景）**：浏览器到不了 127.0.0.1 预览服务器，`build_preview_runtime` 对非回环 Host 返回 None，前端**回退旧同源端点**（`/api/preview/file|proxy`，仅 host 可用）。残余风险已知（有 CSRF + 无 CORS 兜底）。
- **SSRF 防护：仅允许代理当前对话已登记的端口。**
- 已知限制（MVP）：代理不转发 WebSocket（HMR 在代理预览失效）；页面内动态 import/fetch 的绝对路径不重写；docker 路径每请求一次 docker exec。

### 16.3 前端

- store `stores/preview.ts`；dock 第六窗 `PreviewWindow.vue`；展开面板 `PreviewPanel.vue`。
- 自动展开：personalization `preview_auto_open`（默认 false）；规则 = 最新目标优先、同目标更新不重弹、用户手动关闭后本次任务内不再自动展开。
- **桌面端 localhost 链接拦截**：桌面壳 `on_navigation` 会把非后端同源导航拦去系统浏览器，与「窗口内预览」语义冲突——App.vue 注册 document 级 capture 点击拦截（仅 `__ASTRION_DESKTOP__`），回环 http(s) 链接 `preventDefault` 后走 `preview store 的 openServerUrl()`；**同源（后端端口）链接必须放行**，否则会断应用内导航。

### 16.4 Prompt

`prompts/preview_panel.txt` 冻结段注入。核心要求：启动服务器后在回复中**写完整地址**、不要引导用户手动开浏览器、自我验证仍走 Playwright。

> 代理架构、docker 分流与隔离细节见 `docs/agents_details/preview_panel.md`。

## 17) 代码索引（codegraph，2026-10-06 新增）

> 定位：精确的**代码结构查询器**（tree-sitter 符号图 + SQLite/FTS5），**不是语义搜索引擎**——按词法匹配符号名与代码词。

- 全局 CLI `/opt/homebrew/bin/codegraph`（npm 包 `@colbymchenry/codegraph`，实测 v1.6.2）；建索引 `codegraph init --yes .`；产物 `.codegraph/` 已 gitignore。
- **遥测默认开启，已关闭但换机器/重装后需重新关闭**（`codegraph telemetry off`）。
- 实测规模：946 文件 / 18,711 节点 / 49,134 边 / 2.4 秒 / 库 59MB。

| 命令 | 用途 |
|------|------|
| `codegraph explore "<查询>"` | **首选**：一次返回相关符号源码 + 调用路径 + 影响面 |
| `codegraph impact <symbol> --depth 2` | 改动影响面（含 HTTP 路由反向关联） |
| `codegraph callers/callees <symbol>` | 谁调用它 / 它调用谁 |
| `codegraph query <name>` / `node <symbol>` | 按符号名搜索 / 单符号源码与调用链 |
| `codegraph affected [files...]` | 改动会影响哪些测试（支持 `git diff --name-only \| codegraph affected --stdin`） |
| `codegraph files` / `status` / `sync` | 文件结构 / 索引统计 / 手动增量同步 |

**使用要点（实测踩坑，务必遵守）**：

1. **查询必须用符号名或英文术语**——`explore` 是词法匹配，**纯中文自然语言查询会直接失败**；中文查询必须夹带英文标识符。
2. **改代码前先查影响面**（比 grep 逐层追溯高效）。
3. **行号可信，但 `callers` 建议交叉验证**（实测有调用点只出现在 `impact` 中而缺失于 `callers`）。
4. **CLI 无文件 watcher**：索引是静态快照，改代码后需 `codegraph sync`。
5. **MCP 与 CLI 是两条独立通道**：MCP 工具仅主 agent 可见，**子智能体需经 Bash 调 CLI**；本项目当前未接入 MCP。
6. `codegraph install` 的自动配置列表**不含 Astrion**，如需接入需手动配 stdio server。
7. 检索结果有噪音（FTS 命中不相关符号），需人工过滤。

发现新优点或新问题请记录到项目记忆 `codegraph_code_index.md`。

## 18) 子智能体固定权限与单次命令完全访问（2026-10-07）

- **`create_sub_agent` 必填 `access_level`**：`working_dir_write`/`workspace_write`/`sandbox_write`/`full_access`，创建时校验主权限上限；plan/readonly 可创建两个窄档。sandbox_write口径=工作区＋沙箱授权可写路径。每个实例保存创建时规范化的 `workspace_root`、`working_dir` 与固定档位，恢复时核对原路径身份，**不重新 resolve 后扩大授权**。旧记录缺档位不迁移、不恢复。
- **可信权限唯一入口 = `modules/execution_scope.py` 的 ContextVar**；子工具通过 `modules/sub_agent/execution.py` 使用独立 FileManager/TerminalOperator/TerminalManager，**不修改父对象再恢复**。父权限/执行环境切换不改变已有子档位；子工具只开放自身白名单，不继承父自定义分派，**不开放后台命令或单次升级参数**。
- `working_dir_write`仅子工作目录可写，`workspace_write`为原工作区可写；workspace_root始终是原项目身份，相对路径和默认cwd使用working_dir。macOS临时目录位于对应固定写根内，不附带宿主`/tmp`写授权。原生 IO 用 POSIX no-follow 描述符（或 Windows 句柄），拒绝替换链接、特殊文件与多硬链接写入。工作目录按路径组件检查并安全新建，**不用字符串 startswith**。
- **Docker 不支持 `full_access`**；最低档经 `modules/docker_scoped_launcher.py` 强制 Landlock ABI≥3 + libseccomp + capability 清理；无法安装隔离则命令失败，**不使用 DAC 降级或宿主机回退**。系统工具目录可读、持久写根仅容器工作区（另允许 `/dev/null`）。
- 主 `run_command` 的 `request_full_access=true` **必须提供非空原因**；plan/readonly 拒绝，Docker/远端替身后端不伪装支持。已处于 direct 时无需额外申请。**Windows direct 用 cmd.exe，沙箱 bash 命令不得自动原样重试到宿主机。**
- 单次申请复用 `ToolApprovalManager` 的 `approval_type=full_access`。**开启自动审核时自动与人工独立裁决**，任一拒绝即终结该次申请，两项通过才执行；**拒绝完全访问只回填工具失败结果并继续智能体循环，不触发 task_stopped；用户主动停止仍正常取消**。**人工先允许不能取消自动审核**。授权记录保存执行者/根/对话/任务/工具调用/完整参数，`claim_execution` 原子领取一次，`FullAccessGrant.consume` 在原生命令入口再次核对。授权失效返回正常工具失败结果，**不中断历史消息配对**。内置工具名禁止被自定义注册或分派覆盖。
- 前台、后台均绑定该次权限，不改变主对话全局状态；后台创建经 `asyncio.to_thread`，Popen 与取消在相同锁内握手。**正常后台完成不等于取消全部后台任务。**
- Web 审批 UI 在输入栏 `ComposerApprovalDock.vue`（人工模式：上方滚动内容 + 左下固定拒绝/允许；自动审核时才加右侧进度区）。同对话串行审批、不加翻页；完成后不自动展示记录卡片或恢复入口，`/` 与 `+` 的手动入口始终能打开。视觉由用户验收。
- 回归目录：`test/2026-10-07_子智能体权限与单次完全访问/`、`test/2026-10-07_审批面板交互/`。

> **改子权限 / 审批前必读**：`docs/agents_details/sub_agent_access.md`（含 UI 细节与逐条约束，与本文同级）。

## 19) 运行中对话显示快照（2026-10-08）

- **桌面标签导航（2026-10-23）**：标签唯一写者为 `modules/desktop_navigation/`，双 WebView 仅提交幂等命令及消费版本快照；稳定 key 不随新对话绑定改变，去重与运行态按工作区＋对话识别。`ui/desktopNavigation.ts` 在完整状态更新后消费目标，加载仍走唯一 bootstrap，显示提交前校验 renderer/navigation 所有权；禁止恢复 eval 标签桥、全列表 PUT 或双页本地生成身份。关闭不停止任务，删除清理标签，刷新继承 pending 目标。完整约束及回归见 `docs/desktop_navigation.md`。
- **显示加载唯一入口**：`server/conversation_bootstrap.py` + `static/src/app/methods/conversation/bootstrap.ts`；契约见 `docs/运行对话显示快照契约.md`。服务端 `conversation_view/` 冻结已提交历史前缀并增量维护当前任务显示投影，同一显示事务捕获投影、主任务摘要与绝对 `next_event_idx`；前端离线构造后一次提交，再用 `attachSnapshot` 从该游标续接。**有限事件 deque 只用于增量，不用于重建全部显示。**
- **禁止恢复旁路**：不要恢复 `restoreTaskState`、从事件 0 重放、末条角色/工具猜边界、文本匹配去重、全历史扫描补工具注册、mounted/watcher 第二次恢复、对账独立写消息/归零游标或全局 task handler。
- **所有权**：导航使用 `conversation/session.ts` 的 session 身份与 epoch（空页面也推进 epoch）；轮询独立使用 generation/request owner。**所有异步响应、catch/finally、timer 在写状态前检查归属，不能只比较 conversation/task 字符串 ID。**
- **跨对话索引**：`ui/workspace.ts` 的 `/api/tasks` 列表使用独立请求身份，**不绑定导航 session**；切换对话不停止刷新，失败保留缓存并重试，页面卸载才退役。输入空闲但任务缓存运行时，发送前重新核实；标题统一经 `conversation` store 更新各类型/工作区/搜索缓存，补查走显式对话与工作区的 `/metadata`，不读 `/current`。
- **身份与生命周期**：事件和持久化共享用户 `message_id`；工具 preparing/execution/call 别名在 hydrate 一次登记。压缩提交后 rebase 显示前缀并接管新快照；检查点按用户/工作区/对话退役旧显示。前台结束但后台仍活动时保持活动状态。
- **存储边界**：`read_conversation`（兼容名 `load_conversation`）**不做迁移回写**；同持久化目录共享锁。**锁顺序 = 显示锁 → 任务锁**，不在任务锁内反向获取显示锁。仍维持单后端进程约束。
- **工作区导航（2026-10-12）**：目标工作区与对话随快照一次提交，选择写入串行；同源 API 派发时捕获明确工作区，普通查询不选择工作区。首次路由先获取真实身份，**未知作用域不得猜为 default**。目录刷新不改写已提交的可见身份；草稿恢复失败反馈后继续页面初始化。具体约束见 `docs/agents_details/display_snapshot.md`。
- **输入草稿与运行引导（2026-10-23）**：删除提前输入排队 UI、自动续发与公开队列接口；主任务运行中发送直接引导，失败保留输入。引导附件复用普通发送的 media_paths/file_paths 隐藏通知，主输入栏标记 media_paths_only，实时注入和历史重建均只传路径。草稿按认证用户／工作区／对话独立，空页每工作区一份；localStorage 同步保存，服务器后台重试，以 revision CAS 与 client/sequence 防旧写覆盖；不用无 CSRF beacon，导航不等待远端保存。未消费内容恢复草稿并去重，落盘后才确认来源。完整约束见 `docs/agents_details/display_snapshot.md`，回归 `test/2026-10-23_对话草稿与运行引导/`。
- **查询／运行分层**：`context/workspace.py` 解析可信路径，`data.py` 提供轻量存储，`resources.py` 装配执行实例；bootstrap、列表、草稿、Token、模式及导航辅助查询不得为读取而创建 WebTerminal／容器或复制技能。必要后台恢复异步按完整身份合并；技能成功版本属于工作区，草稿按作用域排序并保留旧请求水位。
- 回归：`python -B test/2026-10-07_运行对话快照/run_regressions.py` 与 `test/2026-10-12_工作区切换重构/README.md` 中的命令（需已安装依赖与 Node）。视觉由用户验收；**不将离线回归等同于实际速度达标或所有卡顿消失**。

## 20) Linux host 沙箱（2026-10-08 首批适配）

- **独立安装入口**：`sudo bash scripts/setup-linux-sandbox.sh`（默认授权 `SUDO_USER`；root 直接安装必须 `--user <普通账户>`）。支持 `--dry-run`/`--status`/`--no-install-dependencies`。**不依赖 Astrion CLI，也不启动后端。**
- **首批环境**：Ubuntu 24.04 / kernel 6.8 / systemd 255 / cgroup v2 / AppArmor / libseccomp；**其它发行版不自动放行**。安装器可装系统依赖，但**不得通过关闭 AppArmor/userns 限制或降级 direct 来绕过失败**。安装器全局安装与网页 sudo/polkit 仍需实机验收。
- **可信助手**：`/usr/local/libexec/astrion-sandbox` + `/etc/astrion-sandbox/helper.json` + `astrion-sandbox.service`。代码、配置、C 身份入口与 BPF 必须 root-owned；**运行时不从用户源码树导入 root Python 模块**。升级保留旧助手备份，验收失败回滚。
- **调用与身份**：普通客户端经固定 SEQPACKET socket 传严格 JSON 与三个 stdio FD；SO_PEERCRED/SO_PEERSEC/pidfd 核验身份，拒绝 UID0 与递归调用。**UID、groups、助手路径与 unit 属性不能来自模型参数。** 特权阶段仅挂载；任意用户命令只能在 C 入口降到真实普通 UID/GID、清能力、开 NNP 后执行。
- **文件根**：系统目录与精确清单唯一来源 `modules/linux_sandbox/constants.py`，主/子/只读/后台/持久终端统一走 `plans.py`。源在普通用户 fork 中逐组件 no-follow 打开并固定 FD，cwd 降权后再次验证固定 inode。**`/proc`、`/dev`、`/sys` 与内部 runtime 禁止作为宿主挂载授权。**
- **网络与回收**：共享 host 网络；每任务独立 transient unit（restricted=deny any + allow localhost，none=deny any）。runner 检查 enforcing 标签与实际 BPF，缺失则拒绝。客户端断开/pidfd 退出、助手停止与取消均回收本任务 unit。
- **证据与限制**：`test/2026-10-08_Linux_host沙箱实验|助手|适配/`。**这些是有限验证，不等同跨发行版安全认证或生产全局安装验收。**
- 安装脚本**不设置或存储网页登录密码**（与 §21 分离）。

## 21) Host 网页可选密码保护（2026-10-08）

- **默认免登录**：未运行密码脚本、缺少配置时沿用回环免登录。**禁止**按监听地址或推测公网状态强制初始化；存在但损坏的密码配置**必须报错，不能静默降为免登录**。
- **唯一开启/重设入口**：`python3 scripts/host_password.py --password 'YOUR_PASSWORD'`（8–1024 字符，保留空白；可加 `--data-dir`）。每次执行开启保护并撤销旧网页登录会话；**不打印密码**。
- **存储唯一权威**：`modules/host_auth.py` → `<DATA_DIR>/host_auth.json`（版本、enabled、scrypt 哈希、generation），0600 原子写；脚本与网页共用跨进程文件锁。**缺文件只读不创建**；可修复损坏的常规配置，**不写穿符号链接**。
- **scrypt 实现兼容**：`modules/host_auth_hash.py` 用项目已有 `cryptography` 的 Scrypt（固定 n=32768/r=8/p=1/64 字节，保留 Werkzeug 哈希格式与随机盐，恒定时间比较）。**生成和校验都不依赖 `hashlib.scrypt`**（本机 Apple Python 3.9 缺该属性），**不能只修脚本而漏掉登录校验**。
- **网页认证**：`server/host_password.py` 管策略；`server/auth.py` 的 `/host-login` 收密码。登录 nonce 与 generation 共同校验，开启/重设/关闭令旧 session 失效。**启用时隐藏并拒绝普通账户登录/注册**。设置→通用只提供「验证当前密码后关闭」，**没有网页开启/重设接口**。
- **桌面豁免**：以壳启动后端时注入的 `ASTRION_DESKTOP_VERSION` 判断，**不能相信网页 JS、HTTP 头或 query 标记**。桌面不读取或应用 Host 密码配置。
- **本机 Bearer 独立**：`gateway_auth` 只注入请求内身份，不发行浏览器 nonce/Cookie；无效 Bearer **不回退 Cookie**。
- **运行任务保持**：密码变更不走 logout、不清理终端/任务、不取消正在运行的工作。
- 回归：`python3 -B test/2026-10-08_Host密码保护/run_regressions.py`（临时数据目录与合成凭证，禁读真实 `.env` 与密码/token）。

## 22) 主／子智能体 API 花费（2026-10-08）

- **范围**：总花费 = 主 + 每个子智能体，**三个审核智能体暂不计入**。按非缓存输入、缓存命中输入、输出三项计价，不单列缓存写入；缓存命中率的冷启动豁免不减免费用。原有 token 五项仍保持主智能体口径。
- **价格与账本**：`modules/api_pricing.py` 捕获请求当时的精确提供商/模型价格；`modelsdev_registry.py` 版本 2 缓存保留 `cost.input/cache_read/output` 及来源时间，**不借其它提供商价格**。`modules/conversation_costs.py` 在 `<工作区 data_dir>/api_costs/<conversation_id>.json` 原子保存，以请求 UUID 防重、Decimal 累加。**旧历史、缺单价或缺 usage 标记为未计价，禁止当免费。**
- **子恢复**：`modules/sub_agent/usage.py` 统一归一化、保存 `token_usage.cached_input`、继承累计统计；两个恢复入口都调用。压缩只清上下文、**不清历史累计**。
- **币种**：外观 `display_currency` = USD/CNY，默认 USD。**人民币只转换显示，不改账本**；`modules/exchange_rates.py` 每 UTC 日后台更新 Frankfurter 参考汇率，失败保留缓存，**无首次缓存不虚构汇率**。
- **等待态**：头像 `api_request_start` 的明确等待态优先于上次工具意图与残留流式状态；响应开始或任务终结清除，停止后不重显。
- 契约 `docs/conversation_api_costs.md`；回归 `/opt/homebrew/bin/python3.11 -B test/2026-10-08_主子费用/run_regressions.py`。网络刷新、真实账单与前端视觉需实际运行验收。
- **工作段 tokens/s（2026-10-19 更新）**：Chat Completions 保持 `output tokens / 首个有内容增量至流结束耗时`（含思考、不做输入差分）；**所有 Responses 只统计正文＋工具调用**，分子为 `output_tokens − reasoning_tokens`，耗时由 `utils/api_client/responses/speed.py` 在原始 SSE 层记录正文增量／工具名及参数至对应 done 的区间并取并集，排除思考、首段等待、完成尾段与工具执行，不使用缓冲后的摘要／参数到达时间。缺推理明细或有效计时则跳过速度样本，**不得回退到含思考口径**。费用与累计 token 始终使用完整 usage；账本原有 `duration_ms`／`ttft_ms` 保留，Responses 另存 `speed_sample={output_tokens,duration_ms}`（空对象表示跳过）。段级取 `Σ样本输出 / Σ样本耗时`，样本在 ContextManager 内存按工作段累积，段真正结束时写入锚点 user 消息 `metadata.token_speed` 并随 `token_update` 下发（任务完成时前端只本地标记 work_timer、**不重载历史**）。前端在该段最后一条有文字输出的 assistant 下方渲染「左侧复制按钮＋右侧 x.x tokens/s」常驻行，段末不是模型文字或无有效样本则不显示。回归：`test/2026-10-19_Responses输出速度/`，完整口径见费用契约。
- **压缩续接即同一工作段（2026-10-16）**：深压缩续接**不得**提前 `finalize_user_work_timer()`，续接递归必须传 `work_anchor_message_id` 继承原锚点（编辑摘要、work_timer、tokens/s 共用同一锚点消息）；否则前端编辑摘要卡片会在任务仍在续接时提前显示，且段真正结束时无人收尾。实现细节与踩坑见项目记忆 `work_segment_anchor_and_tokens_speed`。

## 23) 项目 Git 记录与自动评审（2026-10-13）

- **入口与范围**：侧边栏「工作流」下面的「项目 Git」进入 `/git?workspace_id=<当前工作区>`，只扫描进入时已确认工作区内的 Git 工作树；发现与详情都按该单一工作区解析，跳过依赖/运行态目录、不跟随符号链接。入口图标使用本地官方 Git SVG，视觉尺寸 17px、热区沿用侧栏规范。
- **阅读布局**：Git 为无应用侧栏的独立全屏页；仓库下拉与带进出动画的提交选择面板互斥，文件导航默认展开，diff 占主要空间。文件开关放在文件数左侧并对齐提交作者头像中心；没有专注按钮。提交说明保持单行，左侧无 hover 的符号打开完整 message 浮层；评审记录不带数字，通过浮层菜单选择真实对话。
- **查询唯一权威**：`modules/project_git/`；Web 蓝图 `server/project_git.py`，任务启动 `server/project_git_review.py`。历史 diff 来自固定 Git 对象，提交默认第一父版本，分支使用 merge base；不能用当前工作树代替目标版本。
- **评审**：新建目标工作区的普通对话，初始 `execute + readonly + sandbox`，自动发送固定目录和完整 SHA 的请求，沿用正常 RuntimeService/门闸/显示快照。点击时工作区有活动主任务就提示忙，不新建、不排队；不改变其他入口并发规则。
- **身份与数据**：查询不创建执行实例；评审会话独立创建、不切换现有 terminal。跳转携带显式 `workspace_id`，首次初始化必须验证后等待串行选择完成再加载对话。关联记录原子写入工作区运行态 data，每仓库最近 200 条；未知任务状态不视为成功。
- **首批边界**：只读本地 Git 浏览与评审，不包含远端 fetch、GitHub/Gitea PR 接入。视觉和真实模型执行由用户验收。
- 完整契约、API 实现位置与验证命令见 `docs/project_git.md`；回归目录 `test/2026-10-13_项目Git/`。

## 24) CrossPet 本机桌宠接入（2026-10-14）

- **可选 host 输出**：默认关闭，由 Astrion 设置 → 通用的「CrossPet 联动」单一开关控制；所有工作区共用，存 `DEPLOY_CONFIG_DIR/crosspet.json`。不要求 CrossPet 侧启用；其可选 `astrion.json` 只提供目录。默认输出 macOS `/tmp/crosspet`、Windows `%LOCALAPPDATA%\CrossPet\state`；Docker/web 不输出。关闭后停止三个文件所有写入，保留旧文件由读取端过期；保存与输出共用锁。协议见 `docs/crosspet_integration.md`。
- **观察入口**：`server/crosspet.py` → `modules/crosspet/`；任务线程只派发白名单元数据，配置、模型解析、后台活动探测和文件 IO 都在观察器线程。关闭时不探测后台活动或读取前台对话模型，不创建执行实例。设置端点 `server/crosspet_settings.py` 同时挂在 Web/headless，host 管理员可改，开启时启动或唤醒观察器。
- **状态与隐私**：独立 `astrion-state.json`，身份哈希，不包含消息、思考、工具参数、命令、结果或凭据；心跳不增加动作修订号。`task_complete` 不是最终结束，真实终态和后台活动共同决定动作；业务失败不得庆祝。最多输出 256 个会话，保留前台、活动及等待会话优先。
- **配套 CrossPet**：开发副本在 `_experiments/crosspet/`，原始 ZIP 基线在 `_experiments/crosspet-upstream/`，均不纳入 Astrion Git。独立来源合并，不覆盖现有 AI 钩子；45 秒过期清理自身状态。素材授权不变，不复制素材进 Astrion。
- **订阅额度（2026-10-15）**：`modules/crosspet/quota.py` 以 Astrion 独立 OAuth 和已有代理后台查询用量，只有联动开启且订阅已连接才查询；不在任务或开关锁内做网络 IO。额度仅以白名单数值附加到 `quota.gpt`，不输出账号/凭据/原始响应。CrossPet 保留原有「显示 GPT 额度」（仍只读本机 Codex），新增与其互斥的「显示 Astrion 的 GPT 额度」；打开 Astrion 选项时读取快照额度并标注来源，失败保留旧数据、不混入其他账号也不自动切回 Codex，重置到期显示待更新而非推算 100%，15 分钟旧数不显示，来源/登录代次变化不误庆祝。API 余额与费用不包含在此功能。
- **接入方式与上游 PR**：CrossPet 侧不需要安装钩子，默认两边目录一致即可工作；仅在使用自定义目录时通过 CrossPet 设置 →「接入 AI」里的 Astrion 项登记（等价命令行 `install astrion`，只写 CrossPet 自己的 `astrion.json`，保留已登记目录与其他字段，未登记时撤销不建文件）。安装/卸载脚本不新增 Astrion 自动目标，`refresh` 不改写登记；macOS 的 `CROSSPET_STATE_DIR` 只作用于 Astrion 文件与 `seen-astrion`，其他来源与 `pet.pid` 保持 `/tmp/crosspet`。**上游 PR 已开：**https://github.com/lokicorvus/crosspet/pull/21（fork `JOJO6618/crosspet` 的分支 `feat/astrion-integration`，提交 `3a53433`，32 个文件、+799/−35）。提交时上游 `main` 已前进到 1.3.5，因此用 `_experiments/merge_crosspet_135.py` 做三方合并（base 1.3.4、ours 1.3.5、theirs 改动），**不要直接把 1.3.4 补丁 `git apply` 到新基线**；唯一冲突在 `app/web/index.html`（脚本引入顺序与 `applyCharState` 的来源合并 + `standsIn` 代演融合）。材料在 `_experiments/crosspet-pr/`。
- **回归**：`test/2026-10-14_CrossPet接入/` 的 unittest 和 `runtime_check.py`；独立开关回归 `test/2026-10-15_CrossPet开关/` 的 unittest 和 `settings_api_check.py`。设置 UI 构建/lint 通过后由用户验收；Windows 原生运行仍需实机验证。新增订阅额度与收窄改动按用户要求只改文件，运行测试与原生构建留给用户，不将前轮通过记录当作额度接入已验证。

## 25) 对话级目标模式（2026-10-17）

- **文件唯一权威**：`<工作区 data_dir>/goal_states/<conversation_id>.json`，全部续接保留对话 ID。入口弹窗填写后显式开始；禁止恢复输入栏 armed／下一条消息自动变目标的旧行为。
- **生命周期**：关闭目标不取消当前任务；停止主任务持久暂停目标。paused / blocked 均能手动继续，进程重启只恢复暂停显示，不自动续跑。自动后台通知不得解除暂停／阻塞。
- **执行与交互**：开始／继续设 execute；权限已有 auto_approval / unrestricted 则保留，其余变 auto_approval。活动目标禁止 ask_user、submit_plan、人工确认工具和单次完全访问申请；正常自动审核与智能体内部通信保留。
- **重试与并发**：网络耗尽后通过 RuntimeService 在同一对话派发新 user 输入；额度／认证／配置错误阻塞。Timer 绑定 run_id + task_id + next_retry_at，不能因用量更新 revision 而丢失重试。显示发布锁序固定 VIEW_LOCK → 目标状态锁，禁止反向获取。
- **审核与限制**：以原目标和真实执行证据判断，不用“没有工具调用”代替审核；审核取证强制只读沙箱，不继承主对话 direct。轮数／Token 限制在目标弹窗可选，继续保留累计统计并重置本次限制基线，Token 来自同对话主／子新请求。
- 详细契约：`docs/agents_details/goal_mode.md`；回归目录：`test/2026-10-17_目标模式重构/`。视觉和真实模型／网络恢复由用户运行验收。

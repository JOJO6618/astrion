# Repository Guidelines (Code-Verified)

> Last verified against current codebase: 2026-06-25

这份文档基于当前仓库实际代码重写。若与未来代码冲突，以代码为准并及时更新本文档。

> 和用户交流时默认使用中文。

## 0) 项目名称

本项目（仓库 `agents`）的暂定产品名为 **Astrion**。

- **构词**：`Astr-`（希腊语 *astron*，星星/天体）+ `-ion`（粒子/状态后缀，如 photon、electron）。
- **意象**：星之子、星际粒子、来自宇宙尘埃的微小发光体。
- **寓意**：Agent 像星际间传递信号的粒子，把人类意图传递到工具、文件、终端与子智能体之间；同时把零散任务像星尘聚合成恒星一样，聚合成可执行结果。
- **状态**：暂定名，后续若变更需同步更新 `AGENTS.md`（及本地 `claude.md`，未随仓库发布）。
- **中文参考**：阿斯特里恩；简称可考虑 **星子**。

## 1) 当前项目结构（按代码现状）

- **后端入口**
  - `main.py`: 统一启动入口（当前默认走 Web 模式 + thinking_mode=True）
  - `server/app.py`: 推荐的 Web 服务入口（封装并转发到 `server/app_legacy.py`）
  - `web_server.py`: 兼容入口，已标记 deprecated，但仍可启动
- **后端核心目录**
  - `server/`: Flask 业务主线（chat/task/status 已拆分为子包：`server/chat/`、`server/status/`、`server/tasks/`；REST 任务轮询为主，Socket.IO 主要用于兼容与实时辅助通道）
  - `core/`: 终端与工具编排（`main_terminal.py`、`web_terminal.py`、`main_terminal_parts/*`；其中 `main_terminal_parts/context/` 和 `main_terminal_parts/tools_definition` 已拆分为 base + mixin 子包）
  - `modules/`: 可复用能力模块（terminal/file/memory/sub_agent/upload_security/user 等；`file_manager`、`persistent_terminal`、`terminal_ops`、`mcp_client_manager` 已拆分为子包）
  - `config/`: 配置拆分（`api.py`, `limits.py`, `terminal.py`, `paths.py` ...），由 `config/__init__.py` 聚合并加载 `.env`
  - `utils/`: API client、日志、上下文与对话工具等公共函数（`api_client`、`tool_result_formatter`、`context_manager`、`conversation_manager` 已拆分为子包；原入口文件保留为兼容入口）
- **前端目录**
  - `static/src/`: Vue 3 + TS 前端
  - `cli/src/`: React 19 + Ink 6 + TypeScript CLI 前端（正在重写中）
  - 监控动画相关核心文件：
    - `static/src/components/chat/monitor/MonitorDirector.ts`
    - `static/src/stores/monitor.ts`
    - `static/src/components/chat/monitor/*`
- **CLI 相关文档**（本地文档，未随仓库发布）
  - `docs/cli_ui_display_spec.md`: CLI 显示层设计说明
  - `docs/cli_slash_commands_spec.md`: CLI `/` 指令设计说明
- **其他子项目/资源**
  - `android-webview-app/`: Android WebView 客户端工程
  - `modules/sub_agent/`: 子智能体执行逻辑（主进程内 `asyncio.Task`，工具调用复用主进程链路）
  - `easyagent/`: 旧版 Node.js 子智能体实现，暂时保留但已不再使用
  - `_experiments/`: 本地实验残留与历史文档归档目录（**不纳入 git**，见 §7）

## 1.5) 运行态数据目录与路径变量（2026-06 更新）

> 设计目标：运行态数据（对话、用户工作区、日志等）默认存放在用户主目录的 `~/.astrion/astrion/` 下，对齐 `~/.claude`、`~/.codex` 惯例，**不落在源码树内**。配置实现见 `config/paths.py`。

### 1.5.1 默认位置与模式分流

运行态数据根目录（`data_root`）默认为 `~/.astrion/astrion`，可通过 `ASTRION_DATA_ROOT` 整体搬迁。在该根目录下，按运行模式自动分流（由 `TERMINAL_SANDBOX_MODE` 决定）：

- 宿主机模式（`TERMINAL_SANDBOX_MODE=host`）→ `~/.astrion/astrion/host`
- 其它模式（默认 docker / web）→ `~/.astrion/astrion/web`

数据根目录下还包含：`settings.json`（唯一配置文件）、`config/`（部署级配置，host/web 共享）。每个模式目录下包含：`data/`（对话、用户库、记忆、sub_agents.json、sub_agent_tasks 等）、`users/`（web 多用户工作区）、`api/users/`（API 用户工作区）、`logs/`（日志）。

### 1.5.2 路径解析优先级（从高到低）

1. **具体目录环境变量**：`DATA_DIR` / `LOGS_DIR` / `USER_SPACE_DIR` / `API_USER_SPACE_DIR`（单独覆盖某个目录，最高优先级）
2. **数据根目录环境变量**：`ASTRION_DATA_ROOT`（整体搬迁运行态数据根目录，默认 `~/.astrion/astrion`）
3. **兜底默认**：`~/.astrion/astrion/<mode>`

具体目录变量支持相对路径（相对仓库根目录展开）、绝对路径与 `~`。

> 注意：`config/*.json` 分两类：
> - **程序能力**（`docker_risk_markers.json`、`skill_hints.json`）：是程序行为的一部分，随版本演进，仍锚定源码树。`prompts/`、`agentskills/` 同理。
> - **部署级配置**（`custom_models`、`host_workspaces`、`auto_approval`、`goal_review`、`forbidden_commands`、`host_sandbox_policy`）：因部署/机器而异或含密钥，外置到 `~/.astrion/astrion/config/`（即 `DEPLOY_CONFIG_DIR`，可单独用该环境变量覆盖，host/web 共享）。读取走 `config.resolve_deploy_config(name)`，回退链：部署目录 → 源码树 `.json` → 源码树 `.json.example`，因此开发环境不必先跑 setup 也能用源码树种子。含密钥/机器特定的 5 个（除 `forbidden_commands`）不纳入 git，仓库仅留 `.example`。

### 1.5.3 Host / Web 双路径机制（2026-06 新增）

`paths.py` 新增三个固定变量，支持 host 模式同时读取 web/ 数据：

- **`IS_HOST_MODE`**：当前是否为 host 模式（布尔值）
- **`WEB_DATA_DIR`**：固定指向 `<data_root>/web/data`（不受当前模式影响）
- **`WEB_USER_SPACE_DIR`**：固定指向 `<data_root>/web/users`（不受当前模式影响）

**host 模式**（`modules/user_manager.py`）：
- 用户列表：同时加载 `host/data/users.json` + `web/data/users.json`（web 用户不覆盖已有）
- 工作区列表：合并 host/ 和 web/ 两处
- 工作区定位：已有 web 工作区直接复用；新工作区写入 host/
- 写操作（创建/删除/重命名）：始终走 host 路径

**web 模式**：只读取 web/ 数据，无变化。

> 设计意图：host 模式 = host + web 数据都可用；web 模式 = 仅 web 数据可用。

### 1.5.4 日志策略（2026-06）

- API 请求体落盘（dump）默认**关闭**，由 `AGENT_API_DUMP_ENABLED` 控制（`1/true/yes/on` 开启）。
- 日志混合轮转（实现见 `utils/log_rotation.py`）：
  - 追加型单文件（`host_workspace_debug.log`、`chunk_*.log`、`conn_diag.log`、`api_debug.log`、TaskLogger/ErrorLogger）按大小轮转，默认单文件 20MB、保留 3 份。
  - 按份计量目录（`api_requests/` 一请求一文件）只保留最近 N 个，默认 30。
  - 阈值可覆盖：`AGENT_LOG_ROTATE_MAX_BYTES` / `AGENT_LOG_ROTATE_BACKUPS` / `AGENT_DUMP_KEEP`。

### 1.5.5 数据迁移

- 迁移脚本：`scripts/migrate_runtime_data.py`（源码树 → 运行态根；复制+备份+可回滚+幂等，`logs/` 丢弃不迁）。
  - 脚本 `import config` 复用程序同一套路径解析（含 `.env`），**模式由 `.env` 决定**；执行前务必先 `--dry-run` 确认目标。
- 清理误迁副本：`scripts/cleanup_misplaced_web.sh`。

## 2) 当前可用启动/构建命令（已按代码核对）

### Python / 服务端
- 安装依赖：`pip install -r requirements.txt`
- 推荐启动 Web：`python -m server.app`
- 可选参数：`python -m server.app --path ./project --port 8091 --debug --thinking-mode`
- 兼容启动方式：`python web_server.py`
- 统一入口：`python main.py`（当前实现会默认进入 Web 启动流程）

### Frontend（根目录）
- 安装依赖：`npm install`
- 构建：`npm run build --silent 2>&1 | tail -n 5`（默认只看最后 5 行，减少 Vite 构建资源列表等无关输出）
- 开发监听（当前脚本是 build watch）：`npm run dev`
- Lint：`npm run lint`

### CLI（React / Ink）
- 安装依赖：`npm --prefix cli install`
- 开发启动：`npm run cli` 或 `npm --prefix cli run dev`
- 构建：`npm run cli:build`
- 类型检查：`npm run cli:typecheck`
- 可执行命令名（构建后）：`agents` / `agents-cli`

## 3) 测试现状（不要再写过时命令）

- 当前仓库内可见自动化冒烟：`test/test_server_refactor_smoke.py`（`unittest`）
  - 运行方式：`python -m pytest test/test_server_refactor_smoke.py -q`
  - 注意（2026-08-01 实测）：`python -m unittest test.test_server_refactor_smoke` 在标准 Windows Python 上失败——stdlib 自带常规包 `test` 遮蔽本地 `test/` 目录（无 `__init__.py` 的命名空间包优先级更低），报 `No module named 'test.test_server_refactor_smoke'`。无 pytest 时可用：
    `python -c "import sys; sys.path.insert(0, '.'); import unittest; suite = unittest.TestLoader().discover('test', pattern='test_server_refactor_smoke.py'); r = unittest.TextTestRunner().run(suite); sys.exit(0 if r.wasSuccessful() else 1)"`
- `test_system_message.py` 依赖外部 `MOONSHOT_API_KEY` 与网络，不属于离线稳定 CI 用例。
- 当前仓库未发现 `pytest.ini`/`pyproject.toml`/`tox.ini`；不要默认要求 `pytest` 作为唯一入口（仅作为冒烟测试的便捷运行器）。
- CLI 当前最小可复现验证：
  - `npm --prefix cli run typecheck`
  - `npm --prefix cli run build`
- 若改动 `server/chat/`、`server/status/`、`server/tasks/` 等后端接口适配，补充：
  - `python3 -m py_compile server/chat/*.py server/status/*.py server/tasks/*.py`
  - `python -m pytest test/test_server_refactor_smoke.py -q`

## 4) 代码修改约定（实用版）

- **根治优先原则**：修复问题时优先判断能否根治；能根治的，应告知用户根治方案与影响范围并询问是否执行，在一定范围内的重构是可接受的，尽可能避免打补丁式的临时修改。
- **精确读取原则**：禁止整文件通读。阅读/修改前先用搜索工具（grep / 符号查找 / 关键字定位）锁定需要的行段，只精确提取并阅读相关片段，不要把整个文件一次性读进来。
- **文件编辑方式**：修改文件时优先使用 `apply_patch` 或其他原生文件编辑工具；尽量不要用 `bash`/`python` 脚本批量改文件，除非原生工具明显不适合。
- **后端改动优先级**：先改 `modules/`、`server/` 内对应模块，最后才动入口。
- **前端改动优先级**：按 `static/src` 现有分层改（`app/`、`stores/`、`components/`、`composables/`）。
- **CLI 改动优先级**：优先在 `cli/src/App.tsx`、`cli/src/components.tsx`、`cli/src/eventMapper.ts`、`cli/src/api.ts` 内做最小闭环修改。
- 涉及 monitor 动画/事件联动时，至少同步检查：
  - `MonitorDirector.ts`（动画与场景执行）
  - `stores/monitor.ts`（事件队列与状态机）

## 5) 风格与质量

### 文件长度与拆分

- **单文件最好不超过 1000 行**；超过时应优先考虑合理拆分，避免维护成本快速上升。
- **行数不是硬性指标**：文件是否拆分取决于**可维护性**，而不是必须小于某个固定行数。
- 当单一文件超过约 500 行时，应评估是否具备以下拆分价值：
  - 业务逻辑过重、职责不单一；
  - 存在清晰的边界（如按工具类型、按资源、按生命周期阶段）；
  - 拆分后能提高复用性、可读性或测试便利性。
- **不要为了拆分而拆分**。边界模糊、耦合紧密或改动频率低的文件，保持现状可能更划算。
- Vue SFC 因模板、样式、脚本耦合较深，拆分风险较高；优先抽离 composable 和子组件，而非直接切分 `.vue` 文件。

- Python：4 空格、`snake_case` 函数、`PascalCase` 类，新增公共函数尽量补 type hints。
- Vue/TS：保持现有代码风格，不做无关风格清洗。
- CLI React/TS：保留现有 Ink 渲染方式与光标修正逻辑，不要轻易重写输入框定位策略。
- 日志：优先复用现有 logger/日志路径，不引入大量临时 `print`。
- 提交前至少做与改动相关的最小验证（命令输出或手工步骤要可复现）。
- 视觉验证默认由用户完成：除非用户特别说明，所有需要视觉确认的修改（尤其是动画/过渡效果），构建/lint 通过后交由用户亲自查看确认，不要用 Playwright 截图或浏览器自动化代替用户验收。
- 运行根目录前端构建时，默认使用 `npm run build --silent 2>&1 | tail -n 5`。

### CLI 当前交互约束（2026-05-15）

- CLI 连接的是现有本地 Web API（默认 `127.0.0.1:8091`），不是独立 agent runtime。
- 启动 CLI 时应清屏、连接本地服务、创建新会话，并将输入区固定在底部。
- 当前目录若不在工作区中，应先弹出“是否添加到工作区”的选择。
- 思考内容当前默认隐藏，只显示“思考中 / 思考完成”标题；相关折叠代码保留，后续可继续修。
- 不要在未获得用户要求的情况下运行交互式 TUI 压测或长时间模拟输入，以免刷屏占满上下文。

## 5.5) 前端设计风格统一规范（强制）

> 适用范围：所有前端 UI（以 `static/src` Web 为主；`cli/src` 在视觉可类比处同样适用；以及未来新增的任何界面）。
> 约束级别：写新 UI 或改动 UI 时**必须遵守**；遇到存量违规应在最小改动允许范围内顺手修正。
> 配色基础设施：两层 token（原始层 + 语义层）定义在 `static/src/styles/base/_tokens.scss`（`:root[data-theme='classic'|'light'|'dark']` + 首屏回退 `:root:not([data-theme])`），切换逻辑见 `static/src/utils/theme.ts`；`.stylelintrc.cjs` 三条规则做防回退栏杆，`build` 含 `stylelint` 步骤。

1. **禁止边缘光晕**：不使用任何 glow / 外发光 / 彩色光晕效果（大范围彩色 `box-shadow` 扩散、`filter: drop-shadow` 光圈、`::before/::after` 模糊光晕等）。已有的要移除。允许的只是中性、克制的投影——沿用 `--shadow-*` 既有 token，不自造发光阴影。**仅在用户明确允许或要求时才可使用光晕效果。**
2. **禁止原生浏览器组件**：不直接使用浏览器默认外观的 `<select>`、`<input type=checkbox/radio/range/date/color>`、`alert/confirm/prompt`、原生右键菜单、原生 tooltip 等，一律换成与项目风格统一的自定义组件（下拉 / 开关 / 单选 / 滑块 / 模态 / Toast）。
3. **禁止圆角套娃**：不允许「圆角矩形套圆角矩形套圆角矩形」的多层嵌套卡片——典型垃圾审美，且极大占据有效显示面积。容器层级要扁平，优先用分隔线 / 留白 / 底色区分，不要多包一层带边框圆角的盒子。
4. **图标外框对齐**：同一组图标按钮的外框（点击热区 / 容器）必须统一对齐，不能一会居中、一会左对齐、一会右对齐；同组固定相同尺寸与对齐方式。
5. **图标视觉对齐而非理论对齐**：按图标视觉重心对齐而非几何边界盒；对重心偏移的图标（三角形 / 播放键 / 放大镜等）做微调，使其「看起来居中」。
6. **颜色遵循三模式切换 + 两层 Token**：所有颜色走原生三模式，禁止写死颜色字面量（裸 hex / `rgb()`/`hsl()` 字面色 / `var(--x, #hex)` 兜底），一律用**中性语义** CSS 变量（`--surface-*` / `--text-*` / `--border-*` / `--accent*` / `--state-*`）。三主题定位：**经典抄 Claude 亮色盘**（暖奶油 + 暖橙 primary `#cc785c`）、**浅色抄 ChatGPT 亮色盘**（冷白 + 灰 + 近黑 primary `#181818`）、**深色自研中性灰阶**。亮色表面靠灰阶拉层次（经典 `--surface-base #faf9f5` < `soft #f5f0e8` < `card #efe9de` < `muted #e8e0d2`），不许全塌成白。primary 克制（CTA-only），hover/选中/运行态走中性灰。半透明 tint 用 `color-mix(in srgb, var(--token) N%, transparent)` 派生。`--claude-*`/`--theme-*` 是过时别名，新代码勿用。新增颜色必须三主题（含首屏回退）补齐。
7. **带文字容器固定高度**：所有含内部文字的选项 / 按钮 / 标签 / 容器固定高度，禁止因文字多少被撑大撑高；过长文字用省略号或内部滚动处理。
8. **窗口设最大尺寸 + 内部滚动**：所有弹窗 / 面板 / 列表设置 `max-height` / `max-width`，超出由内部容器滚动，不顶大整个窗口；滚动条要么隐藏，要么做样式适配，不暴露原生粗滚动条。
9. **实体面板禁止半透明**：实体 UI 容器（对话区 / 侧栏 / 下拉/二级菜单 / 抽屉 / 对话框本体 / 状态条 / tooltip / 输入栏）背景必须不透明，且不加 `backdrop-filter` 磨砂。唯一例外：遮罩层 scrim（`--overlay-scrim`，`position:fixed;inset:0`）和刻意玻璃质感装饰保留半透明。
10. **主题变体必须与基础定义同文件（禁止跨文件补丁覆盖）**：组件的 dark/light/classic 主题变体写在该组件自己的样式文件里（文件内统一的 `body[data-theme='…'] { … }` 块），禁止把某组件的主题样式写到别的文件去覆盖。新增主题样式前先确认该组件基础定义所在文件；发现存量跨文件覆盖应顺手回迁并删除原覆盖。`!important` 只准用于遮盖第三方库样式，禁止用于压过项目自有定义（出现这种需求说明应该合并定义，而不是加 `!important`）。审计脚本：`python3 cache/style_audit.py` + `python3 cache/theme_diff.py`（本地脚本，未随仓库发布）。
11. **大面积色块禁止纯黑/近黑**：任何窗口的 hover、头部、背景等大面积色块，禁止使用纯黑（`#000`）或肉眼近黑（如 `#0a0a0a`/`#0f0f0f` 一档）的颜色，深色模式同样如此。深色下的层次靠中性灰阶拉开：hover 用 `--hover-bg`（深色为白色微量 tint，提亮而非压黑），弹窗/面板头部优先用分隔线区分而非深色衬底（参考 `VersioningDialog.vue` 头部）。纯黑/近黑只允许用于小范围强调渲染（如行内代码衬底等小面积场景）。

## 5.6) 工具结果显示规范（强制）

> 适用范围：所有使用 `tool-result-meta` / `tool-result-content` 结构渲染的工具结果（如 `static/src/components/chat/actions/ToolAction.vue`、`toolRenderers.ts` 等）。
> 约束级别：新增或修改工具结果渲染时**必须遵守**。

1. **meta 部分只放参数**：`tool-result-meta` 仅用于展示工具调用的参数、配置、状态标识等元信息。例如：状态、ID、路径、任务描述、超时时间等。禁止在 meta 区域放置执行结果、统计摘要、输出内容等。
2. **content 部分只放结果**：`tool-result-content` 仅用于展示工具执行后的结果、输出、统计、总结等。例如：文件内容、命令输出、搜索命中、执行统计（工作时间 / 调用次数 / 工具次数）、最终回复摘要等。禁止在 content 区域重复展示工具参数。

## 5.7) 调试输出规范（强制）

> 约束级别：需要临时加调试日志排查问题时**必须遵守**。

### 前端调试日志

- **统一筛选词（强制）**：所有临时 `console.log` 必须使用**同一个**筛选前缀（例如 `[route-debug]`），方便用户在浏览器控制台用该前缀一次性筛选。
  - **同一次调试任务中严禁使用多个前缀**。如果涉及多个模块（如状态栏、Git 摘要、用户问题），应共用同一个前缀（如 `[status-bar-debug]`），通过日志对象里的字段区分模块，而不是发明多个前缀增加用户筛选成本。
  - 选择前缀时优先与本次排查的「用户可见现象」对齐，而不是与内部模块名对齐。
- **控制数量与时机**：
  - 禁止一次性输出上百上千条日志刷屏。
  - 只在关键路径（入口、分支判断、状态变化、导航动作）打印，避免在循环、高频事件、每帧渲染中输出。
  - 需要大量结构化数据时，优先用 `console.group` / `console.table` 或对象快照，而不是逐条打印。
- **用完即清**：问题定位后应及时删除或注释掉临时调试日志，不要长期留在代码里。

### 后端调试日志

- **必须写入文件**：后端调试信息禁止直接 `print` 到终端刷屏，必须写入日志文件。
- **复用现有 logger**：优先复用项目已有 logger（如 `utils/logger.py`、`modules/multi_agent/debug_logger.py` 等），按模块落到 `~/.astrion/astrion/<mode>/logs/` 下。
- **关键状态转换必打**：多智能体、子智能体、任务轮询等复杂链路应在关键状态转换点写结构化日志，便于复现问题后按时间线追溯。

## 5.8) 前端多语言（i18n）文案规范（强制，2026-08 新增）

> 详细规范：`doc/frontend/i18n_spec.md`。约束级别与 §5.5 同级：写新 UI 或改动 UI 文案时必须遵守。

- **引擎**：vue-i18n v10（Composition 模式）；文案唯一定义在 `static/src/locales/{zh-CN,en-US}/<namespace>.ts`，中文为源语言。
- **使用**：模板用 `$t('ns.key')`（全局注入、响应式）；SFC script / 纯 TS 用 `import { t } from '@/locales'`（调用时求值、非响应式；响应式标签用 `useI18n()` 或读取 `currentLocale`）。
- **公共词**：高频通用词唯一来源是 `common` 命名空间，禁止在各域重复定义。
- **key 奇偶强校验**：en-US 聚合器用 `DeepString<typeof zhCN>` 约束，en 缺/多 key 直接 tsc 报错；新增命名空间须在 `zh-CN.ts` / `en-US.ts` 同步注册。
- **防回退栏杆**：`npm run lint` 先跑 `scripts/i18n_audit.mjs`（剥离注释后查裸中文，独立命令 `lint:text`）；存量文件列在 `scripts/i18n_baseline.txt`，**迁移完一个文件就删一行**，删除后该文件永久受栏杆保护。
- **语言切换**：个人空间 → 外观 → 界面语言；默认 zh-CN（不跟随浏览器），持久化 key `agents_ui_locale`。
- **边界**：后端下发文字（API error、通知、工具结果摘要）不做多语言，前端原样显示；CLI 暂不纳入。

## 6) Git 工作流（开发 + Review）

### 6.1 核心原则

- 默认直接在 `main` 上开发，不强制新建功能分支。
- 每个功能建议在 `main` 上保持一个清晰 commit，历史干净可回滚。
- 使用 **Conventional Commits**：`feat:` `fix:` `refactor:` `chore:` `docs:` `test:` `perf:`。
- 个人项目，不设 PR 和 Review 流程；如用户临时要求隔离开发，再按需新建分支。

### 6.2 工作流（唯一步骤）

AI 执行以下流程时，每一步都要向用户说明在做什么：

```
0. 检查工作区状态
   git status
   如有未提交的改动：
   - 若无关：提醒用户先 commit 或 discard
   - 若有关：git stash push -m "WIP: <简短描述>" 暂存

1. 确认在 main 并同步最新代码
   git checkout main && git pull origin main

2. 开发并提交
   git add <修改的文件>
   git commit -m "feat(scope): 简短描述"
   （多轮迭代可用 git commit --amend 保持一个 commit）

3. 推送到远程（备份 + 方便切设备）
   git push origin main
```

**重要约束**：
- AI 必须在本地验证修改能正常运行，再建议用户提交。
- 修改完成后按改动规模汇报：大修改说明核心变更点与验证结果；小修改只说明核心变更点。
- 禁止 AI 在未经用户确认的情况下执行 `git commit` 或 `git push`。
- commit / push 授权均为一次性：用户说“可以 commit/push”只代表允许这一次操作；后续如需继续 commit / push，必须由用户再次主动同意。
- 允许 commit 不等于允许 push：两者授权相互独立，获得 commit 许可后仍需单独征得 push 许可。
- 不需要每次都执行或输出 diff；如用户要求 Review，再在同一对话内使用 `git diff` 或 `git diff HEAD~1..HEAD`。

### 6.3 常用命令速查

| 操作 | 命令 |
|------|------|
| 暂存未完成工作 | `git stash push -m "WIP: xxx"` |
| 修改最近 commit | `git commit --amend` |
| 同步 main | `git checkout main && git pull origin main` |
| 同对话内 Review diff | `git diff` 或 `git diff HEAD~1..HEAD` |
| 查看提交历史 | `git log --oneline -10` |

## 7) 安全与仓库卫生

- 严禁提交真实密钥（`.env`、token、cookie、用户隐私）。
- 运行态数据默认在 `~/.astrion/astrion/<mode>/`（`data/`、`users/`、`logs/`、`api/`），不在源码树内；详见 §1.5。`.gitignore` 仍忽略源码树内的 `logs/`、`data/`、`users/`、`api/`、`project/` 等，以防通过具体变量指回源码树或历史遗留产生污染。分享前需脱敏。
- **`_experiments/` 用途**：归档本地实验残留与历史文档（调试记录、旧变更日志、模型测试脚本、翻译资料、旧子智能体文档等）。该目录**不纳入 git**（已在 `.gitignore`）。需要保留但不属于当前主线、又不想直接删的零散文件，统一放这里，不要散落在根目录。
- 不要把本地构建产物（如 `static/dist/`、`node_modules/`）纳入提交。

## 8) Android App 发布联动要求（重要）

当修改前端并需要发布 Android WebView App 时，必须同步执行以下步骤（依据 `doc/android_app_release_and_update.md`，本地文档未随仓库发布）：

1. 同时更新版本信息  
   - `android-webview-app/app/build.gradle.kts`：递增 `versionCode`，更新 `versionName`
2. 同时更新更新说明  
   - `android-webview-app/APP_CHANGELOG.md`：在顶部新增当前版本说明
3. 完成修改后提醒用户运行上传脚本  
   - 在发布流程中运行：`bash ./upload_android_apk.sh`（本地私有脚本，未随仓库发布）
   - 脚本除上传 APK 外，还会同步 `app/build.gradle.kts` 与 `APP_CHANGELOG.md` 到服务器（2026-09-02 修复）

> 禁止只改前端代码而不更新版本号/更新说明，否则会导致客户端更新提示与分发信息不一致。
> 服务端 `/api/app/version` 的版本号是从服务器文件系统上的 `build.gradle.kts` 解析的（实现见 `server/status/docker.py`），**不是从 APK 包解析**——只传 APK 不同步元数据文件会导致 App 检测不到新版本。

## 9) 给 Agent 的硬性要求

- 先读当前代码再执行，不依赖历史文档记忆。
- 输出结果按改动规模决定：
  - 大修改：必须包含核心变更点、验证结果（已执行命令/未执行原因）。
  - 小修改：只需包含核心变更点。
- 不要求每次列出所有修改文件；仅在用户要求、改动复杂或有助于说明时列出。
- 不要求每次执行或展示 diff；仅在用户要求 Review 或排查差异时使用。
- 如果发现本文档过时，直接更新 `AGENTS.md` 并在结果中说明依据。

## 10) 宿主机权限模式与沙箱执行机制（2026-05 更新）

完整说明文档：`docs/host_sandbox_and_permission_model.md`（本地文档，未随仓库发布）

为避免误解，当前系统有两套独立但叠加的控制：

1. **权限模式（Permission Mode）**：`readonly` / `approval` / `auto_approval` / `unrestricted`  
2. **执行环境（Execution Mode）**：`sandbox` / `direct`（仅宿主机模式可切换）

### 10.1 权限模式

- `readonly`
  - `run_command` 可调用，但在只读沙箱执行；写入由系统拒绝
  - 读取同样受限（各平台只读强制的可读边界见 §10.8）；读不到属预期边界，不是故障
  - `write_file` / `edit_file` 等写入类工具直接拒绝
  - 持久终端可自由创建与输入，但以只读身份运行（docker 非特权 uid / 宿主机只读 profile），写入 EPERM 由系统强制；权限跨界切换（受限档⇄unrestricted）时现有终端直接销毁，重开生效
- `approval`
  - `run_command` 先走只读沙箱（可读边界见 §10.8，读越界也会触发权限拒绝）
  - 若出现权限拒绝（例如 `Operation not permitted` / `Permission denied`），触发前端审批
  - 审批通过后，仅该次命令以可写沙箱重试；工具结果返回“重试后的最终结果”
  - **审批只授予该次命令的工作区内写权限，不放大读取**（2026-08-30 方案一）：可写沙箱读边界与只读同一白名单，读越界唯一途径是「路径授权」；重试后仍为文件权限拒绝时，工具结果附带 `read_scope_hint` 引导模型说明
  - **执行环境锁定为沙箱**（2026-08-30 起）：direct 下无沙箱兜底，启发式漏判的写命令会直接执行成功、审批承诺被架空，故批准/自动审核与 direct 硬互斥（锁定机制同 plan/只读，见 §10.6）
  - 持久终端以只读身份运行（同 readonly 条目）：终端内写入永远 EPERM，需审批的写入走 `run_command` 两段式通道
- `auto_approval`
  - `write_file` / `edit_file`：工作区内路径直接执行，工作区外路径进入审批流程
  - `run_command` 先走只读沙箱（可读边界见 §10.8）；触发权限拒绝后由后台审批智能体自动审核
  - 自动审核拒绝时，工具返回“被拒绝+理由”并继续主循环（不强制结束任务）
  - 批准后的读取边界与 approval 相同（不放大）
  - 执行环境同样锁定为沙箱（同 approval 条目）
- `unrestricted`
  - 权限层不做拦截：工作区内读写自由、命令免批准；是否沙箱由执行环境决定
  - 注意「无限制」只管工作区内：宿主机沙箱执行时读边界仍是白名单（见 §10.8），授权范围外读取需路径授权；direct 模式才真正无边界

### 10.2 执行环境

- `sandbox`（默认）：使用 OS 沙箱执行（macOS: `sandbox-exec`，Linux: `bwrap + seccomp`，Windows: `WSL2`）
- `direct`：宿主机直接执行（高风险），**仅 `unrestricted` 权限可选**：受限权限档（只读/批准/自动审核）与 plan 模式下后端 `set_execution_mode` 硬锁拒绝 direct（i18n 键 `main_terminal.restricted_mode_locks_sandbox` / `plan_mode_locks_sandbox`）；进入受限档时若已是 direct 会被联动压回沙箱（记录 `pre_readonly_execution_mode`，切回 unrestricted 时恢复）；存量「受限档+direct」对话在加载时自愈矫正回沙箱
- 切换后一直生效，无自动回退机制（2026-07 已移除原 TTL 自动回退）

### 10.3 路径授权语义

- 前端“路径授权”支持两类路径：
  - **可读可写**
  - **仅可读**
- 语义：
  - 可读集合 = 可读可写 + 仅可读
  - 可写集合 = 可读可写
- 配置来源（2026-08-30 收敛为两个）：`config/host_sandbox_policy.json`（UI 唯一读写目标）+ 真·环境变量 `HOST_SANDBOX_MACOS_WRITABLE_PATHS`（部署通道，与文件合并去重）。settings.json 的 `terminal.macos_writable_paths` 映射已移除（历史值一次性失效，需用路径授权重新添加）；.env 注入技术上仍生效，但不是受支持的通道，不推荐使用

### 10.4 维护约束

- 不要在提示词里暴露内部实现细节（例如“命令文本猜测”等）
- 文案应面向用户能力边界与操作建议，不描述内部判定算法

### 10.5 审核智能体配置与调试

- 审核智能体配置：统一在个人空间「审核智能体」页设置（2026-08 起），存于 personalization.json 的 `review_agents` 键
  - 三个审核智能体：`auto_approval`（自动审批）/ `goal_review`（目标审核）/ `workflow_review`（工作流审核）
  - 字段：`model`（子智能体模型库条目名，留空=模型库 default_model）/ `thinking` / `timeout_seconds` / `max_rounds` / `max_command_timeout`
  - 解析入口：`modules/review_agent_config.py::resolve_review_agent_config`（复用 `sub_agent_models.json` + `_build_sub_agent_profile`）
  - 旧的独立 json 配置（`config/auto_approval.json` / `goal_review.json` / `workflow_review.json.example`）已彻底废除，无向后兼容
- 调试开关（代码变量）：`modules/approval_agent.py` 中 `DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT`
  - 开启后写入：`logs/approval_agent/`
  - 记录以累积 `messages` 为主，便于对齐主/子智能体会话格式

## 10.6) 运行模式（work_mode）系统（2026-08-13 新增）

运行模式（**work_mode**：`plan` / `ask` / `execute`）控制**与用户的交互节奏**，与权限模式（限制模型能力）正交。命名注意：`run_mode` 已被 fast/thinking 思考模式占用，严禁混用。

### 三档语义

- **plan（计划）**：只制定计划并讨论；权限**锁定为只读**且执行环境**锁定为沙箱**（UI 禁用 + 后端 `set_permission_mode`/`set_execution_mode` 双重强制——只读在宿主机依赖 OS 沙箱硬限制，direct 下无沙箱形同虚设）；唯一写例外是 `.astrion/plan/*.md` 计划文档（权限层判定，提示词要求用 write_file/edit_file 直写，不走沙箱）；计划完成后调 `submit_plan` 工具提请批准，**批准后自动切换到 execute**（恢复进入 plan 前的权限与执行环境，无记录时权限回落个性化 `default_permission_mode`、执行环境保持沙箱安全默认）
- **ask（询问）**：先讨论后开工；**禁用 ask_user 工具**（开放式讨论必须直接写在回复里）；与 execute 无执行层差异，仅提示词不同
- **execute（执行）**：自行梳理计划、脑补细节直接开工；仅硬阻塞才提问

### 实现要点（改代码必须知道）

- **后端核心**：`core/main_terminal_parts/tools_policy.py`（WORK_MODES、`get/set/switch_work_mode`、plan 锁；`RESTRICTED_PERMISSION_MODES` 受限档集合 + `set_permission_mode` 内 `_apply_restricted_execution_mode_link` 处理受限档⇄沙箱联动，`pre_readonly_execution_mode` 存对话 metadata——键名保留，语义已泛化为受限档共用）；`switch_work_mode` 处理 plan⇄只读+沙箱联动，`pre_plan_permission_mode`/`pre_plan_execution_mode` 存对话 metadata；执行环境 plan 锁与受限档锁同在 `core/main_terminal.py::set_execution_mode`（只拦 direct）
- **创建对话的模式继承（work_mode + permission_mode 同一原则）**：三条创建路径（prefer_defaults / 显式模式 / safe_navigation）一律沿用 terminal 当前值，**不用个性化默认值覆盖**——/new 页面切换器显示什么新对话就是什么（切换经 `_sync_workspace_terminal_mode` 同步到工作区级 terminal）；个性化 `default_work_mode` / `default_permission_mode` 仅在 terminal 首次构造时生效（tools_policy 加载）。多智能体创建路径（`server/multi_agent.py`）与工作流创建路径（`workflow_runtime_api.py`）同样遵守。plan 锁存在于 `set_permission_mode`，任何创建路径不得在其之前调用非只读 set（曾因此 500）
  - 历史教训：permission_mode 曾长期例外（`a2a04b95` 引入「个性化默认优先」，彼时还没有 /new 切换器同步机制），导致 /new 切只读后新建对话回落无限制（2026-08-30 修复，与 work_mode 对齐）
- **提示词**：`prompts/work_mode.txt` 模板 + `mode.py::_build_work_mode_rules`（三档规则唯一来源，冻结注入与切换通知共用）；冻结注入在 `messages.py`（执行环境之后）；切换走现有 drift 机制（`_RUNTIME_MODE_KINDS` 第四种），通知携带完整新规则文本
- **API**：`GET/POST /api/work-mode`（`server/chat/permission.py`）；**仅空闲可切换，运行中 409**（无 pending 队列）；`plan-approvals` pending/answer 端点在 `server/chat/approval.py`
- **submit_plan 链路**：工具全模式注入（沟通类工具不过滤，非 plan 调用由 handler 运行时兜延返回引导）；`PlanApprovalManager`（`modules/plan_approval_manager.py`）+ `chat_flow_tool_loop.py::_handle_submit_plan` 阻塞等待 → 前端 `PlanApprovalDialog.vue` 弹窗 → 批准则工具循环内切 execute 并静默更新 baseline（避免误发 drift 通知）
- **个性化**：`default_work_mode`（默认 plan），后端 `personalization_manager.py` 白名单 + 前端 `stores/personalization.ts` 三处（接口/默认值/sanitize）
- **前端**：输入栏 `work-mode-switcher`（InputComposer，streaming 时锁定）；plan 下权限菜单内「权限」「执行环境」两组选项禁用+锁定标记（**网络权限组保持可调**，不整体锁死菜单），slash 菜单同步禁用；`get_status` 带 `work_mode` 供多标签页同步

### 10.7) 后台命令只读沙箱修复（2026-08-13）

后台 `run_command`（`run_in_background=true`）此前在**所有环境**绕过只读权限（`background_command_manager` 固定用可写沙箱计划）。已修复：`create_background_command` 透传 `sandbox_write_access`，宿主机路径按它选只读/可写计划；docker 路径按它决定是否以非特权 uid 执行（见 §10.8）。

### 10.8) 平台级只读强制（2026-08-30）

只读权限的强制由各平台原生机制兜底；`config/limits.py` 的命令文本白名单（`_is_readonly_run_command_allowed`）只是**审批决策的启发式**，不再是安全边界（已知可绕过，如 `find . -delete`；绕过后果只是多走一次审批）。

- **docker/web 模式 = 非特权 uid 执行角色 + Landlock 进程级只读域**（`modules/docker_readonly_exec.py`）：
  - 容器主进程保持 root（可写执行不变）；`sandbox_write_access=False` 的执行通道（`terminal_ops/run.py`、`background_command_manager.py`、只读语境创建的持久终端）以 `-u 10001:10001` 运行，`DOCKER_READONLY_EXEC_UID/GID` 可覆盖
  - 第一层强制力 = 内核 DAC：工作区属主为宿主机 root，非属主无写权；600 权限文件（如 .env）不可读；逃逸需提权（setuid/内核漏洞），无 umount 类捷径
  - **第二层 = Landlock 只读域**（2026-09 新增，云端实测 kernel 6.8 / ABI V4 / Docker 28 默认 seccomp 放行）：纯 DAC 的残留漏洞是工作区内历史遗留的 world-writable（777/o+w）路径对只读 uid 仍可写；只读执行时命令再经 `modules/landlock_launcher.py`（首次执行时 docker cp 进容器并自检）进入「工作区写类操作全拒」的内核域，最终权限 = DAC ∩ Landlock，该洞封死。要点：
    - launcher 规则：handled 只含写类操作；ro 路径（工作区挂载点）不加规则（无覆盖即拒绝）；/tmp、/var/tmp、/dev/shm 显式授写权以对齐纯 DAC 现状行为（只读身份 HOME=/tmp）；读/执行不进 handled，仍由 DAC 管。注意不能写「/ 授全量 + ro 授空」的交集规则——空授权规则被内核拒绝（ENOMSG/errno 42）
    - 自检失败（内核 <5.13 / seccomp 拦截 / 容器无 python3）自动降级纯 DAC，warning 日志标注 enforcement level；`DOCKER_READONLY_LANDLOCK=0` 可整体停用
    - 语义边界：Landlock 不管 chmod/chown 等元数据修改，也不管未配置的网络——因此非特权 uid 层必须保留（root+Landlock 的进程可 chmod 放宽权限位让域外进程受益）
  - 前提：工作区属主与该 uid 不碰撞、容器未挂 docker.sock（云端已验证）；macOS Docker Desktop 双重不适用（virtiofs fakeowner 不按 uid 检查；linuxkit 内核未编译 Landlock），仅 Linux 宿主生效
  - 持久终端在 readonly/approval/auto_approval 下同以只读身份创建（`terminal_readonly_enabled` 判定，`terminal_readonly_getter` 注入）；终端里的写入会被拒，写命令走 run_command 审批通道；权限跨界切换（受限档⇄unrestricted）时销毁现有终端会话重建（`_apply_restricted_execution_mode_link` → `close_all()`）
  - Dockerfile 创建 `agent` 用户 + `/etc/gitconfig` safe.directory + 去 setuid 加固；数字 uid 不依赖镜像内用户存在，旧镜像直接受益
- **macOS 宿主机 = Seatbelt 白名单读模型**（`modules/host_sandbox_runner.py`）：
  - 只读/可写两个 profile **共用同一白名单读模型**（`_build_macos_whitelist_read_rules`），唯一区别是写权限：deny default + 系统路径白名单（`MACOS_MINIMAL_READABLE_PATHS`）+ 路径授权（writable + readable_extra）+ 工作区 + 祖先目录 literal allow（缺一个祖先进程 exec 直接 Abort trap，必须为 file-read*）+ env 注入 `GIT_CONFIG_GLOBAL=/dev/null`（可写/只读/持久终端三条 plan 均注入）
  - **deny 规则（.env 正则、~/.ssh 等）必须位于所有 allow 之后**（Seatbelt 后规则覆盖先规则）；两个 profile 均已修复旧顺序漏洞（工作区 .env 曾实际可读）
  - 可写 profile 已于 2026-08-30 白名单化（此前为全局可读，导致 unrestricted/审批批准后能读授权范围外文件）；白名单固有代价：祖先目录顶层文件名可列出（读文件内容仍被拒）
  - 持久终端 shell plan 支持 readonly 参数（`_build_macos_shell_plan` 复用 `_macos_readonly_profile_for_workspace`）：受限档终端以只读 profile 创建，unrestricted 保持可写 profile
  - 原生文件工具对齐（`file_manager/path_mixin.py`）：读 roots 与沙箱白名单同源（系统路径 + 工作区 + 授权）+ 叠加同一禁读清单——至此 host+sandbox 下全部读通道（只读/可写沙箱命令、原生 read_file）共享同一边界
- **Linux 宿主机 = bwrap**：只读为 `--ro-bind / /`（全局只读）；可写为 `--ro-bind / /` + 工作区可写 bind——**读侧仍是全局可读，尚未对齐白名单**；**2026-09-04 起官方口径：Linux 宿主机沙箱未测试、未适配、不可用**（README 与官网文档已同步声明；待有 Linux 测试环境再适配）；**Windows = WSL2 最小根文件系统（白名单）**——命名空间内只有系统目录+工作区，天然符合

## 11) 多智能体对话类型（multi-agent conversation type）

> **2026-08 重构**：已废除「多智能体模式」全局状态，多智能体是**对话的不可变属性**（`metadata.multi_agent_mode = true`，创建时确定、不可变）。URL 统一为 `/<conv_id>`（旧 `/multiagent/*` 由前端 bootstrap 重定向到裸路径）。数据目录 `~/.astrion/astrion/host/mutiagents/`（保留原拼写）。重构方案见 `docs/conversation_type_unification_plan.md`（本地设计文档，未随仓库发布），本节只列 Agent 改代码时必须知道的硬约束。

### 11.0 前端对话类型模型（重构后）

- 三个语义单一的状态，**互不复用**：
  - `currentConversationType`（`static/src/app/state.ts`）：当前已打开对话的类型（`'normal' | 'multi_agent' | null`），由 `enterConversation` 从对话 metadata 恢复（`bootstrap.ts`），空对话为 `null`。
  - `newConversationType`（同上）：空对话时输入栏待创建类型（`'agent' | 'multi_agent'`），localStorage 持久化（`agents_new_conversation_type`），由输入栏底行 `+` 右侧的类型选择器修改（`InputComposer.vue` 的 `agent-type-switcher`）。
  - `sidebarConversationType`（`stores/conversation.ts`）：侧边栏列表过滤器（`'normal' | 'multi_agent'`），localStorage 持久化（`agents_sidebar_conversation_type`），驱动列表/搜索的 `multi_agent_mode=0|1` 请求参数与侧边栏新建按钮的对话类型。
- `stores/conversation.ts` 的 `multiAgentMode` 字段保留，但语义已变为「当前对话是否多智能体」，仅由 `enterConversation`（meta 落地）与空对话 watcher（复位 false）写入——`stores/subAgent.ts` 的读取点依赖它，不得删除。
- 侧边栏类型切换 UI：分段控件统一显示在「搜索对话」下方（平铺/分组模式位置一致，搜索时隐藏）；列表区域用 `<Transition>` 推挤式同步滑动动画（无 out-in，新面板绝对定位从侧边滑入把旧面板顶出，切多智能体向左，切回反向；切换时重置列表滚动位置）。
- 发送消息创建对话时按 `newConversationType` 选择 `/api/conversations` 或 `/api/multiagent/conversations`（`message/send.ts`）；侧边栏新建按 `sidebarConversationType` 选择（`conversation/action.ts`）。

### 11.1 角色与实例

- 主智能体显示名固定为 `Team Leader`，不需要专门的预置角色文件。
- 子智能体 = `role_id`（如 `ui-operator` / `full-stack-engineer` / `code-reviewer` / `researcher`）+ 角色内编号（同一 role_id 下从 1 递增）。显示名格式 `{Role Name}_{角色内编号}`，例如 `UI Operator_1`，后缀永远带数字。
- **编号暴露原则（2026-08-26 起）**：角色内编号显示名是**唯一**对模型和用户暴露的身份；全局 `agent_id` 是纯内部实现细节（任务字典 key / task_id 生成），由系统自动分配对话级最小空闲正整数，不接受模型指定、不出现在工具参数/结果文案/前端展示中。所有寻址类工具（send_message/stop/terminate/get_sub_agent_status/sleep 的 wait_sub_agent_output、子侧 ask_other_agent）一律用显示名。
- 主→子 / 子→主 / 子→子三种通信通过工具完成，工具签名见 `modules/multi_agent/tools.py`。
- **`send_message_to_sub_agent` 与 `ask_sub_agent` 语义不同，必须保留两者**：前者插入引导消息不阻塞，后者阻塞等待一轮回答。
- 子智能体间通信要求同时向主智能体输出汇报，不允许「偷偷沟通」。

### 11.2 子智能体执行机制

- 子智能体在主进程内 `asyncio.Task`，跑在独立后台事件循环线程里（避开 Flask-SocketIO threading 冲突）。工具调用复用主进程沙箱/容器链路，网络调用走 `utils.api_client.APIClient`。
- **模型请求重试（2026-08-26 起）**：`_run_loop` 对 `_call_model` 包重试循环，与主智能体 `run_streaming_attempts` 同构——最多 5 次尝试（`_SUB_AGENT_MAX_API_RETRIES=4`）、间隔 10s（`_SUB_AGENT_RETRY_DELAY_SECONDS`，用 `asyncio.sleep` 分段等待并响应软停止/取消）。重试条件：**仅当零接收**（未收到任何文本/思考/工具调用）才重试；已开始收到内容后断流（`SubAgentModelCallError.received_any=True`）直接失败。失败终态分模式：多智能体模式下 5 次全失败 → 转为 idle + `_forward_output_to_master` 报错（等 Team Leader 重新下达指令），输出期间断开 → 直接 failed 并同步向主智能体报错；传统模式一律 `_write_failure`。
- **工具「正在调用」进度事件（2026-08-26 起）**：`_call_model` 在工具名+id 首个流式 chunk 到达时即 emit `status="calling"` 进度事件（与后续 running/completed 共用同一 tool_call id），前端按 id 原地更新条目；`RunnerDetailPanel.vue` / `SubAgentActivityDialog.vue` 的 normalizeStatus 识别 `calling`（显示 spinner + 「调用中」），并支持同一 id 历史条目跨组原地更新（多工具 calling 事件交错场景）。
- 子智能体在多智能体模式下：
  - `create_sub_agent` 强制 `run_in_background=False`，不触发 `sub_agent_waiting` 事件，不阻塞前端输入区。
  - 子智能体自然的 assistant 输出结束（无 tool_calls）即本轮任务结束，进入 `idle`，上下文保留，不算 failed。
  - 不能把 `output.success == null` 直接判为 `failed`—`_check_task_status()` 对多智能体任务 `status=running/idle` 视为正常态（见 `modules/sub_agent/state.py`）。
  - idle 等待必须用 `asyncio.Event`，不能用 `threading.Event`，跨线程唤醒用 `call_soon_threadsafe`。
  - 任务记录里 `multi_agent_mode` 字段必须显式写入，缺失会导致传统后台通知池把多智能体任务当后台任务处理。`reconcile_task_states()` 已对旧任务补回。

### 11.3 消息池与派发链路（重点）

**输出端 ↔ 接收端分离**：
- 输出端（`SubAgentTask._forward_output_to_master`）：子智能体每次 assistant 文本输出都封为标准格式消息并 `push_master_message` 到会话的 `MultiAgentState.pending_master_messages`，不再做事。
- 接收端（`MultiAgentState` + dispatch）：根据主智能体当前状态选择插入方式：
  - **情况1（主运行中）**：在 `execute_tool_calls` 末尾 `process_multi_agent_master_messages(inline=True, after_tool_call_id=...)` 插入到下一轮模型 messages 列表里（详见 `server/chat_flow_tool_loop.py:1326`）。
  - **情况2（主空闲）**：`poll_multi_agent_notifications` spawn 出后台 poll，主对话空闲且 pool 有消息就 drain，调 `_dispatch_multi_agent_idle_messages` 创建 `task_type="notice"` 的后续 task，触发新一轮工作。
  - **情况3（主最后一轮无工具调用）**：主循环 `if not tool_calls:` 分支调 `process_multi_agent_master_messages(inline=False)` 后 `continue` 继续迭代，Team Leader 在同 task 续跑处理子智能体输出。（实现上与情况2同路径，即情况3通过后续 task 完成情况2）。

**情况2 硬约束（调试踩过坑）**：
  1. **Pool 优先，不能等所有 running 退出再 drain**：`ask_master` await 期间 status=running，但子智能体本身不会产生新输出，只有等主对话回答才能解套。pool 有消息就立即 drain，不管 running 状态。「对话处于运行状态」只决定前端显示态，不阻塞 pool 消费。
  2. **`_dispatch_multi_agent_idle_messages` 持久化不要重复**：前置 N-1 条调 `inject_multi_agent_master_message` 绑定持久化。最后一条只 emit 给在线客户端、不持久化、在后续 `task_manager.create_chat_task` 走的 `handle_task_with_sender` 里才 `add_conversation`。否则历史里会有两条相同 user 消息，前端刷新会被渲染两遍（一条多智能体渲染、一条通知渲染）。
  3. **Metadata `visibility` 必须显式为 `"chat"`**：`_user_message_ui_defaults("sub_agent")` 默认给 `{visibility: "compact"}`；在 `_dispatch_multi_agent_idle_messages` 构造 `auto_user_message_payload`时，如果有`**ui_defaults`，必须在它之**后**写 `"visibility": "chat"`（dict 字面量中后出现的key 胜出）。同样，在`handle_task_with_sender`处理多智能体消息时，`user_message_metadata.update(multi_agent_meta)`之后要显式`user_message_metadata["visibility"] = "chat"`。
  4. **必须传 `auto_message_type`**：前端 `isMultiAgentMessage()` 只看 `auto_message_type.startsWith('multi_agent_')`。`auto_user_message_payload` 和 `preceding_user_notices[i].payload` 都必须显式写 `auto_message_type`，字段不能用空值，否则前端 fallback 起通知渲染。

### 11.4 通知池轮询器完全独立

- 传统后台子智能体 / 后台 `run_command` 通知：`poll_completion_notifications`，在 `handle_task_with_sender` 结尾的 `needs_completion_poll` 。
- 多智能体通知：`poll_multi_agent_notifications`，在 `needs_ma_poll`。两者完全独立，避免 task_manager 单工作区互斥竞争。
- `task_complete` 事件中：
  - `has_running_sub_agents` 只算传统后台任务，不算多智能体。
  - `has_running_multi_agent` 多智能体专用字段，同时包含 _running_ 实例和 pending master 消息（详见 `server/chat_flow_task_main.py:2324`）。前端通过该字段走独立的 `startMultiAgentTaskProbe`（不启动 `sub_agent_waiting`）。

### 11.5 渲染与前端约定

- 多智能体消息渲染条件是**两个独立判断**：
  - `isMultiAgentMessage()`：看 `auto_message_type.startsWith('multi_agent_')`。
  - `getMessageVisibility()`：看 `metadata.visibility`。两者都必须正确，消息才能走多智能体渲染分支。任一错都会 fallback 到通知渲染。
- 多智能体消息不显示新的 assistant 回复头部（Astrion/工作时间），即 `metadata.starts_work=false`。但前端通过 `has_running_multi_agent` 在 `task_complete` 中单独处理恢复轮询。
- 子智能体进度弹窗：输出与工具按真实时间线混排，默认 3 行，过长用省略号或内部滚动；颜色走 `--text-primary` 等语义 token。
- 全局工具规范统一适用于多智能体权限：UI 不能引入 `compact` 以外的 fallback 样式习惯复制到多智能体渲染。

### 11.6 工具结果格式化与前端渲染位置

新增多智能体工具时，必须同步补齐「后端结果格式化」和「前端结构化渲染」，否则前端会 fallback 到原始 JSON 或空白。

**后端 formatter（主智能体工具）**
- 实现位置：`utils/tool_result_formatter/agent_context.py`
- 注册位置：`utils/tool_result_formatter/dispatch.py` 的 `TOOL_FORMATTERS`
- 处理入口：所有主智能体工具执行结果，最终由 `format_tool_result_for_context()` 转换为自然语言摘要，写入对话历史。

**后端 formatter（子智能体通信工具）**
- 实现位置：`modules/sub_agent/toolkit.py` 的 `_format_tool_result()`
- 覆盖工具：`ask_master` / `ask_other_agent` / `answer_other_agent` / `list_active_sub_agents`
- 处理入口：子智能体 tool call 结果回填到子对话上下文前。

**前端 renderer（主路径）**
- 实现位置：`static/src/components/chat/actions/toolRenderers.ts` 的 `renderEnhancedToolResult()`
- 调用方：
  - `static/src/components/chat/MinimalBlocks.vue`
  - `static/src/components/chat/StackedBlocks.vue`
- 注意：这两个视图已移除 `enhanced_tool_display` 开关判断，**所有工具块都强制走结构化渲染**，不再显示原始 JSON。

**前端 renderer（备用路径）**
- 实现位置：`static/src/components/chat/actions/ToolAction.vue` 的 `renderToolResult()`
- 调用方：`static/src/components/chat/ChatArea.vue`
- 同样已移除原始 JSON fallback，仅作为 ChatArea 独立工具块渲染的备用。

**新增工具 checklist**
1. `modules/multi_agent/tools.py` 定义工具签名。
2. `core/main_terminal_parts/tools_execution.py` 实现工具 handler 并返回标准 dict（`success` + 业务字段 + 可选 `error`）。
3. `utils/tool_result_formatter/agent_context.py` 新增 `_format_<tool_name>()`，`dispatch.py` 注册。
4. `static/src/components/chat/actions/toolRenderers.ts` 新增对应 `render<PascalCaseToolName>()` 并在 `renderEnhancedToolResult()` 中分发。
5. 如果是子智能体通信工具，同步在 `modules/sub_agent/toolkit.py` 的 `_format_tool_result()` 中补 formatter。

### 11.7 调试机制

- 调试日志统一走 `modules/multi_agent/debug_logger.py` 的 `ma_debug()` 函数，写入 `~/.astrion/astrion/host/logs/multi_agent_loop.log`。
- 关键状态转换点必须打 `ma_debug`，便于复现 bug：
  - `handle_task_with_sender_start`（含 `pending_master_messages_count`）
  - `state_push_master_message` （交出 `queue_len_before`）
  - `poll_ma_tick` （每 0.5s 一次，包含 `instance_count` / `statuses` / `pending_count` / `main_active` 的完整状态快照）
  - `dispatch_ma_idle_enter` / `dispatch_ma_idle_before_create_task` / `dispatch_ma_idle_task_created` / `dispatch_ma_idle_sender_user_message` / `dispatch_ma_idle_exit_ok`
  - `create_chat_task` 异常 / `provide_answer` 跨循环回写
  - `runtime_injected` 字段标记（metadata 里能区分多智能体 inline / idle / ask插入路径）

### 11.8 已知坑

- 多智能体模式下根本不 `emit` `sub_agent_waiting` 事件，避免前端进入「等待后台子智能体」的输入区阻塞态。
- `_announced_sub_agent_tasks` / `notified` 等标记仅适用于传统后台子智能体任务，多智能体任务不走这条通知路径。
- 多智能体任务的 `output.json` 中 `status` 在子智能体自然进入 idle 时会被写为 `"idle"`；在 `_check_task_status` 中由 `_check_task_status_keep_alive` 跳过防止错误判定为 `failed`。
- 传统后台通知池 `_collect_pending_completion_notices` 在 `task.get("multi_agent_mode")` 为真时跳过该 task；多智能体派出走独立的 `poll_multi_agent_notifications` 路径，不当混用。
- `_has_pending_completion_work` 主动排除 `multi_agent_mode=True` 的任务；两者永远独立。

### 11.9 对话切换与状态保留

- 切换会话不清理 `_running_tasks` 和 `_sub_agent_instances`。`SubAgentManager` 的全局 tasks 字典按 `task_id` 保持，多智能体状态由 `conversation_id` 在 `get_multi_agent_state` 中查。
- 子智能体对话存在 `~/.astrion/astrion/host/host/data/sub_agents/`。重启后走 `manager.restore_sub_agent` 恢复实例引用。
- **`MultiAgentState` 是进程级全局单例**（2026-07 重构）：存放在 `modules/multi_agent/state.py` 的 `GLOBAL_MULTI_AGENT_STATES`（key=`conversation_id`），所有 `SubAgentManager` 实例共享；`manager.multi_agent_states` 只是该全局 dict 的引用。此前它是 manager 实例属性，对话级 terminal 缓存重建会产生多个 manager，各自的 `_load_state` 都从磁盘快照 `from_snapshot` 出一份独立副本，导致 terminate 只标记其中一份、前端轮询落到其他副本显示陈旧 idle。`get_or_create` / `drop` / `_load_state` restore 均通过 `GLOBAL_MULTI_AGENT_STATES_LOCK`（RLock）互斥。
- **进程重启后的状态校准**（2026-07 新增）：`_load_state` 恢复 ma 快照后，会用任务记录（持久真相）校准实例终态——任务记录是 terminated/终态而快照里还是 idle 的，一律校准为终态；同处还有存量 `_None` 后缀显示名的自愈迁移（按「对话×角色×创建时间」重编号）。
- **显示名编号语义**：显示名后缀（如 `Full-Stack Engineer_1`）是**角色内编号**，创建路径 `tools_execution.py` 中通过 `peek_agent_id_for_role` + `commit_agent_id_for_role` 两步走——先 peek 构造显示名，创建成功后才提交计数器，**失败不消耗编号**（避免跳号）；全局 `agent_id` 由 `manager.next_free_agent_id()` 自动分配（对话级最小空闲正整数），两者是两套独立命名空间，不得混用。

---

注：本节按「现有架构 + 多智能体分支」方案描述；如与代码冲突，以代码为准并同步修订本节。

---

## 12) 对话级主任务门闸与单写者不变量（2026-08-12）

> 事故背景：一个对话并发运行了两个主聊天任务（socketio 用户任务 + 完成通知轮询器派发的通知任务），交叉写入共享 `conversation_history`，产生 `assistant→assistant→tool→tool` 乱序段，最终 API 400 `tool_call_id is not found`、通知永久丢失。本节机制即为修复该事故引入。

### 12.1 单写者不变量（核心约束）

**一个 WebTerminal（≈ 一个打开的对话）同一时刻只允许存在一个主聊天任务。** 主任务包括：用户消息任务、后台完成通知派发任务、多智能体 idle 派发任务等一切会向 `conversation_history` 追加消息并请求模型的执行体。宁可拒绝/推迟新任务，也绝不并发写入。

### 12.2 门闸本体

- 实现：`server/main_task_gate.py`（进程内字典，key=terminal_id，value=token）。
- 获取/认领：`acquire_or_claim_main_task_gate(terminal_id, owner_desc)`——门闸空闲或持有者是同一任务时返回 token，否则返回 None。
- 释放：`release_main_task_gate(terminal_id, token)`，token 不匹配则拒绝（防止错误释放他人门闸）。

### 12.3 唯一入口与 token 移交

- **唯一入口**：`process_message_task`（`server/chat_flow.py`）是所有主任务的门闸入口——进入即获取/认领门闸，拿不到则向用户发 error 并返回，绝不强行执行；`finally` 中释放。
- **通知链移交**：完成通知轮询器在派发前先预占门闸（`server/chat_flow_task_main.py`），token 经 `session_data["main_task_gate_token"]` 移交 `_run_chat_task` → `run_chat_task_sync` → `process_message_task`（持 token 认领，不重复获取）；派发失败时释放门闸并回滚已打的通知标记（`_rollback_completion_notice_marks`）。
- **兜底释放**：`_run_chat_task`（`server/tasks/models.py`）外层 `finally` 兜底释放，防止异常路径门闸泄漏。

### 12.4 改代码注意事项（硬性）

1. **新增任何主任务入口必须走门闸**：不要绕过 `process_message_task` 直接驱动一轮模型对话；多智能体 idle 派发（task_type="notice"）目前依赖 `_multi_agent_main_task_active` 标志，后续应统一纳管。
2. **不要在 return 分支手写 `_tool_loop_active` 恢复**：`execute_tool_calls`（`server/chat_flow_tool_loop.py`）已改为守护包装（try/finally 复位，内层 `_execute_tool_calls_impl`），新增提前返回路径无需也不应手动操作该标志——并发交错「存旧值→置True→恢复旧值」正是此前标志卡死的原因。
3. **不要依赖 build_messages 防御层掩盖并发问题**：`core/main_terminal_parts/context/messages.py` 的孤儿 tool 消息剥离只是「坏数据不再 400」的止血层，乱序段本身意味着历史已被污染；发现剥离 warning 日志应按事故排查，而不是视为正常。
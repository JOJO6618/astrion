# Repository Guidelines (Code-Verified)

> 最后核对代码：2026-11-19 ｜ 最近压缩：2026-11-19（第 2 次：64,012 → 46,793 字符，§28 下沉）
> **本文只放硬约束与「改代码必须知道」**；机制细节、历史沿革、逐条清单、完整命令下沉到 `docs/`（主体 `docs/agents_details/`，索引 `docs/README.md`）。
> **细则与本文同级**：`docs/agents_details/` 每一条都是**硬约束**，不是可选背景；改某机制前必须先读对应细节文档（见文档地图）。冲突时以细则为准，并回来同步本文。
> **体积硬约束（强制）**
> ① 每次改完本文**必须统计字符数**：`python3 -c "print(len(open('AGENTS.md',encoding='utf-8').read()))"`
> ② **> 50,000 字符必须拆**：整节下沉为 `docs/` 文档，正文只留「不变式 + 指针」，拆完再统计一次确认降下来了。
> ③ **拆 ≠ 改表述**：把多条并成一句、删约束、合成超长段落，字符数没降都不算拆；**只认字符数，不看行数**。
> 与代码冲突时一律以代码为准，并回来同步本文与对应细节文档。
> **章节号边界**：主节号 §0–§28 与压缩前一一对应（项目记忆与既有文档按号引用），**不要重排、不要复用已废弃的号**；新机制顺延编号，子标题细节进细节文档。
> `doc/` 不追踪；旧 `docs/`、`doc/` 已归档到未追踪的 `_experiments/docs_archive/`，历史引用保留原路径写法（实际文件加该前缀）。
> 和用户交流默认使用中文。

## 文档地图（改这些领域前必须先读）

`AD/` = `docs/agents_details/`；未标目录的在 `docs/` 根。

| 领域（触发） | 文档 |
|------|------|
| 项目结构、加改目录与模块 | `AD/project_structure.md` |
| 运行态数据目录、路径变量、日志轮转 | `AD/runtime_data_paths.md` |
| 启动 / 构建 / 打包、解释器探测 | `AD/commands.md` |
| 测试写法与回归执行 | `AD/testing.md` |
| 公共执行层（文件/进程/终端/远程边界） | `execution_contract.md` |
| 私有项目常驻记忆（MEMORY.md） | `workspace_memory.md` |
| Git worktree、执行根、主子环境同步 | `git_worktree.md` |
| 前端设计（唯一规范） | 根目录 `DESIGN.md` |
| 定时任务 | `scheduled_tasks.md` |
| Chat 模式与消息树 | `chat_mode.md` |
| 对话导出 / 导入 / 迁移 | `conversation_transfer.md` |
| 权限模式、路径授权、平台沙箱 | `AD/permission_and_sandbox.md` |
| 运行模式 plan/ask/execute | `AD/work_mode.md` |
| 目标模式（goal） | `AD/goal_mode.md` |
| 统一子智能体 | `AD/multi_agent.md` |
| 主任务门闸与单写者 | `AD/main_task_gate.md` |
| 工具动态加载 | `AD/tool_loading.md` |
| MCP 运行态与浏览器隔离 | `AD/mcp_runtime_scope.md` |
| 网页提取与搜索 | `AD/web_extract_and_search.md` |
| 设置页与提供商体系 | `AD/settings_and_providers.md` |
| 思考内容摘要 | `thinking_summaries.md` |
| 预览面板 | `AD/preview_panel.md` |
| 代码索引 codegraph | `AD/codegraph.md` |
| 子智能体固定权限 / 单次完全访问 / 审批面板 | `AD/sub_agent_access.md` |
| 运行中对话显示快照 | `AD/display_snapshot.md` |
| 桌面标签导航 / 多视图 | `desktop_navigation.md`、`desktop_multi_view.md` |
| 桌面内置浏览器 | `native_browser.md` |
| 桌面更新策略 | `desktop_update_policy.md` |
| Linux host 沙箱 | `AD/linux_host_sandbox.md` |
| Host 网页密码 | `AD/host_password.md` |
| 主 / 子 API 花费 | `AD/api_costs.md` |
| CLI 交互与分层 | `AD/cli_workflow.md` |
| 项目 Git 记录与评审 | `project_git.md` |
| CrossPet 本机接入 | `crosspet_integration.md` |
| 用量统计页 | `AD/usage_stats.md` |
| 存储空间统计与清理 | `AD/storage_stats.md` |
| 技能名称注入与 search_skills 检索 | `AD/skills_search.md` |
| 主 Agent 系统提示词快照 | `system_prompt_snapshot.md` |
| Host 主仓与 Docker 拆分边界 | `docker_fork_split_plan.md` |
| 自动审核策略 | `auto_approval_review.md` |
| 提交共同作者尾注 | `coauthor_trailer.md` |

## 0) 项目名称

本项目（仓库 `agents`）的**暂定**产品名为 **Astrion**：`Astr-`（希腊语 *astron*，星星/天体）+ `-ion`（粒子/状态后缀）。中文参考「阿斯特里恩」，简称可考虑 **星子**。

- **状态**：**暂定名**，变更时同步 `AGENTS.md` 与本地 `claude.md`（未随仓库发布）。构词细节与寓意见压缩前的 git 历史版本。

## 1) 当前项目结构（按代码现状）

- **入口**：Host Web 使用 `./start.sh` 或 `start.bat`；Web、CLI headless 和 Electron 都使用 `backend-rust/src/main.rs`。
- **后端**：`backend-rust/src/runtime/`、`process_runtime/`、`tools/` 维护任务与执行边界。REST 任务轮询为唯一实时通道。下文旧 Python 文件指针可查迁移快照 `fb0a27032`。已绑定后端失败不得回退控制端（`docs/execution_contract.md`）。
- **产品资源与维护工具**：`config/` 提供配置种子。宿主机技能和开发测试可以使用 Python，官网维护脚本独立部署。
- **前端**：`static/src/`（Vue 3 + TS）；`cli/src/`（opentui + React 19，旧 Ink 存档 `cli/legacy-ink-src/`）
- **子项目**：`desktop-electron/`（macOS + Windows 共用 Electron）、`desktop/`（旧 Tauri 壳，仅供新壳复用 Windows staging/图标/原生模块）、`easyagent/`（已弃用）、`_experiments/`（不入 git，见 §7）
- 完整目录职责与监控动画核心文件见 `AD/project_structure.md`

### 1.5) 运行态数据目录与路径变量

运行态数据根默认为 `~/.astrion/astrion/`，可用 `ASTRION_DATA_ROOT` 覆盖。数据和日志沿用 `host/data/` 与 `host/logs/`。具体 `DATA_DIR` 与 `LOGS_DIR` 覆盖优先。桌面壳默认 `~/.astrion/astrion-desktop`（Windows `%USERPROFILE%\.astrion\astrion-desktop`），调试覆盖用 `ASTRION_DESKTOP_DATA_ROOT`，壳设置仍在 `~/.astrion/rundata_settings.json`。

- **`config/*.json` 分两类**：程序能力类（`docker_risk_markers`/`skill_hints`/`command_blocking_recommended`）锚定源码树；部署级配置（`custom_models`/`host_workspaces`/`auto_approval`/`goal_review`/`host_sandbox_policy`）外置到 `DEPLOY_CONFIG_DIR`（走 `config.resolve_deploy_config`）。
- 主仓从 Host 目录解析注册工作区。旧 Docker/Web 数据沿用原部署。配置要求旧模式或 `LINUX_SAFETY` 时，主仓在初始化运行数据前退出。API dump 默认关闭（`AGENT_API_DUMP_ENABLED`），轮转见 `utils/log_rotation.py`。
- **私有项目常驻记忆**：存 `<DATA_DIR>/memory/<目录名>-<路径摘要>/MEMORY.md`，只在新建对话与压缩后全文加载。继续及恢复沿用冻结快照，免预读只适用于已注入文件，readonly/plan 守门继续生效。
- **索引式项目记忆**：专题存入同目录的 `topics/`。启动时提示迁移，验证源和目标后保留说明。老对话冻结提示词不回写。完整契约见 `docs/workspace_memory.md`。

> 路径解析细节、个人指令拦截 `command_blocking` 语义见 `AD/runtime_data_paths.md`。

## 2) 启动 / 构建 / 打包命令（已按代码核对）

| 用途 | 命令 |
|------|------|
| 依赖 | `npm install`、Rust 工具链 |
| Host Web（Rust） | `./start.sh` 或 `start.bat`（可加 `--path ./project --port 8091 --thinking-mode`） |
| Rust 后端构建 | `cargo build --locked --release --manifest-path backend-rust/Cargo.toml`（CLI 默认自启 Rust headless） |
| 前端构建 / lint | `npm run build --silent 2>&1 \| tail -n 5` ／ `npm run lint` |
| CLI 开发 | `bun cli/src/main.tsx`（bun ≥1.3）；类型检查 `cd cli && ./node_modules/.bin/tsc --noEmit` |
| 桌面壳（macOS） | `cd desktop-electron && npm install && npm run dev`；发布 `bash desktop-electron/scripts/release_mac.sh [版本]` |
| 桌面壳（Windows） | Windows 原生环境 `node desktop-electron/scripts/build_windows.mjs`（仅目录包加 `--dir`，默认不发布） |

**硬约束**：

1. **绝不能 full 与 headless 双进程并存**——RuntimeService 单例、门闸、审批 Map 都是进程内单例，两者端口/数据目录相同，只是挂载路由面不同。
2. Desktop 依赖安装需 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`；壳加载后端 serve 的 `static/dist`，**改前端必须先 `npm run build`**。
3. Windows 必须在 Win 机构建，版本/说明在 `desktop-electron/windows-release.json`（macOS 用同目录 `package.json`/`DESKTOP_CHANGELOG.md`，独立维护）。**Windows Electron 0.10.0 已于 2026-11-14 正式发布**，迁移测试包 0.10.0–0.10.3 从未发布，**不能按测试包编号推断正式版**；跨壳更新仍须原 minisign 签名，NSIS 用 `latest.yml`，**未授权不得发布**。
4. 构建/安装/lint 因沙箱权限失败按 §5「构建沙箱已知问题」处理，**禁止改构建配置绕过**。
5. **前端产物带内容哈希**：`vite.config.ts` 输出 `assets/[name]-[hash:8].(js|css|ext)`，`static/*.html` 由 `server/entry_assets.py` 按 `static/dist/.vite/manifest.json` 注入。**新增 HTML 出口必须用 `serve_entry_html`**；HTML 响应一律 `no-store`；装配样式由 `scripts/generate_entry_styles.mjs` 生成；桌面壳 quick 窗口各自读同一 manifest，改哈希规则必须同步。部署见 `test/2026-10-15_前端产物hash化/README.md`。

> 完整形态说明、headless 额外路由、解释器探测顺序、打包链路见 `AD/commands.md`。

## 3) 测试现状（不要再写过时命令）

- **目录规范（强制）**：新增测试一律在 `test/` 下新建 `YYYY-MM-DD_功能/`；**禁止**放 `test/` 根或项目根。同日同功能的独立批次用 `_02`/`_03`，不得覆盖已有目录；辅助文件放对应目录内。
- 无法确认日期的存量测试在 `test/历史测试_日期不明/`，**不按 mtime 猜日期**；移动时修正相对路径、辅助引用与运行命令。
- **执行方式**：pytest 可按分类目录收集；unittest **必须**用具体分类目录作 `discover` 起点，不能依赖 `discover('test')` 递归（目录不是包）。仓库没有 `pytest.ini`/`pyproject.toml`/`tox.ini`，pytest 只作便捷运行器；标准 Windows Python 上 `python -m unittest test.xxx` 会被 stdlib 的 `test` 包遮蔽，需用 `discover` 形式。
- 改 `server/chat/`、`server/status/`、`server/tasks/` 等后端接口时，至少补 `python3 -m py_compile` 与相关回归目录。

> 具体冒烟用例、CLI 最小验证见 `AD/testing.md`。

## 4) 代码修改约定（实用版）

- **主 Agent 系统提示词（强制）**：固定规则集中 `prompts/main_agent/static.txt`，动态初始化资料各自独立模板；完整初始 system（含提供商基础指令与桥接）一次性保存为 `metadata.frozen_system_prompt`，**两次压缩之间只能读取、禁止重组**。旧对话恢复执行前按原顺序合并旧冻结字段，压缩后才用新布局；普通查询不迁移回写。工作流状态只走既有工具结果，运行期间通知保持原样；不处理运行中切模型。改相关机制前必读 `docs/system_prompt_snapshot.md`。
- **Chat / Agent 尽可能共用代码（强制）**：禁止因模式不同维护两套通用实现；差异通过模式配置、能力边界与必要专属适配表达。共有 UI 用同一组件与样式，共有智能体／工具循环用同一套逻辑，**通用修复必须同时作用于两种模式**。边界见 `docs/chat_mode.md`。
- **根治优先**：能根治的先告知方案与影响范围再执行；避免打补丁式临时修改。
- **精确读取**：**禁止整文件通读**，先 grep／符号查找锁定行段再精确抽取。**编辑**优先 `write_file`/`edit_file`；原生工具不可用时才用脚本，并在结果中说明。
- **改动优先级**：后端先进 `modules/`、`server/` 对应模块，最后动入口；前端按 `static/src` 分层（`app/`/`stores/`/`components/`/`composables/`）；CLI 按 `cli/src/` 分层（入口编排／主界面／Gateway／会话运行时／菜单状态机／渲染分发／通用件／数据层／i18n），新交互先定归属层。
- 涉及 monitor 动画／事件联动时，同步检查 `static/src/components/chat/monitor/MonitorDirector.ts` 与 `static/src/stores/monitor.ts`。
- **命令工具契约**：`run_command` **不向模型暴露 `working_dir`**（前台／后台都不透传），命令默认在执行根（工作区根或当前工作树）运行，子智能体同步。内部接口（`execution_plane`／`terminal_ops`／`background_command_manager`）保留该参数仅供工作树、审核智能体内部调用，**不得加回模型可见参数**。提示词统一要求「不要写 `cd <绝对路径> &&` 前缀」。契约见 `docs/execution_contract.md`。

## 5) 风格与质量

### 文件长度与拆分

- **单文件最好不超过 1000 行**，但行数不是硬指标——拆不拆看**可维护性**（职责单一、边界清晰、拆后更易读易测）。**不要为了拆分而拆分**；Vue SFC 拆 `.vue` 风险高，优先抽 composable 与子组件。

### 语言与验证

- Python：4 空格、`snake_case`、`PascalCase` 类，新增公共函数尽量补 type hints；Vue/TS 与 CLI 保持现有风格，不做无关清洗。日志优先复用现有 logger/路径，不引入大量临时 `print`。
- 提交前至少做与改动相关的最小验证。**视觉验证默认由用户完成**：需要视觉确认的修改（尤其动画/过渡）在构建/lint 通过后交用户查看，**不要**用截图或浏览器自动化代替用户验收。
- 根目录前端构建默认 `npm run build --silent 2>&1 | tail -n 5`。

### 构建沙箱已知问题

vite 构建的 PostCSS 配置搜索会向上冒泡读工作区外文件，宿主机沙箱内被 EPERM 拒绝（tsc/stylelint 前置正常）。**确认属于该问题后禁止改构建配置规避**：对本次命令申请完全访问（`request_full_access=true` + `full_access_reason`，直接发起），或请用户加路径授权（仅可读）；都不可用再讨论执行环境。**禁止以任何未经授权的方式规避沙箱限制。**

**通用前置**（所有构建/安装/lint）：因沙箱权限失败（`EPERM`／`Operation not permitted`／拒绝写入）时，**先核对命令和路径并向用户说明原因**，再走上面的申请或授权。

### CLI 当前交互约束

- **全部走 Gateway host Bearer 通道**（token 在 `host/data/host_api_token`，工作区经 `X-Astrion-Workspace-Id` 绑定）；**禁止** Web 会话通道。
- `astrion` 启动 cwd = 工作区，运行内不可切换；未注册工作区先提示并询问创建。自启动只在探测无服务时 spawn，**绝不 kill/重启已在运行的进程**；401 自动重读 token 重试一次。
- 审批面板收到新 ID 的 `tool_approval_required` 即自动弹出，等服务端终态后清理；i18n 读 OS 语言、启动时定死；文案**禁止**按字符长度硬编码布局；Enter=发送、Shift+Enter=换行。
- 未获用户要求时不要跑交互式 TUI 压测或长时间模拟输入。详见 `AD/cli_workflow.md`。

## 5.5) 前端设计规范（强制）

**唯一规范是根目录 [DESIGN.md](DESIGN.md)**：设计语言、硬约束、字体字号、token、组件范式与出现消失动画只在那里维护。改/评审任何 UI 前必须阅读；新的设计决定直接修订 DESIGN.md，**不在本文另写平行规则**。

- 适用 `static/src`（`cli/src` 视觉类比处同样适用）；存量违规在相关改动的最小范围内顺手修正。
- 硬约束速查（细则见 DESIGN.md §3）：禁光晕；禁原生外观控件；禁圆角套娃；图标热区统一并按视觉重心对齐；颜色只走三主题语义 token；带文字容器固定高度；最大尺寸 + 内层滚动；实体面板不透明；主题变体与基础定义同文件、禁 `!important`；大面积色块禁纯黑／近黑。
- 配色基建：token 在 `styles/base/_tokens.scss`，切换见 `utils/theme.ts`（**唯一 DOM 应用入口**）；`.stylelintrc.cjs` 防回退，`build` 含 stylelint；`show_ui`／`show_html` 内容配色遵循各自提示词。

## 5.6) 工具结果显示规范（强制）

适用所有 `tool-result-meta`／`tool-result-content` 渲染结果；视觉层面同时遵循 DESIGN.md。

1. **meta 只放参数**（状态、ID、路径、任务描述、超时），**禁止**放执行结果、统计摘要、输出内容；**content 只放结果**，**禁止**重复参数。
2. **统一拼装件**：渲染器一律用 `components/chat/actions/toolResultHtml.ts`（`meta`/`param`/`summary`/`box`/`preBox`…），不手写 `<div><strong>`；长参数由 `param` 自动进限高小块。失败原因由 `renderEnhancedToolResult` 统一追加 `.tool-result-error`；普通成功/失败不加「状态」行，只有待审批／等待回答／已拒绝／已取消用 `pendingStatus`。
3. **单层滚动 + 全平铺**：只有结果框（`.tool-result-content.scrollable`／`.tool-result-diff.scrollable`）与长参数小块可滚（`overscroll-behavior: contain`）；外层 `.collapsible-content` 只裁切不滚动、展开高度不封顶。参数、结果、错误／提醒全部无底色／描边／圆角／内边距，从同一左边缘起排，参数与结果之间只有一条分隔线；错误只用文字颜色。**新增结果元素不得自带内边距框**；极简与普通模式同规则。

## 5.7) 调试输出规范（强制）

- **前端**：同一次调试的临时 `console.log` **必须用同一个前缀**（如 `[route-debug]`，优先对齐用户可见现象），多模块靠字段区分；只在关键路径打、禁止刷屏，大量数据用 `console.group`/`console.table`；**定位后立即删除或注释**。
- **后端**：**禁止 `print` 到终端**，必须写文件并复用现有 logger（`utils/logger.py`、`modules/multi_agent/debug_logger.py`），落 `~/.astrion/astrion/<mode>/logs/`；多智能体、子智能体、任务轮询等**关键状态转换必打**结构化日志。

## 5.8) 前端多语言（i18n）文案规范（强制）

> 详细规范（已归档）：`_experiments/docs_archive/doc/frontend/i18n_spec.md`，约束级别与 §5.5 同级。

- **引擎**：vue-i18n v10（Composition）；文案唯一定义在 `static/src/locales/{zh-CN,en-US}/<namespace>.ts`，**中文为源语言**。
- **使用**：模板 `$t('ns.key')`（已开 `globalInjection`，模板侧全局注入且响应式）；SFC script／纯 TS 用 `import { t } from '@/locales'`；公共词只放 `common`。
- **key 奇偶强校验**：en-US 聚合器用 `DeepString<typeof zhCN>` 约束，缺/多 key 直接 tsc 报错，新命名空间两处同步注册。**防回退栏杆**：`npm run lint` 先跑 `scripts/i18n_audit.mjs`（单跑 `lint:text`），存量列在 `scripts/i18n_baseline.txt`，**迁移完一个删一行**。
- **切换**：个人空间 → 外观 → 界面语言，默认 zh-CN，key `agents_ui_locale`。后端下发文字不做多语言；CLI 独立读 OS 语言。

## 6) Git 工作流（开发 + Review）

- 默认直接在 `main` 开发，每个功能建议一个清晰 commit；Conventional Commits（`feat:` `fix:` `refactor:` `chore:` `docs:` `test:` `perf:`）；个人项目，不设 PR/Review 流程。
- **AI 每一步都要向用户说明在做什么**：`git status`（无关改动提醒 commit/discard，有关则 `git stash push -m "WIP: <描述>"`）→ 确认在 main 并 `git pull` → 开发并提交 → `git push`。
- **禁止 AI 在未经用户确认时执行 `git commit` 或 `git push`**；两者授权**各自一次性**，「允许 commit」不等于允许 push。必须先在本地验证修改可用再建议提交。
- 汇报按改动规模：大修改说明核心变更点与验证结果，小修改只说核心变更点；不要求每次出 diff，用户要求 Review 时用 `git diff` / `git diff HEAD~1..HEAD`。
- **提交必须带共同作者尾注**：`Co-authored-by: {智能体名} powered by {具体模型名} <{邮箱}>`（模型名取提交时实际模型）；规范见 `docs/coauthor_trailer.md`，commit 时写对，不要等 push 再补。

## 7) 安全与仓库卫生

- **严禁提交真实密钥**（`.env`、token、cookie、用户隐私）；分享前脱敏。
- 运行态数据默认在 `~/.astrion/astrion/<mode>/`，不在源码树内；`.gitignore` 仍忽略源码树内 `logs/`、`data/`、`users/`、`api/`、`project/`。
- **`_experiments/`**：归档要保留但不属主线的零散文件（调试记录、旧变更日志、测试脚本等），**不纳入 git**，不要散落根目录。
- **不要提交本地构建产物**（`static/dist/`、`node_modules/`）。**文档目录**：`docs/` 纳入版本控制（AGENTS.md 相关文档与开发文档，含 `agents_details/`）；`doc/` 不追踪，旧内容在 `_experiments/docs_archive/`（见 `docs/README.md`）。

## 8) Host 主仓范围

主仓维护 Host 浏览器界面、本机沙箱、CLI/headless、macOS 与 Windows 桌面端及 Quick Chat。普通账号、API 用户、容器执行和 Android 发布归独立 Docker 版的拆分范围。Host 继续通过 `/api/v1/models` 提供模型目录，网页登录和 CLI 认证分别走 `/host-login` 与本机 Gateway Bearer。历史部署与拆分边界见 `docs/docker_fork_split_plan.md`。

## 9) 给 Agent 的硬性要求

- **先读当前代码再执行**，不依赖历史文档记忆。
- 输出按改动规模：大修改必须含核心变更点 + 验证结果（已执行命令／未执行原因）；小修改只需核心变更点。不要求每次列出所有修改文件或展示 diff。
- **发现本文过时，直接更新 `AGENTS.md`（及对应细节文档）并在结果中说明依据。**
- **改本文后立刻统计字符数，> 50,000 必须拆**（命令与口径见文首「体积硬约束」）。

## 10) 宿主机权限模式与沙箱执行机制

**改权限 / 沙箱 / 平台隔离前必读**：`AD/permission_and_sandbox.md`（与本文同级）。两套**独立但叠加**的控制：**权限模式**（`readonly`/`approval`/`auto_approval`/`unrestricted`）× **执行环境**（`sandbox`/`direct`，仅宿主机模式可切）。

### 10.1 权限模式

- **`readonly`**：`run_command` 只能在只读沙箱执行；读取同样受限（见 §10.8），**读不到属预期边界不是故障**；写文件直接拒绝；持久终端可建可输入但以只读身份运行；权限跨界切换时现有终端销毁重开。
- **`approval`**：命令按既有规则预审批，或先只读执行、遇拒后审批并以可写沙箱重试（**不放大读边界**）。完全访问下每次命令与终端输入执行前人工审批，通过后直接在宿主机执行，不做沙箱试运行。
- **`auto_approval`**：工作区内写文件直接执行、区外进审核；沙箱命令保留原逻辑。完全访问下每次命令先自动审核，通过后执行，拒绝则回填失败结果并继续循环。
- **`unrestricted`**：权限层不拦截，是否沙箱由执行环境决定。**「无限制」只管工作区内**——沙箱读边界仍是白名单，范围外需路径授权；只有 direct 才真正无边界。

### 10.2 执行环境

- `sandbox`（默认）：macOS `sandbox-exec`／Linux `bwrap+seccomp`／Windows `WSL2`。
- `direct`：宿主机直接执行，**只读与计划模式强制沙箱**。首次切入弹风险确认（红色「我明白，切换」+ 返回沙箱 + 不再提示）；`full_access_warning_dismissed` 默认 false，恢复已有 direct 对话不反复弹窗。
- 审批记录绑定创建时执行环境、完整参数与任务身份，执行前核对并单次领取；`direct_execution` 用完全访问作用域，不借用单次提权 grant；审核取证独立绑定只读沙箱。切换后一直生效，**无自动回退**。

### 10.3 路径授权

- 两类：**可读可写**、**仅可读**，可读集合 = 两者之和。
- 配置来源只有两个：`config/host_sandbox_policy.json`（UI 唯一读写目标）+ 环境变量 `HOST_SANDBOX_MACOS_WRITABLE_PATHS`（合并去重）；`settings.json` 的旧映射已移除。
- 工作区级授权：弹窗分「作用域」（当前工作区/全局）与「类型」；工作区条目存 `workspaces` 字典、**只增不减**，deny 清单仅全局级；工作区移动/重命名后条目自然失效。模块锁**必须 `threading.RLock`**（save 持锁会回调 load/save，普通 Lock 自死锁）。

### 10.4 维护约束

不要在提示词里暴露内部实现细节（如「命令文本猜测」）；文案面向能力边界与操作建议，不描述内部判定算法。

### 10.5 审核智能体

- 设置页「审核智能体」→ `personalization.json` 的 `review_agents`；三个：`auto_approval`／`goal_review`／`workflow_review`，字段只有 `model`（留空=自动规则）与 `max_rounds`（默认 10）。**思考固定开启、推理强度走默认、无设置项**；**无产品层超时**（流式 300s 无块即断流重试）；**取证命令超时固定 60s**。统一经 `modules/review_model_caller.py`，失败按 `modules/retry_policy.py` 分类重试（最多 5 次、固定 10s），每次请求计入账本（§22）；旧的独立 json 与 `sub_agent_models.json` **已废除，无兼容**。
- **送审上下文**：user 为紧凑纯文本；直接输入/运行引导/ask 问答合计最近 5 条、普通调用＋结果最近 5 个，固定附已批准计划/当前目标并去重；工具须按结果配对（不能用执行前的 `_recent_tool_actions`）；历史补查用本次冻结快照。审核失败独立 error、不执行且不转人工；新引导/条件变化使旧审核失效。契约 `docs/auto_approval_review.md`。

### 10.6) 运行模式（work_mode）系统

控制**与用户的交互节奏**，与权限模式正交。**`run_mode` 已被 fast/thinking 占用，严禁混用。**

- **plan**：只制定计划并讨论；权限锁只读、执行环境锁沙箱（UI 禁用 + 后端双重强制）；唯一写例外 `.astrion/plan/*.md`；完成后 `submit_plan`，**批准后自动切 execute**（恢复进入 plan 前的权限与环境）。
- **ask**：先讨论后开工；**禁用 `ask_user`**（开放式讨论写在正文）；与 execute 无执行层差异。
- **execute**：自行梳理计划、直接开工，仅硬阻塞才提问。

后端核心 `core/main_terminal_parts/tools_policy.py`（WORK_MODES、`switch_work_mode`、plan 锁、`RESTRICTED_PERMISSION_MODES={readonly}` 与只读⇄沙箱联动）；执行环境锁在 `core/main_terminal.py::set_execution_mode`。**Web 新对话**：新建按钮只进 `/new`，首条消息才创建；执行环境/网络只继承刚离开的同工作区对话，跨工作区或来源缺失固定 `sandbox + restricted`，**禁止扫描最近对话**；推理强度 `null` 是有效默认；**任何创建路径不得在 plan 锁之前调用非只读 set**（曾因此 500）。提示词 `prompts/work_mode.txt` + `mode.py::_build_work_mode_rules` 是三档唯一来源；API 仅空闲可切（409）；前端在 streaming 时锁定，plan 下权限/执行环境禁用但**网络权限组保持可调**。

### 10.7) 后台命令只读沙箱

后台 `run_command` 必须透传 `sandbox_write_access`：由 Host 平台执行器选只读或可写计划。**链路不变量，不得回退。**

### 10.8) 平台级只读强制

只读由平台原生机制兜底；`config/limits.py` 的命令文本白名单只是**审批启发式，不是安全边界**（可绕过，后果仅是多走一次审批）。

- **macOS host**：Seatbelt 白名单读模型（`host_sandbox_runner.py`），只读/可写共用同一白名单，区别只有写权限。要点：缺祖先目录 literal allow 会让 exec Abort trap；**deny 必须位于所有 allow 之后**（曾致工作区 `.env` 实际可读）；xcselect 垫片需放行 `MACOS_BASE_MACH_RULES` 与 `/Library/Preferences`；原生文件工具读 roots 与白名单同源；受限网络档允许本机监听与可写目录内 Unix socket，但挡不住监听 `0.0.0.0` 后的局域网入站。
- **Linux host** 见 §20；**Windows** = WSL2 最小根文件系统白名单。

## 11) 统一子智能体

所有主对话只用普通 ConversationManager；**禁止**在业务代码加入双目录回退或旧传统子任务恢复（旧数据仅离线迁移）。

- **容量**：设置→子智能体「允许同时存在的子智能体数量」，用户级 `sub_agent_max_instances` 默认 20、仅正整数，唯一来源 `modules/sub_agent_settings.py`。按 `status != terminated` 计数（idle/running/failed 都占名额），**只有 terminate 释放**；不另限 running；已有实例续接不占新名额；创建检查与登记共用注册表锁。输入栏显示同口径「X 个子智能体」。
- **名称与工具**：`modules/sub_agent/definitions.py` 是唯一签名来源，`named.py` 分派。name 同对话 casefold 唯一、1–80 字符、**终结后不复用**；内部编号不对模型暴露。主侧 6 工具＝create/send/status/stop/terminate/answer，子侧 ask_master/ask_other_agent/answer_other_agent/status。工具结果不返回 task_id/conversation_id（前端运行视图保留）。模型用 intelligence 选；**不提供**角色工具、finish_task、强制交付文件、子等待 sleep、公开 run_in_background。
- **保留与复用**：完成后默认保留 idle 并优先复用原实例及上下文。**本轮完成、暂时无任务、准备最终回复都不是终结理由**，不例行清理、不预防性释放名额。只在用户明确要求、实例发生不可恢复错误、或严重违反明确指令且纠正后仍偏离时才 terminate；达到实例上限且无实例可复用又确需新建时，可终结不再需要的空闲实例。**发现正在或即将执行未经授权的破坏性操作立即 stop。** 普通报错/可恢复故障/理解偏差不构成终结理由；停止与终结都不能撤销已发生操作。
- **轮次**：全部后台，自然最终回复进 idle 并保留上下文。`timeout_seconds` 省略即不限时；**预算到期必须完成当前模型响应与整批工具并真实回填后，才阻止下一次模型请求**。stop 即时取消：半截响应不提交，已提交调用的缺失结果补写「被主智能体取消」（界面停止为「被用户取消」），已完成结果保留；停止后 idle、`end_reason=stopped` 且可继续。terminate 是吸收态并优先于 stop。运行中消息安全入队，**不能插到 assistant/tool 中间**。
- **持久化**：共享注册表的查询不得覆盖活动记录。Python 写 `sub_agent_runtime.json`（version=1）和 `sub_agent_tasks/<task_id>/conversation.json`。Rust 写 `sub_agents.sqlite3`，v1 文件仅由迁移作业读取。**不读旧 sub_agents.json**。保存 pending_work、pending_master_messages、work_paused；stop 暂停排队消费，新消息再解除。恢复校验原模型、固定权限、项目身份与工作目录，**不隐式重跑旧任务**。详见 `AD/multi_agent.md`。
- **通知与单写者**：中间输出留子记录，每轮只把最终回复作为 user 消息通知主；子子通信同时报告主。生产端只入队；主运行中在完整工具批次后按序消费，主空闲时 poll 预占门闸并经 RuntimeService 移交 token。**有消息立即消费，不等 running 全部退出**（避免 ask_master 互锁）。目标暂停策略不被自动通知解除。
- **显示契约**：`auto_message_type=multi_agent_*`、`visibility=chat`、`starts_work=false` 是协作消息协议；持久化与事件共用 message_id，恢复只走显示快照；传输字段 `has_running_multi_agent` 不是对话类型。
- **MCP 与权限**：每子实例独立 MCPClientManager，复用全局服务注册表，按子智能体名单独立注入和调用。默认 Playwright profile 加稳定 actor_id；MCP 不套内置目录写权限沙箱；四档权限见 §18。
- **显示与工具同步**：主 formatter 在 `agent_context.py`、子 formatter 在 `toolkit.py`；前端 `toolRenderers.ts` 委派 `subAgentResults.ts`；meta 只参数、content 只结果；新增工具同步 tool_config 类别、工具系列/只读清单与动态加载注册；快捷面板与详情时间线文案统一在 `components/chat/quickdock/subAgentToolText.ts`（「动作＋关键参数」，新增子工具补一条）。⋯ 菜单「停止」仅 running 可点 → `POST /api/sub_agents/<task_id>/stop`，软停止后 idle、保留实例/名额/上下文，与「强制关闭」（terminate）区分，结果以「被用户取消」通知主。
- 回归：`test/2026-10-22_子智能体统一/`；真实模型、UI 与各原生平台另需实机验收。

## 12) 对话级主任务门闸与单写者不变量

> 事故背景：并发两个主聊天任务交叉写 `conversation_history`，产生 `assistant→assistant→tool→tool` 乱序段，最终 API 400 `tool_call_id is not found`、通知永久丢失。

### 12.1 单写者不变量（核心）

**一个 WebTerminal（≈ 一个打开的对话）同一时刻只允许一个主聊天任务。** 主任务包括用户消息任务、后台完成通知派发、多智能体 idle 派发等一切会向 `conversation_history` 追加消息并请求模型的执行体。**宁可拒绝/推迟，绝不并发写入。**

### 12.2 门闸本体

`server/main_task_gate.py`：进程内全局锁 `_LOCK` + terminal 实例属性 `_main_task_gate_token`（**不是**以 terminal_id 为 key 的字典）。

- `try_acquire_main_task_gate(terminal)`：非阻塞，空闲发新 token 返回，被占用返回 `None`
- `acquire_adopted_main_task_gate(terminal, token)`：认领派发方预占 token，**判定依据是 token 相等**；被他人占用返回 `None`，空闲则退化为竞争获取
- `release_main_task_gate(terminal, token)`：仅 token 匹配时清空，不匹配/重复/空 token 均为无操作
- `is_main_task_gate_busy(terminal)`：只读探测

获取/认领与释放必须**成对且带 token**。⚠️ 历史文档的 `acquire_or_claim_main_task_gate(terminal_id, owner_desc)` **代码中不存在**，勿引用。

### 12.3 入口与 token 移交

- **接入点 6 处（另 1 处外层兜底）**：`process_message_task`（`server/chat_flow.py`）、通知派发预占（`chat_flow_task_main.py`）、手动压缩（`tasks/compression.py`）、深度压缩 REST（`conversation.py`）、工作流激活与通知派发（`workflow_runtime_api.py`）、兜底释放（`tasks/models.py::_run_chat_task` 的 finally）。**新增任何主任务入口必须走门闸**；chat/notice/compression 受理检查统一按对话互斥。
- **压缩**：空闲手动压缩是 `task_type="compression"` 的主任务；运行中经 `server/tasks/compression_requests.py` 在当前主任务内排队，完整响应与整批工具结果回填后以 `mode="requested"` 压缩续接并保留工作段锚点；重复请求合并，停止取消待执行，**禁止另起并发写入者或从事件 0 重放**。自动压缩同属原主任务；压缩均算智能体运行（运行中发送即引导）。`server/compression_commit.py` 一次原子提交，失败或提交前取消保留旧历史；`tasks/queue_state.py` 把未消费引导恢复到草稿，保存成功才确认移除，不自动续发。
- **通知链移交**：轮询器派发前预占门闸，token 经 `RuntimeContext.directives.main_task_gate_token` → `TaskRecord.directives` → `run_chat_task_sync(..., main_task_gate_token=...)`；派发失败要释放门闸并回滚已打标记（`_rollback_completion_notice_marks`）。⚠️ `session_data["main_task_gate_token"]` 代码中不存在。
- **自适应压缩阈值**：`adaptive_compress_enabled`（默认关闭）决定是否计入实测固定开销基线；判定 `modules/adaptive_context.py::is_adaptive_enabled`，**四类消费点必须同时判断**：`server/chat_flow_task_main.py`、`chat_flow_tool_loop.py`、`server/runtime/service.py::_context_limit`、前端 `InputComposer.vue` 圆环分母。
- **深压缩阈值**：`metadata.compress_limit_tokens` + `compress_limit_source`（snapshot/custom）是唯一权威。创建对话时 `create_conversation` 读一次个人配置写快照（Agent 用 `deep_compress_trigger_tokens`，Chat 用 `chat_deep_compress_trigger_tokens`），之后触发判断/上下文分母/前端圆环都只读对话记录；老对话缺字段回退当前配置，由前端空闲时 `POST .../compress-limit` 补写；修改只允许空闲（409）。**不要再新增直接读个人配置的阈值判断。**

### 12.4 改代码硬性

1. **新增任何主任务入口必须走门闸。**
2. **不要在 return 分支手写 `_tool_loop_active` 恢复**：`execute_tool_calls` 已是守护包装（try/finally 复位，内层 `_execute_tool_calls_impl`）。
3. **不要依赖 build_messages 防御层掩盖并发问题**：孤儿 tool 剥离只是止血层，出现 warning 按事故排查。

### 12.5 Socket.IO 已整体移除

轮询是**唯一**实时通道；**事件出口唯一权威 = 任务事件流**（`TaskRecord.events`，`_append_event`）。替代：`status_update` → 空闲 5s 轮询 `/api/status`；`quota_*` 本在事件流；会话列表/切换 → 操作后主动刷新；`system_ready` → `loadInitialData`；终端输出 → REST 轮询；连接状态 → `/api/health`。

- 新增「实时通知」一律写 `_append_event` 或扩 `/api/status` 载荷，**禁止重新引入 socket 推送**；`server/extensions.py` 只剩 `run_background`。
- `WebTerminal.broadcast()`/`terminal_manager.broadcast` 调用点保留但回调恒为 None；`make_terminal_callback(username)` 是兼容空操作，仅为不炸历史 import。

## 13) 工具动态加载（tool_loading）

> 唯一权威 `core/tool_loading.py`；设计文档 `docs/dynamic_tool_loading_plan.md`。

**机制**：低频工具在顶层 `tools` 数组只以 stub（名称+引导式短描述+空参数）常驻；模型先调 `load_tools(tool_names=[...])` 拿完整定义再调用；守门按对话 metadata 放行已加载工具。

1. **对话文件是唯一权威**：`metadata.tool_loading` 创建时快照一次（所有新主对话统一，无类型例外）；执行链路**禁止**为此读 personalization／Flask session。
2. **老对话无字段 = 未启用**：读侧一律 `get_tool_loading_state()` 防御性解析，**不做迁移**；字段损坏同样按未启用。
3. **tools 数组对话级稳定**：deferred 工具替换为 stub 而非剔除（纯 tool result 塞定义曾导致反复 load 死循环）；stub 只由完整定义决定、与加载状态无关，数组每轮逐字节一致；**禁止**把 loaded 工具追加回数组。
4. **prompt 目录进完整 system 冻结快照**（主 Agent key=`frozen_system_prompt`；旧 `frozen_tool_loading_prompt` 仅供一次性迁移），不随 loaded 变化。
5. **压缩必须重置**：深/浅/手动压缩三处钩子统一 `reset_state_after_compression`（loaded 清空、pending 回满）。
6. **守门**：`tools_execution.py` 分发链前置，`deferred_set − loaded` 的工具被调用返回引导错误；`load_tools` 本身在 `_READONLY_ALLOWED_TOOLS` 中。
7. **`load_tools` 的 formatter 必须保留完整 JSON 定义**（`agent_context.py::_format_load_tools`），只做摘要会让历史重建后模型失去参数结构。
8. **重复加载直接拒绝**：全部已 loaded 时返回 `success=False`（`load_tools_all_already`）；prompt 与 formatter 强调「不进 tools 数组也可直接调用」。
9. **`close_sub_agent` 已彻底删除**，勿恢复。

**新增可延迟工具 checklist**：① `DEFERRABLE_REGISTRY` 注册；② 后端 formatter 与前端 renderer 覆盖；③ 个人空间勾选 UI 自动出现（i18n `personalization.toolLoadingCat.<key>` 双语）；④ 跑 `test/历史测试_日期不明/test_tool_loading.py`。

## 14) 网页提取与搜索

> 唯一权威 `modules/webpage_extractor.py`；适配器在 `modules/search_providers/`；契约 `AD/web_extract_and_search.md`。

**提取链**：白名单直提（本机、免费、**唯一自动回退层**）→ 用户选定的提取商（Jina 默认／Tavily／Exa／Parallel）。**选定商失败即报错，不再向其他家回退**（避免用户不知情消耗多家付费额度）。

1. 两个工具调用点都走统一入口 `extract_single_url()`，不要绕过单写某层。
2. 直提降级链：GitHub blob → jsDelivr CDN → GitHub API contents（匿名限 60 次/时）→ trafilatura → 转交选定提取商。**不要用 raw.githubusercontent.com**（国内 DNS 污染）。
3. `trafilatura` 是可选依赖：import 失败时通用直提静默失效（GitHub 直链仍可用），白名单不报错。blob URL 的 branch 按单段解析（`[^/?#]+`），含斜杠分支名会自然降级。
4. 域名匹配 = 精确或子域名后缀，输入经 `_normalize_domain` 清洗；提取商凭证复用 `resolve_search_credential()`，选定家无 key 返回明确错误，**不静默换家**。
5. **新增 personalization 键必须双注册**（`DEFAULT_PERSONALIZATION_CONFIG` + `sanitize_personalization_payload`），否则保存后被静默丢弃。
6. 返回格式 `🌐 网页内容 (N 字符)` + `[提取方式: ...]`。**read-before-edit 已读白名单**含 `recall_project_memory`（返回全文视为已读），`search_project_memory`、`search_skills` 只回片段、刻意不标记。
7. **配置分离**：`personalization.json` 只存 UI/行为偏好，搜索/提取服务配置存 `service_config.json`，API 密钥存 `secrets.json`（0600、原子写）；三者统一由 `personalization_manager.py` 读写。
8. **搜索适配器**：`search_engine.py` 只做校验/归一化、凭证解析、结果包装；适配器出参 `{"ok","results","answer","dropped"}`，**`dropped` 必须透传**到 `filters.provider_dropped`；前端 `filters`/`results[]` 结构对所有服务商一致。

## 15) 设置页与提供商体系

> 后端 API 契约 `docs/providers_api.md`；完整分区清单与目录字段见 `AD/settings_and_providers.md`。

**设置页**：原个人空间抽屉（13 Tab）拆成「个性化 + 账户」留抽屉，其余迁 `/settings` 全屏页；沿用手写 History API 路由，**不引 vue-router**。

- **路由前后端双登记**（曾因只登前端导致整页 404）：`server/auth.py` 注册 `/settings` 与 `/settings/<path:section>`，前端 `route.ts` 登记 `settings`、`settings/*`。**进出都是整页跳转**，不做 SPA pushState（pinia 每次重建，`activeSection` 从 `general` 起）。
- **挂载在 App.vue 的 Agent 加载门槛之前**，以 `settingsRoute` 独立互斥显示，组件在 `app/components.ts` 异步注册；设置页**不启动 Agent 连接、工作区运行态或轮询**。URL 深链由 SettingsShell onMounted 解析（先 `fetchSessionStatus()` 确保管理员判定就绪），非法分区回默认。
- **SettingsShell 与 PersonalizationDrawer 禁止同时可见**；**新增分区三处同步**：`settingsNav.ts` 的 NAV_GROUPS + SECTION_TITLE_KEYS（含搜索索引）、SettingsShell 的 SECTION_COMPONENTS、`stores/settings.ts` 的 `SettingsSection`；样式在 `settings/settings.scss`（`stg-` 前缀）。管理员分区 = codex/admin/providers。
- **MCP 设置**：`/settings/mcp` 使用已认证的 Host 会话。全局服务启停优先于主、子智能体各自的全局名单和工作区覆盖。工具发现、注入、列表和调用统一按有效名单校验。契约见 `AD/mcp_runtime_scope.md`。

**提供商体系**：

1. **目录与存储**：`config/providers_catalog.json`（**一个 base_url 一条目**；编程订阅 badge=「编程订阅」，套餐 Key 与按量 Key 不通用；`static_models` 仅在 `/models` 失败时回退，**在线发现优先**）。凭证存 `<DATA_DIR>/providers.json`（0600 原子写，**不入 git**）。`modules/provider_manager.py` 是唯一权威。
2. **双协议**：Chat Completions + Responses；`/models` 不标注协议，裁决顺序 catalog `protocol_map` → models.dev `npm`（须有 `protocol_source` 门控）→ 默认 chat。**Anthropic Messages / Google 原生协议不做适配**（模型正常显示，`chat()` 报错 + tooltip）。`_fetch_models` 在 fetch 层剔非聊天模型，**解析层 `parse_models_payload` 保持纯净**。
3. **注册表三路合并**：手写 custom + provider 同步 + Codex；key = `{provider_id}/{model_id}`（带 `api_protocol`）；`get_profiles()` 用 models.dev enrich 四字段。
4. **权限=管理员级**：`server/providers.py` 全部端点 `@api_login_required + @admin_api_required`；用户级 key 是预留方向，**勿擅自做**。
5. **推理强度绑思考能力**：`supports_reasoning_effort` 始终跟随 `supports_thinking`（三方一致）。**顶栏模型弹窗**：单列，列表下是推理滑块，最左「无」即原快速模式（`fast`），底部左「默认推理」（`null`）右「高速模式」；Agent／Chat／手机共用 `ModelEffortPanel.vue`，换算在 `utils/reasoningStops.ts`。**ChatGPT 高速模式独立**：只对 OAuth 模型显示，状态权威 `metadata.chatgpt_fast_mode`，仅目录支持 Fast 时发 `service_tier=priority`；不传给子智能体或辅助请求。
6. **custom_models.json**：`/api/custom-models` CRUD 直读部署目录，`_sanitize_custom_model` 白名单校验，**写入始终落 `DEPLOY_CONFIG_DIR`**。**模型可见性 = 用户级**：`personalization.hidden_models` 后端只注册 + sanitize，**过滤在前端** `stores/model.ts`，与提供商管理是两条独立线。
7. **Codex 即 openai-codex**：OpenAI 拆 `openai-api`（api key）与 `openai-codex`（订阅 OAuth）。**连接管理单一入口**：连接/断开/刷新只在「提供商」分区（`useCodexLogin.ts` + `CodexConnectDialog.vue`），**禁止**在 Codex 分区内联登录/断开/刷新。
8. **其余**：图标在 `static/icons/providers/`（新增提供商先补图标）；`ollama`/`lmstudio` 是 auth=none；models.dev 数据源 = 快照 + 在线缓存 6h，`protocol_source` 门控必须存在。
9. **模型切换与重连**：Chat Completions 与 Responses 可互切；只向同一 Responses 连接回放加密推理。OAuth 重连验证账号身份并保留连接 ID，刷新目录后报告完成。目录暂缺时对话与设置保留原模型 key，无法使用的对话模型显示「未选择模型」。细则见 `AD/settings_and_providers.md`。
10. **模型目录自动刷新**：`POST /api/providers/refresh-all` 刷全部已连接 + Codex，前端进入应用与每小时各调一次（挂在 `app/lifecycle.ts` 的 settingsRoute 分支之前），进程内每小时最多真跑一次、重复调用返回 `skipped`。**不要在 `start_background_jobs` 加后端定时器**（会变成应用关着也刷）。
11. **Responses 请求形状（绕开 OpenAI 6.2 缓存回归）**：gpt-6.2 系**不缓存 `instructions`**（缓存缺口≈system 大小），`input` 前缀照常缓存；工具 schema 编译更严（`propertyNames` 直接 `invalid_json_schema`）。`translate.py` 两条硬约定：① OpenAI 系端点把 system 作为 `input` 首条 `role:"developer"` 发送且**完全不写 `instructions`**（第三方网关上的非 OpenAI 模型保持原形状）；② `flatten_tools()` 前必须过 `sanitize_json_schema()`（剥 `propertyNames`／`patternProperties`／`if-then-else`，参数名不动）。只做减法；`prompt_cache_key` 只对 codex_oauth／openai-api／第一方端点下发。**改前先确认 OpenAI 是否已修复**；回归 `test/2026-11-19_*`。

## 16) 预览面板（Preview Panel）

> 智能体创建的 HTML、启动的 dev server、输出的 localhost 链接自动进右侧 dock「预览」窗口。代理架构与隔离细节见 `AD/preview_panel.md`。

- **检测唯一权威** `modules/preview_targets.py`，两类捕获源：① `write_file`/`edit_file` 的 `.html`/`.htm`；② 流式输出的 localhost 链接（120 字符滚动缓冲，防 URL 跨 chunk 断裂）。**run_command/terminal 输出检测已删除**（脏数据主来源）。URL 路径限 ASCII 白名单；裸主机与 favicon 不记录；尾部 `/` 归一后按**完整 URL** 去重（同 origin 不同 path 保留多条，刻意设计）；`0.0.0.0` 映射 `127.0.0.1`。存储 `metadata.preview_targets`，同 URL 重复命中不写不广播。
- **安全模型 = 换源隔离**（同源端点 + `sandbox` 等于无沙箱）。localhost 走**独立预览服务器**（`server/preview_server.py`）：只绑 127.0.0.1 随机高位端口；路径带每对话 token（优先 `metadata.preview_token`，被整文件回写冲掉时用 `_derived_preview_token()`）；两道关卡 = Host 头白名单（防 DNS rebinding）+ token 注册表反查。`build_preview_runtime()` 在 targets/bootstrap/messages 三处带 `preview_base` + `preview_token`。
- **预览服务器解析工作区必须传显式身份（硬约束）**：独立 Flask app 无主应用 session，`_resolve_context()` 必须传 `identity=RuntimeIdentity(host_mode=True, host_workspace_id=<注册工作区>, role=<注册角色>)`；否则造出假工作区 `users/<u>/projects/<ws>/project`，并用同一缓存键把错误 project_path 的 terminal 写进全局终端池（此后文件预览必报「文件不可用」，直到进程重启）。
- **Host 预览统一启用**（唯一判定 `is_preview_enabled()` = `IS_HOST_MODE`，记录／端点／序列化／prompt 四处门控一致）。**远端域名**（Host 经反代）时前端回退旧同源端点 `/api/preview/file|proxy`。**SSRF 防护：只代理当前对话已登记的端口。** MVP 限制：不转发 WebSocket（HMR 失效）、动态 import/fetch 绝对路径不重写、代理每请求一次 HTTP 拉取。
- **前端**：`stores/preview.ts`、`PreviewWindow.vue`、`PreviewPanel.vue`。自动展开 `preview_auto_open`（默认 false）：最新目标优先、同目标更新不重弹、手动关闭后本次任务内不再展开、对话区 <300px 不展开。**窄布局自动折叠**：<300px 按「快捷面板组（快捷面板＋文件预览＋预览窗口）→ 右侧终端/Git」折叠，手动展开则强制保留至该组关闭，单向不自动展开。
- **桌面端预览窗口已停用**：壳内不渲染两个预览组件，`preview_auto_open` 不生效，预览目标不计入快捷面板判定；**后端照常记录 `metadata.preview_targets`，只是不展示**。正文 http(s) 链接由 App.vue document 级 capture 拦截（仅 `__ASTRION_DESKTOP__`）转内置浏览器（先 `nativeBrowserStore.open = true`，拿到 lease 后 `navigate`；超时或缺作用域回退 `openExternal`）；**同源链接必须放行**。
- **正文文件链接分流**：`a.md-download-link` / `a[data-download="1"]` 在 Host 模式调 `/api/system/open-file` 用默认程序打开（失败 toast 后端文案）；判定走 `settings.isHostMode`；`<show_file>` 下载按钮不受影响。
- **Prompt**：`prompts/preview_panel.txt` 冻结注入——启动服务器后必须**写完整地址**、不要引导用户手动开浏览器、自我验证仍走 Playwright。

## 17) 代码索引（codegraph）

> 精确的**代码结构查询器**（tree-sitter 符号图 + SQLite/FTS5），**不是语义搜索引擎**——按词法匹配符号名与代码词。

- 全局 CLI `/opt/homebrew/bin/codegraph`（npm `@colbymchenry/codegraph`）；`codegraph init --yes .` 建索引，产物 `.codegraph/` 已 gitignore；**遥测默认开启，需 `codegraph telemetry off`（换机器/重装后重关）**。

| 命令 | 用途 |
|------|------|
| `codegraph explore "<查询>"` | **首选**：相关符号源码 + 调用路径 + 影响面 |
| `codegraph impact <symbol> --depth 2` | 改动影响面（含 HTTP 路由反向关联） |
| `codegraph callers/callees <symbol>` | 谁调用它 / 它调用谁 |
| `codegraph query <name>` / `node <symbol>` | 按符号名搜索 / 单符号源码与调用链 |
| `codegraph affected [files...]` | 改动影响哪些测试（`git diff --name-only \| codegraph affected --stdin`） |
| `codegraph files` / `status` / `sync` | 文件结构 / 统计 / 手动增量同步 |

- 查询**必须用符号名或英文术语**（纯中文会直接失败）；改代码前先查影响面；行号可信但 `callers` 建议交叉验证（实测有调用点只出现在 `impact`）。CLI 无 watcher，改代码后 `codegraph sync`；MCP 仅主 agent 可见（子智能体经 Bash 调 CLI）；结果有噪音需人工过滤。新优点/问题记进项目记忆 `codegraph_code_index.md`。

## 18) 子智能体固定权限与单次命令完全访问

> **改子权限 / 审批前必读**：`AD/sub_agent_access.md`（含 UI 细节与逐条约束，与本文同级）。回归：`test/2026-10-07_子智能体权限与单次完全访问/`、`test/2026-10-07_审批面板交互/`。

- **`create_sub_agent` 必填 `access_level`**：`working_dir_write`/`workspace_write`/`sandbox_write`/`full_access`，创建时校验主权限上限；plan/readonly 可创建两个窄档。实例保存创建时规范化的 `workspace_root`、`working_dir` 与固定档位，恢复时核对原路径身份，**不重新 resolve 后扩大授权**；旧记录缺档位不迁移、不恢复。
- **可信权限唯一入口 = `modules/execution_scope.py` 的 ContextVar**；子工具经 `modules/sub_agent/execution.py` 用独立 FileManager/TerminalOperator/TerminalManager，**不修改父对象再恢复**。父权限切换不改变已有子档位；子工具只开放自身白名单，**不开放后台命令或单次升级参数**。
- `working_dir_write` 仅子工作目录可写，`workspace_write` 为原工作区可写；`workspace_root` 始终是原项目身份；未绑工作树时用 `working_dir`，已绑定时用独立 execution_root（见 §27）。macOS 临时目录在固定写根内，**不附带宿主 `/tmp` 写授权**；原生 IO 用 POSIX no-follow 描述符（或 Windows 句柄），拒绝替换链接、特殊文件与多硬链接写入；目录按路径组件检查并安全新建，**不用字符串 startswith**。
- 主 `request_full_access=true` **必须提供非空原因**；plan/readonly 拒绝，只适用于真实本机 Host 后端；已在 direct 时无需申请。**Windows direct 用 cmd.exe，沙箱 bash 命令不得自动原样重试到宿主机。**
- 单次申请复用 `ToolApprovalManager`（`approval_type=full_access`）。用户级 `allow_auto_approve_full_access` 默认 false：关闭时自动审核模式需自动与人工独立通过、无限制模式仅需人工；开启时两种模式均由审核智能体批准即可执行（`human_approval_required=false`）。**该标志是单闸语义**：面板不渲染允许/拒绝按钮（`humanDecisionRequired`），`decide()` 抛 `tool_approval.human_not_required`；**双闸场景人工先允许不能取消必需的自动审核**。条件在请求创建时快照。拒绝/故障/过期均不执行，任一拒绝仅结束申请并回填失败结果，**不触发 task_stopped**。授权记录绑定执行环境/完整参数/任务身份，`claim_execution` 原子领取一次，`FullAccessGrant.consume` 再核对，失效不破坏消息配对。
- 前台、后台均绑定该次权限，不改变主对话全局状态；后台创建经 `asyncio.to_thread`，Popen 与取消在同一锁内握手。**正常后台完成不等于取消全部后台任务。**
- **审批 UI** 在输入栏 `ComposerApprovalDock.vue`（人工模式：上方滚动内容 + 左下固定拒绝/允许；自动审核时加右侧进度区）；同对话串行审批、不加翻页。**收起后的「待审批操作」入口**内嵌在 git 状态栏「N 个终端」之后、`+/-` 之前（纯文字无边框图标），可见条件 = 已收起且存在**仍需人工决定**的请求（`needsHumanDecision`）；任务终态兜底清理残留条目。视觉由用户验收。
- **审核失败终态（`decision=error`）必须由显示投影当终态处理**：projector 的 resolved 白名单含 `error`，`tool_approval_required` 遇到非 `pending` 终态直接跳过；前端同样跳过、切换对话/新建页清空本地审批状态，否则重载快照会把已失败请求塞回列表。

## 19) 运行中对话显示快照

> 显示加载唯一入口：`server/conversation_bootstrap.py` + `static/src/app/methods/conversation/bootstrap.ts`；契约 `docs/运行对话显示快照契约.md`。

- **桌面标签导航（v3 多视图）**：标签唯一写者仍是 `modules/desktop_navigation/`（state v3：`placement`、`page`、`renderers`；无 navigation/commit/resume）。每个标签一个独立内容视图，**视图一生固定绑定一个标签**（启动 `attach {key, renderer_id}`）；侧边栏点击/新建/工作区/模式切换一律发命令并转发快照，由壳层切视图。仅 chrome 视图轮询快照、壳层广播（revision 更大者）。发送守卫校验 attach＋renderer 所有权＋标签与可见 session 一致；隐藏视图降频（≈1s、不停止）。**禁止恢复** eval 标签桥、全列表 PUT、pending→commit 门禁。契约 `docs/desktop_navigation.md`。
- **零工作区与默认工作区（仅 host）**：首次启动且 catalog 为空时 `ensure_default_host_workspace` 建默认工作区（`~/Desktop/Astrion/Default`，无桌面回退 `~/Astrion/Default`）；写过 `auto_default_workspace` 标记后即使删空也不重建；chat→agent 交接复用同一路径函数。零工作区是正常空态：`code=no_workspace` 时前端静默忽略、**不跳转**；**禁止用组件级 `currentHostWorkspaceId` 判断**；GET 快照不得 503；无模型时 `/new` 装配不报错，只在发送时给 `no_model_configured`。
- **显示加载**：`conversation_view/` 冻结已提交历史前缀并增量维护当前任务显示投影，同一事务捕获投影、主任务摘要与绝对 `next_event_idx`；前端离线构造后一次提交，再用 `attachSnapshot` 从该游标续接。**有限事件 deque 只用于增量，不用于重建全部显示。**
- **禁止恢复旁路**：不要恢复 `restoreTaskState`、从事件 0 重放、末条角色/工具猜边界、文本匹配去重、全历史扫描补工具注册、mounted/watcher 第二次恢复、对账独立写消息/归零游标或全局 task handler。
- **所有权**：导航用 `conversation/session.ts` 的 session 身份与 epoch（空页面也推进 epoch）；轮询独立使用 generation/request owner。**所有异步响应、catch/finally、timer 在写状态前检查归属**，不能只比较字符串 ID。
- **跨对话索引**：`ui/workspace.ts` 的 `/api/tasks` 用独立请求身份，**不绑定导航 session**；切换对话不停止刷新，失败保留缓存并重试。标题统一经 `conversation` store 更新各缓存，补查走显式对话与工作区的 `/metadata`，不读 `/current`。
- **身份与生命周期**：事件与持久化共享用户 `message_id`；工具 preparing/execution/call 别名在 hydrate 一次登记。压缩提交后 rebase 显示前缀并接管新快照；前台结束但后台仍活动时保持活动状态。
- **存储边界**：`read_conversation`（兼容名 `load_conversation`）**不做迁移回写**；**锁顺序 = 显示锁 → 任务锁**，不得反向获取；维持单后端进程约束。
- **工作区导航**：目标工作区与对话随快照一次提交，选择写入串行；同源 API 派发时捕获明确工作区；首次路由先取真实身份，**未知作用域不得猜为 default**；目录刷新不改写已提交的可见身份。
- **输入草稿与运行引导**：无提前输入排队 UI、无自动续发；运行中发送直接作为引导，失败保留输入。引导附件复用 `media_paths`/`file_paths` 隐藏通知（`media_paths_only`），历史重建只传路径。草稿按「用户／工作区／对话」独立，localStorage + 服务器后台重试，以 revision CAS 防旧写覆盖；**不用无 CSRF 的 beacon**。回归 `test/2026-10-23_对话草稿与运行引导/`。
- **查询／运行分层**：`context/workspace.py` 解析可信路径、`data.py` 轻量存储、`resources.py` 装配执行实例；bootstrap、列表、草稿、Token、模式及导航辅助查询**不得为读取而创建 WebTerminal／执行实例或复制技能**。
- **启动期预热与端口复用**：壳层必须在 `waitBackendReady` 之后、**任何视图创建之前**完成一次免密 `host-login`（`session-priming.js`），否则首个 `/api/conversation-tabs` 401 →「同步失败」。后端端口复用上次值（`userData/backend-port.json`）——`SESSION_COOKIE_NAME` 与 localStorage 均按 origin 隔离。首帧主题经 `shell:theme-get` 注入 `window.__ASTRION_INITIAL_THEME__`，内联脚本优先读它。
- 回归：`python -B test/2026-10-07_运行对话快照/run_regressions.py`、`test/2026-10-12_工作区切换重构/README.md`；**不将离线回归等同于实际速度达标**。

## 20) Linux host 沙箱

- **安装入口**：普通账户构建 `astrion-linux-sandbox`，再执行 `sudo bash scripts/setup-linux-sandbox.sh`（默认授权 `SUDO_USER`；root 直接装须 `--user <普通账户>`）。支持 `--dry-run`/`--status`/`--no-install-dependencies`，不依赖 CLI，不启动后端。
- **首批环境**：Ubuntu 24.04 / kernel 6.8 / systemd 255 / cgroup v2 / AppArmor / libseccomp，**其它发行版不自动放行**；可装系统依赖，但**不得靠关闭 AppArmor/userns 或降级 direct 绕过失败**；全局安装与 sudo/polkit 仍待实机验收。
- **可信助手**：`/usr/local/libexec/astrion-sandbox/astrion-linux-sandbox` + `/etc/astrion-sandbox/helper.json` + `astrion-sandbox.service`。二进制、配置与 BPF 必须 root-owned 且不可由普通用户写入，升级保留备份。安装、客户端与特权服务共用独立 Rust crate。
- **身份**：SEQPACKET socket 传严格 JSON 和三个 stdio FD。核验 SO_PEERCRED、SO_PEERSEC 和 peer pidfd，拒绝 UID0 与递归调用。UID、groups、助手路径与 unit 属性只能取自内核和可信配置。用户命令只能在 Rust 入口降到真实 UID/GID、清除能力、开启 NNP 后执行。
- **文件根**：系统挂载路径由 `backend-rust/linux-sandbox/src/protocol.rs` 定义，主/子/只读/后台/持久终端共用 Rust 命令计划。源在普通用户 fork 中逐组件 no-follow 打开并固定 FD，cwd 降权后再验 inode。**`/proc`、`/dev`、`/sys` 与内部 runtime 禁止作为宿主挂载授权**。
- **网络与回收**：共享 host 网络，每任务独立 transient unit（restricted = deny any + allow localhost）；runner 检查 enforcing 标签与实际 BPF，缺失即拒绝；断开/退出/取消均回收 unit。
- **证据**：`test/2026-11-27_Linux_Rust沙箱/` 覆盖 Rust 协议与非特权传输，`test/2026-10-08_Linux_host沙箱实验|助手|适配/` 是迁移前的隔离记录。生产安装与跨发行版支持须分别实机验收。安装脚本**不设置或存储网页密码**（与 §21 分离）。

## 21) Host 网页可选密码保护

- **默认免登录**：未运行脚本、缺配置时沿用回环免登录；**禁止**按监听地址或推测公网状态强制初始化；存在但损坏的配置**必须报错，不能静默降为免登录**。
- **开启与重设**：`./backend-rust/target/release/astrion-headless host-password --password 'YOUR_PASSWORD'`（8–1024 字符，保留空白，可加 `--data-dir`）。每次重置撤销旧登录会话，命令不打印密码。
- **存储**：`backend-rust/src/web_auth/credentials.rs` 管理 `<DATA_DIR>/host_auth.json`（版本、enabled、scrypt 哈希、generation）。Unix 文件权限为 0600，命令与网页共用跨进程锁和原子写入。读取缺文件不创建，写入拒绝符号链接。
- **scrypt 兼容**：沿用 Werkzeug 哈希格式，n=32768、r=8、p=1、64 字节，使用随机盐和恒定时间校验。
- **网页认证**：`backend-rust/src/web_auth/` 处理 `/host-login`，按 nonce 和 generation 校验会话。开启、重设、关闭均使旧会话失效。开启后拒绝普通账户登录和注册。网页设置只提供验证当前密码后关闭保护。
- **桌面豁免**：以壳注入的 `ASTRION_DESKTOP_VERSION` 判断，**不能相信网页 JS、HTTP 头或 query 标记**。`gateway_auth` 只注入请求内身份，不发行浏览器 nonce/Cookie，**无效 Bearer 不回退 Cookie**。
- **运行任务保持**：密码变更不走 logout、不清理终端/任务。回归 `test/2026-10-08_Host密码保护/run_regressions.py`（临时目录与合成凭证，禁读真实 `.env`）。

## 22) 主／子智能体 API 花费

> 契约 `docs/conversation_api_costs.md`；细则 `AD/api_costs.md`。

- **范围**：总花费 = 主 + 每个子智能体 + 思考摘要 + 三个审核智能体请求（审核自 2026-11-06 计入）；按非缓存输入、缓存命中输入、输出三项计价，不单列缓存写入，**冷启动豁免不减免费用**。token 五项与 tokens/s 保持主智能体口径，目标模式 Token 上限保持主／子口径；明细按执行者独立显示（审核 `actor_id=review_agent:<key>`）。
- **价格与账本**：`modules/api_pricing.py` 捕获请求当时精确价格；`modelsdev_registry.py` 版本 2 缓存保留 `cost.*` 及来源时间，**不借其它提供商价格**；`modules/conversation_costs.py` 写 `<工作区 data_dir>/api_costs/<conversation_id>.json`（原子、UUID 防重、Decimal 累加）。**缺单价或缺 usage 标记为未计价，禁止当免费。**
- **子恢复**：`modules/sub_agent/usage.py` 统一归一化、保存 `token_usage.cached_input`、继承累计统计；压缩只清上下文、**不清历史累计**。
- **币种**：`display_currency` = USD/CNY（默认 USD），**人民币只转换显示、不改账本**；`modules/exchange_rates.py` 每 UTC 日后台更新汇率，失败保留缓存，**无首次缓存不虚构汇率**。
- **等待态**：头像 `api_request_start` 优先于上次工具意图与残留流式状态；响应开始或任务终结清除，停止后不重显。
- **工作段 tokens/s**：Chat Completions 用 `output / 首个有内容增量至流结束耗时`；**Responses 只统计正文＋工具调用**（分子 `output_tokens − reasoning_tokens`，耗时由 `utils/api_client/responses/speed.py` 在原始 SSE 层按增量区间取并集，排除思考/首段等待/尾段/工具执行），缺推理明细或有效计时则跳过样本，**不得回退到含思考口径**。费用与累计 token 始终用完整 usage；Responses 另存 `speed_sample`（空对象=跳过）。段级 = `Σ样本输出 / Σ样本耗时`，段结束时写锚点 user 消息 `metadata.token_speed` 并随 `token_update` 下发（前端只本地标记 work_timer、**不重载历史**），在有文字的 assistant 下方渲染「复制按钮 + x.x tokens/s」。回归 `test/2026-10-19_Responses输出速度/`。
- **压缩续接即同一工作段**：深压缩续接**不得**提前 `finalize_user_work_timer()`，递归必须传 `work_anchor_message_id` 继承原锚点（编辑摘要、work_timer、tokens/s 共用同一锚点）。见项目记忆 `work_segment_anchor_and_tokens_speed`。

## 23) 项目 Git 记录与自动评审

- **入口与范围**：侧边栏「工作流」下的「项目 Git」→ `/git?workspace_id=<当前工作区>`，只扫描进入时已确认工作区内的 Git 工作树，跳过依赖/运行态目录、不跟随符号链接；图标用本地官方 Git SVG（17px）。
- **布局**：无应用侧栏的独立全屏页；仓库下拉与提交选择面板互斥，文件导航默认展开，diff 占主要空间；提交说明保持单行，左侧无 hover 的符号打开完整 message 浮层；评审记录不带数字，通过浮层菜单选真实对话。
- **查询唯一权威** `modules/project_git/`（Web 蓝图 `server/project_git.py`，任务启动 `server/project_git_review.py`）：历史 diff 来自固定 Git 对象，提交默认第一父版本，分支用 merge base，**不能用当前工作树代替目标版本**。
- **评审**：新建目标工作区的普通对话，初始 `execute + readonly + sandbox`，自动发送含固定目录与完整 SHA 的请求，沿用正常 RuntimeService/门闸/显示快照；点击时该工作区有活动主任务就提示忙，**不新建、不排队**。
- **身份与数据**：查询不创建执行实例；评审会话独立创建、不切换现有 terminal；跳转携带显式 `workspace_id`，首次初始化验证后等待串行选择再加载；关联记录原子写入运行态 data，每仓库最近 200 条；未知任务状态不视为成功。
- **边界**：只读本地 Git 浏览与评审，不含远端 fetch 与 PR 接入；视觉与真实模型执行由用户验收。契约 `docs/project_git.md`；回归 `test/2026-10-13_项目Git/`。

## 24) CrossPet 本机桌宠接入

> 协议 `docs/crosspet_integration.md`。

- **可选 host 输出**：默认关闭，由设置→通用「CrossPet 联动」单开关控制（所有工作区共用，存 `DEPLOY_CONFIG_DIR/crosspet.json`）；不要求 CrossPet 侧启用，其 `astrion.json` 只提供目录。默认输出 macOS `/tmp/crosspet`、Windows `%LOCALAPPDATA%\CrossPet\state`；关闭即停止三个文件所有写入，保存与输出共用锁。
- **观察入口** `server/crosspet.py` → `modules/crosspet/`：任务线程只派发白名单元数据，配置、模型解析、后台活动探测与文件 IO 都在观察器线程；关闭时不探测活动、不读前台模型、不创建执行实例。设置端点 `server/crosspet_settings.py` 同挂 Web/headless。
- **状态与隐私**：独立 `astrion-state.json`，身份哈希，**不含消息、思考、工具参数、命令、结果或凭据**；心跳不增加动作修订号；`task_complete` 不是最终结束（真实终态与后台活动共同决定动作），业务失败不得庆祝；最多 256 会话。
- **订阅额度** `modules/crosspet/quota.py`：以 Astrion 独立 OAuth 与既有代理后台查询，仅联动开启且订阅已连接时查，只在观察器线程做网络 IO；额度以白名单数值附加到 `quota.gpt`，不输出账号/凭据。CrossPet 侧「显示 Astrion 的 GPT 额度」与原「显示 GPT 额度」互斥；失败保留旧数据、不混账号、不自动切回 Codex。
- **接入**：默认两边目录一致即可；自定义目录经 CrossPet 设置→「接入 AI」登记（等价 `install astrion`）。`CROSSPET_STATE_DIR` 只作用于 Astrion 文件与 `seen-astrion`。**上游 PR** https://github.com/lokicorvus/crosspet/pull/21（fork `JOJO6618/crosspet`，分支 `feat/astrion-integration`，提交 `3a53433`）；上游 main 已到 1.3.5，须用 `_experiments/merge_crosspet_135.py` 三方合并，**不要直接把 1.3.4 补丁 `git apply` 到新基线**。
- 配套副本 `_experiments/crosspet/`（原始 ZIP `_experiments/crosspet-upstream/`）不入 Astrion Git，不复制素材；回归 `test/2026-10-14_CrossPet接入/`、`test/2026-10-15_CrossPet开关/`，Windows 原生运行需实机验证。

## 25) 对话级目标模式

> 契约 `AD/goal_mode.md`；回归 `test/2026-10-17_目标模式重构/`。

- **文件唯一权威** `<工作区 data_dir>/goal_states/<conversation_id>.json`，全部续接保留对话 ID；入口弹窗填写后显式开始，**禁止恢复**输入栏 armed／下一条消息自动变目标的旧行为。
- **生命周期**：关闭目标不取消当前任务；停止主任务持久暂停目标；paused / blocked 均可手动继续；进程重启只恢复暂停显示，**不自动续跑**；**自动后台通知不得解除暂停／阻塞**。
- **执行与交互**：开始／继续设 execute，权限已有 auto_approval / unrestricted 则保留、其余变 auto_approval。活动目标**禁止 ask_user、submit_plan、人工确认工具**；单次完全访问申请仅在「允许自动审核通过单次完全访问请求」开启且权限为 auto_approval / unrestricted 时放行（与 `resolve_approval_requirements` 的 auto_full_access 一致）；正常自动审核与智能体内部通信保留。
- **重试与并发**：网络耗尽后经 RuntimeService 在同一对话派发新 user 输入；额度/认证/配置错误阻塞；Timer 绑定 run_id + task_id + next_retry_at，不能因用量更新 revision 丢失重试；**锁序固定 VIEW_LOCK → 目标状态锁**。
- **审核与限制**：以原目标与真实执行证据判断，**不用「没有工具调用」代替审核**；取证强制只读沙箱，不继承主对话 direct；轮数/Token 限制在弹窗可选，继续保留累计统计并重置本次限制基线。

## 26) 用量统计页

> 细则与回归见 `AD/usage_stats.md`、`test/2026-11-06_用量统计页/`。

- **入口**：设置页「账户 → 用量统计」（`/settings/usage`，内容列 1040px），个人空间「账户」卡片加跳转按钮；前端 `settings/tabs/UsageStatsTab.vue` + `tabs/usage/`，文案 `usageStats`，图表色用 `--heat-*`／`--series-*`（三主题＋首屏回退已补齐）。
- **唯一数据源 = 对话费用账本**（`<data_dir>/api_costs/`，只读）；`modules/usage_stats/` 维护可重建派生缓存 `usage_stats_rollup.json`（按账本 mtime/size 增量，UTC 小时 × 模型 × 执行者）。**禁止**另写用量计数或改写账本；未计价请求单列，不当免费。
- **来源归集**：工作区与 Chat 目录经可信身份解析，查询**不创建执行实例、不选择工作区**；host 共享 `DATA_DIR/api_costs` 时按各工作区 `conv_*.json` 归属，归属不到进「其它对话」。
- **接口** `GET /api/usage-stats`（source / model_group / tz / tz_offset）；`model_group=name`（默认）按模型名称合并、`provider` 保留提供商前缀（`report.py::group_model_key`，账本与缓存不拆分）。时区换算后端出报表时完成；粒度、指标、范围前端切换，统计范围与模型分组变化才重新请求。

## 27) 对话级 Git worktree

> 完整契约 `docs/git_worktree.md`（改工作树、执行根、主子切换通知、分支入口前必读）。

- **启用与落点**：用户级默认关闭，创建新对话时快照 metadata，老对话不迁移；工作树固定落 `.astrion/worktrees/<name>/`；**workspace 身份与执行根分离**。
- **工具**：`enter_worktree`（创建/进入；创建必传 `start_ref` 与 `include_uncommitted`，携带未提交修改用复制并保留原修改）与 `exit_worktree`（返回主目录、保留成果）；**不提供合并工具、不自动清理**。
- **切换时机**：用户只在主智能体空闲时切换（子/后台运行不阻止）；初始环境进 system，后续切换以工具结果／user 通知传达，**冻结 system 不改**；子运行中在完整工具批次边界同步，idle 等下次主发消息同步。
- **执行根跟随**：前台/后台命令与新终端按当前执行根解析，既有进程保留原 cwd；技能与项目记忆归属原工作区；编辑记录/预览用 workspace-relative 路径，根偏移时模型工具结果用绝对路径，**不得把 bootstrap 的文件资源改成执行根后重复拼接**；失效绑定保留恢复入口，**执行失败不得回退主目录**；非 Local 后端明确拒绝。
- **外部/非托管工作树**：`enter` 的键是 `branch_name` 或 `directory`，**严格互斥**（同时给或都不给直接报错）。create 只落 `.astrion/worktrees/`，不接受路径、不产生授权。工作区外需：模型侧在「路径授权」可读写集合内或 `execution_mode=direct`；用户手动切换须是本仓库已注册工作树（拒绝主工作树、符号链接、非工作树目录）。绑定外部根时写 `git_worktree.grant={path, added}`，运行期只由 `modules/git_worktree/grants.py` 的 ContextVar 经 `get_macos_writable_paths`／`get_macos_readable_paths` 单点并入沙箱边界（文件工具、前后台命令、`terminal_manager` cwd 守门同源），**禁止写用户级策略文件**；`threading.Thread` 不继承 ContextVar，后台命令必须显式快照并重绑。该根下有运行中子智能体/后台命令/终端时拒绝退出；退出即清除绑定与临时放行。
- **绑定点必须覆盖主任务全流程**：除工具分发外，`server/chat_flow.py::process_message_task` 线程入口、`service.enter`（branch_name 分支）与 `service.change`（exit 前残留检查）也必须包 `bind_extra_roots`；只绑工具层会漏掉建对话与系统提示词的 git 状态校验，表现为「发送失败：工作目录必须在项目文件夹内」。
- **前端与草稿**：`/api/git-worktree/inspect` 的预检结果就是状态栏分支权威值（`/tmp` 与 `/private/tmp` 不能靠字符串比对）；`/new` 草稿目标不得被 `hydrate()` 无条件清空（仅 `cid !== null` 时清）；`refresh`/`select` 以输入栏弹窗的对话身份为准；**i18n 文案禁止裸 `@`**（vue-i18n 会按 linked message 解析并抛 `INVALID_LINKED_FORMAT`，整个下拉渲染中断）。
- **工作树内没有 `node_modules`**：验证前端构建先 `ln -sfn <主目录>/node_modules node_modules`；合并回主目录后需重跑 build 界面才生效。工作树内 `py_compile` 会因写 `~/Library/Caches` 被沙箱拒，改用 `python3 -B -c "ast.parse(...)"`。
- 回归 `test/2026-11-14_Git工作树/README.md`、`test/2026-11-15_工作树外部路径/`；Windows／WSL／Linux 原生执行与 UI 待实机验收，**不把路径模拟当跨平台验收**。

## 28) 存储空间统计与清理

> 细则见 `AD/storage_stats.md`（2026-11-19 从本节下沉）。

- **仅 host 模式开放**：口径是整个数据根，多用户部署会读到他人数据、清理会删他人文件；非 host 返回 `available=false`。接口沿用 `@api_login_required`，前端 POST 依赖 `static/security.js` 的全局 CSRF 注入。
- **范围 = 数据根内**：`modules/storage_stats/scanner.py` 一次遍历 `DATA_DIR` + `LOGS_DIR`，按 `catalog.py` 最长前缀归 8 桶，未命中归「其他」，**总和等于实际占用**；不遍历工作区 `.astrion/`；沙箱不计；符号链接跳过；字节数优先 `st_blocks`（与 du 同口径），Windows 回退 `st_size`。
- **缓存 + 进入刷新**：`<DATA_DIR>/storage_stats.json`（version 守卫）；`GET` 只读缓存（无缓存触发后台扫描），`POST /api/storage-stats/refresh` 单飞后台扫描（进程内锁、不排队）；前端先渲染缓存再重扫，以 `scanned_at` 变化为轮询终点（30s 收口）。
- **清理只覆盖两类**：`clean/logs`（活动 `*.log` **截断为 0 字节**——用截断而非 unlink，活动句柄会让空间无法及时释放；归档与非日志文件删除、递归子目录）与 `clean/backups`（`keep_days` 1–3650，按 `save/<conv_*>/meta.json.updated_at`，缺失回退目录 mtime，整份删除；只处理 `conv_*` 并复校验父目录身份，符号链接与非 `conv_` 跳过）。**其余类别只统计不清理。**
- **前端**：清理前走 `uiStore.requestConfirm`（danger），显示份数与预计释放；成功后 `pushToast` 并强制重载（先收口旧轮询）；「保留最近」= 7/30/90/自定义（行内数字输入，Enter 严格校验、Esc/失焦静默退出），预计删除量由扫描缓存 `backups` 明细实时计算。
- 回归 `test/2026-11-15_存储空间统计/`；Windows 数据根、三主题视觉与真实清理后的后端连续性需实机验收。

## 29) 技能名称注入与检索

> 细则见 `AD/skills_search.md`（2026-11-19 新增）。

- **只注入名称**：主／子系统提示词的技能段只列名称（`skills_manager.build_skills_list` 按目录分组），不含 description；用途靠常驻工具 `search_skills` 检索 SKILL.md（名称/描述/正文），命中后 `read_skill` 读全文。无开关，不要恢复全量描述注入。
- **检索范围 = read_skill 可读范围**：两者共用 `tools_read._enabled_skills_catalog()`；执行后端读取失败即报错，不回退本机遍历。工具定义文案唯一来源 `skills_manager.SEARCH_SKILLS_DESCRIPTION/PARAMETERS`（主子共用）。
- 纯子串匹配，中文关键词搜不到英文正文；静态规则「关键词带英文词」不可删。`search_skills` 只回片段，不进已读白名单。
- 回归 `test/2026-11-19_技能检索/`。

## 30) 运行期模式切换通知（Rust 后端）

> 唯一权威 `backend-rust/src/runtime/mode_notice.rs`（2026-11-27 补齐 Python 旧语义）。

- **为什么必须有**：系统提示词首次受理后冻结，切换权限 / 执行环境 / 网络 / 运行模式不会重写它；变化必须以 user 消息注入对话，否则模型按冻结的旧规则静默降级。
- **两处注入点**：① admission（`runtime.rs`，`system_prompt::freeze` 之后、`guidance::admit` 之前）补发空闲期切换；② `runtime/agent.rs` 轮次起点 `apply_pending_host_modes` 应用排队切换后注入。**只在完整工具批次边界注入**，禁止插进 `assistant.tool_calls` 与工具结果之间。
- **基线去重**：metadata `runtime_mode_baseline` = 模型已知晓的模式。基线缺失（新对话 / 历史对话）静默对齐、不通知；**只有通知真正落库后才 `mark_known`**，因此同一变化只发一次。
- **文本同源**：通知正文由 `system_prompt/sections.rs` 的 `permission_section` / `execution_section` / `work_section` 渲染，与冻结提示词共用模板，**禁止另写一份规则文案**。
- **归一化复用** `web_status::preferences::effective_modes`（含 plan→readonly、readonly→sandbox 联动），**禁止在通知模块重复实现**。
- **消息形态**：`visibility: compact` + `starts_work: false` + `runtime_mode_notice: true`，`source` 仍按 kind 分 `permission_change` / `execution_change` / `network_change` / `work_mode_change`；前端靠 `visibility` 与 `runtime_mode_notice` 识别，无需改动。
- **已知边界**：Chat 模式（`allows_workflow()==false`）只在 admission 补发，运行中排队切换不注入；系统提示词 execution 段仍固定渲染 `sandbox`，direct 只在切换通知里表达。
- 回归 `test/2026-11-27_运行期模式通知/mode_notice_contract.rs`（经 `#[path]` 挂进 `mode_notice` 模块）。

---

## 体积统计（改完必跑）

```bash
python3 -c "print(len(open('AGENTS.md',encoding='utf-8').read()))"
```

> 阈值 **50,000 字符**：超过必须按文首「体积硬约束」拆分，禁止靠删约束或合并表述压数。

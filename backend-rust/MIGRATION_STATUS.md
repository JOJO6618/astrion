# Astrion 生产后端 Rust 迁移

## 2026-11-27 · 1.0.0 接手与验收约定

1. 1.0.0 是首个正式版。Rust 接管后单写 SQLite。原 Python 格式用作导入来源，不需要提供回退到旧版 App 的功能。下方历史记录中的“Python 再接管”保留为旧版本的验收记录。
2. 启动时提示用户显式迁移。旧会话在列表可见，点击时在会话区域显示“不兼容的对话记录”。迁移失败保留源数据，可重试。新建会话、打开和浏览旧条目均不自动迁移。保留外部 .astrion.zip 传输契约。
3. 正式验收从真实构建的 macOS App 启动，在独立数据目录与 Electron profile 上使用模拟模型事件流，经 UI 验证工具、下一轮请求、主子协作、审批、Chat 分支、旧数据迁移、关闭重启和损坏记录。检查服务 PID 的可执行路径、包内 build ID 和 HTTP 响应的进程身份。
4. 此轮合并提交已获授权，可在 migration/rust-backend 本地分阶段提交。前端性能修复合并为 f78005187。独立构建前端成功，三项监听/标签回归通过。接手前当前二进制目标 `cargo check --bin astrion-headless` 通过；测试目标缺失进程资源夹具。后续需复核整套 Cargo、HTTP、CLI 和实包。

### 11 月 27 日接手后的实测

1. 首次 arm64 1.0.0 目录包通过 Electron 实测。测试使用独立临时数据、独立 profile、本机 HTTP 模拟模型。启动时推迟迁移，点击旧 Agent 得到不兼容提示，手动迁移之后列表和正文恢复，旧 JSON 字节不变。
2. 长历史夹具共 617 条消息，原 JSON 为 26,958,085 字节。实际模型接到 read_file 结果并完成第二轮，重启后原会话可继续。该包一次工具响应到下一请求的本地回环耗时 47 ms。
3. 另一次实包测试验证命令授权、同批提问、主子协作和 MCP 返回图片。Host Web 五种启动方式与 CLI 均使用 Rust 进程。更新的代码还需统一打包验收。

## 上一阶段暂停时的历史记录

工作树分支 `migration/rust-backend`。本次暂停后仅整理文档。18 个可接收消息的子智能体均已收到停止开发、记录现状的指令；此前已终结的实例保留原有记录。

| 项目 | 已有证据与暂停点 |
| --- | --- |
| 正式路由与回归 | Web 165/271，headless 69/91。两套入口有重叠。对应版本的 Rust 回归 293 通过、0 失败、3 默认忽略，另有两个环境相关用例单独通过。详见下方「最近验收」。 |
| 当前源码与二进制 | `target-master/download-build.log` 记录了含下载、inline、登记媒体和 MCP 管理路由的成功构建。现有 debug 文件修改时间为 11 月 27 日 00:17:39。之后的 `resume-check.log` 在 02:27 记录三个进程资源编译错误。负责人已记录三处源码修正。最后一次重建没有生成 `resume-build.log`，因此修正后的编译状态待核。记忆迁移测试夹具已确认在磁盘上。 |
| 生产接线 | 暂停前核对了当前 `gateway.rs`、`host_web.rs`、`main.rs`、`lifecycle.rs`，其中仍缺进程资源的路由挂载、启动配置与退出回收。提问与计划、子智能体、MCP 主/子工具、订阅额度、版本恢复及会话传输各自的接线缺口见下表。 |
| 用户数据与发行 | 账户媒体等分项已有 Python/Rust 接管证据。完整旧数据往返与最终 macOS `Astrion.app` 的构建、包内 Rust PID、业务操作及重启仍待验。当前常规入口仍默认 Python。Windows 原生工具续轮延迟仍待实机核验。 |

### 本次模块交接

下列记录分别列出已落盘实现、既有验证证据及尚未验证的行为。历史复核按压缩记录整理，源码与日志的实测状态以对应模块记录为准。

| 范围 | 交接记录 |
| --- | --- |
| 历史要求、性能、桌面包 | [历史复核](handoff/history.md) · [长步骤列表](handoff/performance.md) · [桌面包](handoff/bundle.md) |
| 会话与文件 | [普通会话](handoff/conversations.md) · [Chat→Agent](handoff/chat.md) · [文件及媒体](handoff/chat-files.md) · [会话传输](handoff/transfer.md) |
| 工具与任务 | [进程资源](handoff/process.md) · [提问与计划](handoff/interactions.md) · [子智能体](handoff/sub-agents.md) · [项目记忆与 MCP](handoff/mcp-memory.md) |
| Web、Gateway 与宿主 | [入口盘点及 Git](handoff/gateway.md) · [认证与管理](handoff/web-auth.md) · [Web 资产与预览](handoff/web-assets.md) · [宿主交互](handoff/host-interaction.md) |
| 模型及版本恢复 | [Responses 与订阅登录](handoff/responses.md) · [版本恢复与 OAuth 接线](handoff/responses-wiring.md) · [提供商与额度](handoff/providers.md) |

## 重启后恢复集成（历史）

已恢复执行。`target-master/recovery-check.log` 和 `recovery-build.log` 记录重启后第一次成功集成编译。`target-master/download-build.log` 记录包含三个文件读取入口和 MCP 管理路由的后续成功构建。此后仍有并行改动，进程资源和当前合并工作树需要重新构建。串行全套第一次重跑在编译测试夹具时中断，详见 `target-master/recovery-suite.log`。Web 165/271、headless 69/91 仍是最近一次完整核对的基线。正常 macOS 应用与旧数据的最终验收仍在待办中。

## 重启前暂停断点（历史）

用户要求收尾暂停。所有子智能体状态已核对，运行数为 0。最后通过完整回归的阶段为下述「账户媒体与 headless 工作区接口」（Web 165/271，headless 69/91）。之后的并行改动保存在工作树，尚未通过统一编译与回归，未计入这组数字。最近一次集成诊断见 `target-master/stage-check2.log`，涉及 Chat 交接签名与可见性、版本恢复模块和子智能体注册表。检查日志记录的是该次编译的源码快照。最终 macOS `.app` 完整实测仍待完成。

## 用户确认的最终交付与验收条件

- Host Web、CLI/headless 和 Electron 的正常入口均须使用 Rust 后端，逐项验证应保留的生产行为。路由覆盖和行为验收分别记录。
- 在隔离数据上完成自动回归、真实入口与跨进程测试。验证 Python 写入、Rust 接管和重启、Python 再接管，核对会话 ID、分支、冻结提示词、工作区归属与未知字段。
- 构建 macOS `Astrion.app` 并启动该产物，核对运行进程的可执行文件确实来自包内 Rust 后端。在应用中完整验证工作区、会话、任务、工具、审批、媒体、设置、子智能体以及关闭后重启恢复。记录产物、测试证据、失败、忽略与平台待验项目。
- 关键阶段简报后继续执行。全部约定交付与验收完成后再总汇报。用户明确暂停、停止，或确有必须由用户处理的阻塞时说明当前状态。

## 最近验收 · 2026-11-26 账户媒体与 headless 工作区接口

Web **165/271**，headless **69/91**。路由清单 `--check` 已通过，剩余 106 和 22 项。`media_roundtrip.py` 验证 Python 上传 → Rust 读取、上传 → Python 接管 → Rust 重启，以及类型、大小、认证、未知字段、分支、草稿与交接快照引用的回收行为。媒体读取的 HTTP 缓存头严格匹配 `private, no-store`。纯 headless 的 Chat 媒体读取为 404。

`route_inventory_http.test.mjs` 实测了工作树查询、变更与检查、技能和状态五项在 Web 登录后的业务响应，并逐项对照 Python headless 的匿名与 Bearer 响应。Rust 套件 293 通过、0 失败、3 默认忽略，日志 `target-master/media-host-suite.log`。单独提供真实前端产物和 Flask 解释器后，两个有环境前提的忽略项均通过（`target-master/media-host-extra.log`）。剩余一项为 `providers.rs` 的忽略文档示例。默认后端仍为 Python，最终 `.app` 待验。

## 2026-11-26 订阅设置

Web 已挂载 **163/271（60.1%）**，headless 已挂载 **64/91（70.3%）**，分别剩余 108 和 27 个 Python 主要 API 方法与路径。Web 新增 `GET /api/codex/settings` 与 `POST /api/codex/settings`。清单已写入并通过 `--check`。共用 Rust 二进制和串行测试套件验证通过，**293 通过、0 失败、3 忽略**（`target-master/codex-settings-suite-v2.log`）。默认后端仍为 Python。

- 设置与实际 OAuth 传输使用相同的代理解析顺序。模型目录刷新使用全局客户端版本作为版本发现失败时的回退。次账号测试检查本地上游收到的版本及请求头，确认不会把其他版本的 ETag 发给上游。
- 合成数据上的真实 Rust Web 登录、Cookie/CSRF、写入、清空、重启和 headless 404 验收通过。Python 生产蓝图在独立回环监听器上以夹具管理员身份读取并修改同一文件，Rust 重启后读回。双方保留模型缓存和未知字段，文件权限为 0600。参见 `test/2026-11-26_Rust订阅设置/codex_settings_roundtrip.py`。
- 全套首次加入次账号测试时，Chat 取消测试的模拟上游向已断开的流写入触发 BrokenPipe。夹具现在仅在延迟流的尾段允许对端断开，其余写入错误仍会失败。原停止、落盘和门闸断言连续通过 5 次，再运行全套通过。

## 上次验收 · 2026-11-26 订阅账号发现

Web 已挂载 **161/271（59.4%）**，headless 已挂载 **64/91（70.3%）**，分别剩 110 和 27 个 Python 主要 API 方法与路径。相对上一阶段新增 Web `GET /api/codex/accounts`。已重建共用二进制，写入 TSV 后 `route_inventory.py --check` 通过。Rust 串行全套 **292 通过、0 失败、3 忽略**（`target-master/accounts-discovery-final-suite.log`）。默认后端仍为 Python。

账号列表和模型目录共用账号发现逻辑。旧次账号尚未写入索引时，目录可以选中其模型，读取后索引仍不存在。真实 HTTP 验收以合成账号逐字段对照 Python 的账号列表、顺序和缓存摘要，确认未登录 GET 返回 401、headless 返回 404。另在共用二进制上复验刷新接口认证和两项本地订阅上游测试，覆盖同账号历史、换账号、浏览器 Chat 及定时任务。

## 上次验收 · 2026-11-26 订阅模型与运行引导

正式清单为 Web **160/271（59.0%）**、headless **64/91（70.3%）**，各剩 111 和 27 个 Python 主要 API 方法与路径。`route_inventory.py --check` 通过。Rust 串行全套 **289 通过、0 失败、3 忽略**（`target-master/oauth-v5-full.log`）。增强的真实 Gateway、浏览器登录及定时任务 HTTP 验收 **2/2**（`target-master/oauth-v6-web.log`）。默认后端仍为 Python。

- 新增 Agent 会话复制、传输目的工作区列表、项目容量、工作区 Agent 运行引导、Web 高速模式设置和 headless 健康查询。CLI 的 `Ctrl+G` 等服务端接受引导后再清理输入。真实监听器验证双工作区、并发复制、旧引导和新引导的顺序、停止、重启及草稿恢复。Chat 草稿和分支复制经过 Python 写入 → Rust 修改和重启 → Python 接管，历史交接状态由 Python 的实际结果校验。
- 已连接的订阅账号可生成模型回复。Gateway 的同一会话先用同账号发送两次，再切换到同模型名的另一账号，验收覆盖冻结提示逐字保留、同账号加密历史回放、换账号隔离、公开历史脱敏和 Fast 能力判定。浏览器 Chat 与定时调度请求也到达本地订阅上游。已有会话计划继承 Fast，新会话默认关闭。公共 YAML 保持 Python 可接受的配置字段。
- 目标、工作流和工具审批审核走共享模型配置。每次独立目标/工作流审核使用单独会话 ID，工具续轮及 401 重试复用该 ID，Fast 关闭，费用归原会话。模型协议对照真实 Python 请求构造，供应商回归覆盖轮换、一次 401 重发和连接失效。刷新管理操作另经正式二进制核对 Web 会话与 CSRF、headless 的注册范围及访问限制。真实订阅额度消耗和云端对 `priority` 的接受度待实测。
- CrossPet 在没有账号索引的旧安装上，能从唯一旧凭据获取合成额度。凭据损坏时保留上次样本并标为 stale，确认登出时清空，关闭及换账号后的迟到结果不会写回。已通过原 Host 配额回归。
- macOS arm64 本地热缓存启动基准以首次带鉴权的 `/api/runtime/sessions` 返回 200 和有效列表为终点。每组预热一次，交替各测 7 次。12 个对话的 Python 3.11 / Rust debug 中位数分别为 **1070.8 / 19.3 ms**，240 个对话为 **1050.0 / 32.7 ms**。两档各 14 次请求的总数与合成数据一致；另各做 3 轮强校验。Rust 测量二进制 SHA-256 以 `9b4f3e8c7417` 开头。详见 `test/2026-11-26_Rust启动性能/` 的业务结果，发行包、冷启动和 GUI 首帧仍待实测。

待迁移范围包括无工作区 Chat 引导、未消费引导进入审批输入、真实子智能体执行、预览服务、检查点生成/恢复、传输写事务、订阅账号登录和跨平台发行验收。

## 上次验收 · 2026-11-26 桌面与账户设置

Web 已挂载 **155/271（57.2%）**，headless 已挂载 **61/91（67.0%）**。分别剩余 116 和 30 个 Python 主要 API 方法与路径。清单写入后 `--check` 通过。Rust 串行全套 **263 通过、0 失败、3 忽略**，见 `backend-rust/target-master/desktop-settings-suite-v2.log`。默认后端为 Python。

- 桌面桥 12 个方法在两个入口注册，模拟壳 HTTP 4/4 通过。壳向 Rust 传递构建编号、平台、架构、策略协议。macOS 宿主机上的启动回归 3/3 通过，覆盖 Python 旧数据、关闭、端口复用和重启。沙箱首次运行因发送退出信号被拒绝而失败。真实安装更新及 Windows 原生桌面待验收。
- 待办和 CrossPet 通过同一生产二进制的真实监听器。修复空账户策略在首个模型请求前触发 panic 的回归。另已验证禁用待办类别后，上游模型实际看不到两个待办工具。CrossPet 的订阅额度属后续批次。
- Chat 草稿和复制通过 Python → Rust → 重启 → Python 双向验收。子模型设置 HTTP、账户偏好并发、历史消息和回顾导出均通过。订阅目录刷新使用本地合成凭据对照缓存、轮换与失败处理。后续的配额及引导、Agent 复制、项目容量仍需重新构建验收。

## 上次验收 · 2026-11-26

`migration/rust-backend` 已挂载 Web **133/271（49.1%）**、headless **47/91（51.6%）**，尚余 138、44 个 Python 主要 API 方法和路径。路由清单已重新生成，随后 `--check` 退出 0。Rust 全套串行 **246 通过、0 失败、3 忽略**，日志 `backend-rust/target-master/account-review-suite.log`。默认后端为 Python。

账户设置三个真实 HTTP 测试均通过，覆盖 Chat 与 Agent 并发写不同字段、旧未知字段、损坏数据保护、设置与记忆的 Python 返回值。原先 Agent GET 引发的丢字段已通过原失败用例复验。会话消息经过空闲、流式中和终态的 Python 对照，回顾预览与导出、检查点读取、搜索、统计、费用、供应商和浏览器偏好均已做真实监听器验收。预览服务及原生浏览器桥仍待实现。下方保留旧阶段的历史数字。

## 历史暂停点 · 2026-11-25 17:16 UTC+8

分支 `migration/rust-backend`。Web 已挂载 **114/271（42.1%）**，headless 已挂载 **45/91（49.5%）**。分母分别是 Python 对应入口的主要 HTTP 方法与路径，同一路由可出现在两个入口。剩余主要 API 分别为 157 和 46。路由基线 `--check` 通过。默认后端仍为 Python。

本阶段六位协作者均已交付。依用户要求在此暂停。没有推送、发布或切换默认后端。

### 最终验证

| 范围 | 结果 |
|---|---|
| Rust 串行全套 | **234 通过、0 失败、3 忽略**。库测试 191 通过，二进制测试 5 通过，其余测试组分别 10、7、11、10 通过。最终本地日志 `backend-rust/target-master/stage-final.log`。 |
| Chat 原位读写 | Python 建立双分支，两次启动 Rust Web，分别编辑并从指定分支续写，最后 Python 接管保存。双向重放操作核对回执、文件字节和修改时间，未知元数据保留。正常监听器通过，Chat 专属库测试 8/8。 |
| 供应商管理 | 正常 Web 监听器覆盖登录、CSRF、连接、模型发现、刷新、响应脱敏与文件权限。Web、headless 均拒绝仅持 Gateway Bearer 的管理请求。供应商测试 24/24。 |
| Gateway 与工作区 | 正常 Gateway 模型和任务 2/2；额外工作区和 CLI 3/3。仅正文指定工作区 B 会创建到 B，头与正文冲突返回 404，两侧列表均不增加。旧注册快照无法在同 ID 新目录创建会话；主侧定向复验通过。 |
| 模型窗口 | 非固定模型 3/3。本地 SSE 上游捕获了实际工具续轮、缺字段、临界输出预算和重启恢复行为。 |
| 长日志 | Flask 分页与 HTTP 11/11，真实 Pinia 与 Vue 组件的轻量宿主测试 12/12。旧后端返回 20 万条时，该次显示 200 行，实时窗口最多 400 条。类型检查与前端构建通过。没有测真实浏览器绘制时间。 |
| 目标模式 | 两个注册工作区、旧 Python 格式会话、本地模拟模型、真实 Web/headless 进程。通过了工作区隔离、同会话互斥、审核续轮、用量、轮次暂停、同 run ID 恢复、陈旧控制返回 409、Web bootstrap 和重启后保持暂停。任务终态后立即重开数据目录的两个原用例 2/2 通过。 |

### 协作者与写入范围

| 实例 | 本阶段所有权及状态 |
|---|---|
| Chat运行时 | `backend-rust/src/chat_mode/`、Chat Rust 契约，已交付。 |
| 供应商管理 | `backend-rust/src/providers.rs`、`src/providers/` 与供应商契约，已交付。 |
| 任务内核二轮 | runtime 生命周期、goal reviewer、目标控制装配和用量，已交付。 |
| 运行路径 | 子智能体长日志 store、详情组件行为及前端回归，已交付。 |
| Gateway验收二轮 | Gateway 正常入口和模型覆盖夹具，已交付。 |
| Responses接线二轮 | 工作区注册、删除、同 ID 重建和 CLI 注册夹具，已交付。 |
| 包体核查、ChatRust二轮 | 保留空闲，分别提供长日志历史基准、模型窗口预算与 HTTP 契约。 |
| ResponsesRust二轮、Web认证二轮、前端资产二轮 | 保留空闲或软停止，分别为 Responses、Web 认证和前端资产。 |

主侧负责生产入口装配、会话创建工作区校验、路由清单、真实监听器与跨语言验收、全套回归及本文件。

### 已知遗留

- 供应商的 models.dev 在线缓存刷新、refresh-all 包含 Codex 账号等差异见 `backend-rust/src/providers/README.md`。
- 目标模式仍待主动审核的独立只读取证执行器、子任务用量、定时任务目标入口及网络退避重试端到端验收。
- 实测环境为 macOS arm64。Windows guardian、Linux 原生沙箱、桌面 GUI、真实 OAuth 与优先服务档位、分发性能尚待验收。
- 11 月 25 日续接时按原端口、工作树可执行文件及两份夹具锁文件重新核对，已回收 12 个旧 Gateway 和 2 个早期目标试验进程，14 个原监听端口均已复查。

### 续接分工（11 月 25 日晚）

主侧负责会话搜索、上下文用量、headless 配额、生产装配及路由基线。Gateway验收二轮独立验证查询；Chat运行时负责账户设置和记忆；供应商管理负责 models.dev 缓存；Web认证二轮负责浏览器偏好；任务内核二轮负责会话历史用量与费用；Responses接线二轮负责版本记录。上面的百分比与测试结果是上一阶段验收基线。

### 复跑

从工作树根执行。`target-master` 是本阶段共用的本地编译目录。

```sh
export CARGO_TARGET_DIR="$PWD/backend-rust/target-master"
export ASTRION_HEADLESS_BIN="$CARGO_TARGET_DIR/debug/astrion-headless"
export ASTRION_LEGACY_TEST_PYTHON=/opt/homebrew/bin/python3.11
cargo build --locked --offline -j1 --manifest-path backend-rust/Cargo.toml --bin astrion-headless
cargo test --locked --offline -j1 --manifest-path backend-rust/Cargo.toml --no-fail-fast -- --test-threads=1
/opt/homebrew/bin/python3.11 -B test/2026-11-24_Rust后端/chat_web_roundtrip.py
/opt/homebrew/bin/python3.11 -B test/2026-11-24_Rust后端/provider_web_roundtrip.py
node --test test/2026-11-24_Rust后端/gateway_e2e.test.mjs test/2026-11-25_Rust模型窗口预算/gateway_model_budget.test.mjs
node --test --test-concurrency=1 test/2026-11-24_Rust后端/gateway_workspaces_e2e.test.mjs
PYTHONPATH=. /opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-24_子智能体步骤详情性能 -p '*test.py' -v
node --test --test-concurrency=1 test/2026-11-24_子智能体步骤详情性能/subAgent_activity_behavior.test.mjs
/opt/homebrew/bin/python3.11 -B test/2026-11-24_Rust后端/route_inventory.py --check
```

### 版本查询接入后的中间验收

2026-11-25 晚续接。本工作树已装配检查点状态、列表与详情。清单实测 Web **130/271（48.0%）**、headless **47/91（51.6%）**。详情按 Flask 无符号整数序号匹配，负数、加号和非数字段在真实监听器返回 404，写入和恢复仍待迁移。此时全套 Cargo 串行回归 **240 通过、0 失败、3 忽略**，日志 `backend-rust/target-master/integration-round10.log`。账户共享配置事务和新增回归仍在开发，该结果只对应本次编译。

真实监听器已复跑供应商、跨进程 Chat 重放、会话搜索缓存失效、工作区汇总、当前上下文、配额、单会话账本及浏览器偏好。工作区汇总和费用与 Python 返回结构对照，通过旧文件字节检查。浏览器偏好验证动态工作区注册和 Web/headless 权限。版本负责人已完成首轮 Web/headless 2 项检查，正在补只读边界。下一批查询由任务内核二轮衔接会话消息；Web认证二轮核对 CrossPet 状态生产与配置的依赖。Chat运行时继续负责偏好事务，Gateway验收二轮独立验证跨入口并发写入。尚未更新正式路由基线。

### 消息查询与回顾的真实入口验收（11 月 26 日）

在 130/271、47/91 阶段构建之后，又挂入三个 Web 方法。实际 Web 监听器的 `/messages` 与 Python 对照覆盖空闲、模型流式输出中、任务结束三个状态。已确认运行中 bootstrap 能看到片段，`/messages` 只显示已提交消息，终态再出现完整助手消息。回顾预览及 Markdown 导出的真实登录、授权、限行、旧工具消息形状和 Unicode 计数对照通过。Rust 独立预览服务尚缺，回环访问的预览 token/base 仍待接入。检查点旧记录缺跟踪方式的修复已重建，补充的越界引用、缺序号、跨工作区、GET/HEAD 用例 2/2 通过。models.dev 的供应商定向回归 27/27 通过。

账户并发独立 HTTP 已发现旧 Agent GET 会删掉 `legacy_extension`，损坏的 `personalization.json` 也可能被默认为空。Chat 运行时负责人正在修改 Agent 与 Chat 共用存储，须重建后复测。CrossPet 的观察器与任务动作已在库中接线，尚待真实运行时适配和 Web/headless 生产入口验证；供应商负责人正在接续已连接 Codex 账号的目录刷新。

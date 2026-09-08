# Client ↔ Gateway 链路审核报告（前端/CLI 消费面）

**审核日期**：2026-09-08
**审核对象**：`<repo>`（Flask 后端 + Vue3 Web + React/Ink CLI）
**审核方式**：只读。grep/符号定位 + 精确行段阅读，禁止整文件通读。行号以本次审核时代码为准。
**背景**：验证设计文档「①②③ 链路（Client ↔ Gateway ↔ Runtime）已贯通」四个实例声明是否属实。
**结论前缀**：✅属实 / ⚠️部分属实 / ❌不属实 / 「未验证」

---

## 1. 每项声明的验证结论 + 代码证据

### 声明 A：全部任务创建入口已收敛到 runtime_service.create_task(ctx)

**结论：✅ 属实（主 Run 受理层面）。**

**证据：**
- 唯一的 `task_manager.create_chat_task` 生产调用点收敛在网关内：
  - `server/runtime/service.py:44` — `return task_manager.create_chat_task(ctx, conversation_id=conversation_id)`
  - 全仓 `grep '\.create_chat_task('` 除以下之外无生产调用：`test/test_runtime_service.py:164`（测试用，验证 None ctx 抛错）、`.claude/worktrees/stoic-matsumoto-ac8ca5/`（旧 worktree，不在当前主工作树）、`_experiments/verify_conversation_level_terminal.py:83,93`（实验脚本，非生产入口）。
- `runtime_service.create_task(ctx)` 调用点共 6 处，均经公开入口：
  - `server/tasks/api.py:187`（Web 创建任务路由）
  - `server/api_v1.py:335`（API v1 消息路由，`@api_token_required`）
  - `server/workflow_runtime_api.py:192`（workflow 激活）、`:269`（workflow notice 派发）
  - `server/chat_flow_task_main.py:618`（多智能体完成通知派发 `_dispatch_completion_user_notice`）、`:1424`（多智能体闲时派发 `dispatch_ma_idle`）
- `RuntimeContext` 三层构造符合契约：适配层直接构造两个点（`server/tasks/api.py:170`、`server/api_v1.py:323`，principal 经 `principal_from_session_snapshot` 由认证后的 session 构造）；内部通知链/工作流/多智能体用 `RuntimeContext.from_terminal`（`server/runtime/context.py` 定义，身份取自 terminal/workspace），不带客户端可提交的门闸 token 等内部指令。
- 执行链 `create_chat_task → 线程 → _run_chat_task → run_chat_task_sync → process_message_task → handle_task_with_sender` 的调用均在运行线程内部（`server/tasks/models.py:991` 调用 `run_chat_task_sync`；`chat_flow_task_main.py:664/1899/2500` 在运行线程内 spawn 子分支），不存在独立于网关的直拉过程。

**说明/例外：**
- 子智能体任务、传统后台命令是**独立的派生工作者实体**，不走 `runtime_service.create_task`（被 `server/chat_flow_task_main.py:2642` 注释明确说明「与传统后台任务完全分离，避免两者竞争 create_chat_task 的单工作区互斥」）。这与 `docs/runtime_protocol.md` §2「子智能体/后台命令是主 Run 派生的后台工作者」一致，不算漏网。

### 声明 B：server/chat/approval.py 六个路由全部转调 runtime_service

**结论：✅ 属实。**

**证据（`server/chat/approval.py`，六路由全部经公共入口）：**
- `list_pending_user_questions` → `runtime_service.list_pending_approvals(..., kind="question")`（`:56`）
- `answer_user_question` → `runtime_service.resolve_approval("question", ...)`（`:71`）
- `list_pending_plan_approvals` → `runtime_service.list_pending_approvals(..., kind="plan")`（`:101`）
- `answer_plan_approval` → `runtime_service.resolve_approval("plan", ...)`（`:116`）
- `list_pending_tool_approvals` → `runtime_service.list_pending_approvals(..., kind="tool")`（`:145`）
- `decide_tool_approval` → `runtime_service.resolve_approval("tool", ...)`（`:161`）

**仪表验证：** `server/runtime/service.py` 中 `resolve_approval`/`list_pending_approvals` 均作了 kind 路由 + 权限/参数校验，再委托给 `server/state` 下的三个 manager。

**例外观察（非 Web 路由，不算入口违规）：**
- `modules/auto_approval_service.py:89` 内部 `tool_approval_manager.decide(...)` 直调，是**自动审批 agent**（执行侧自动裁决），仅被 `server/chat_flow_tool_loop.py:894,1166` 调用（运行循环内）。属于 Execution Plane/Agent Runtime 侧机制，不经客户端入口，但若后续要统一裁决语义可考虑让其复用 `resolve_approval`。

### 声明 C：公共查询入口 list_runs / get_task / get_task_events / list_pending_approvals / list_sessions / get_session_history

**结论：⚠️ 部分属实（run.* 与 approval.* 已供 HTTP，session.list/history 无传输暴露）。**

**证据：**
- `run.list`：HTTP 暴露 `/api/tasks` GET（`server/tasks/api.py:33-44` → `runtime_service.list_runs`）。
- `run.get`：HTTP 暴露 `/api/tasks/<task_id>` GET（`server/tasks/api.py:205` → `runtime_service.get_task`）。
- `run.events`：HTTP 暴露 `/api/tasks/<task_id>?from=`（`server/tasks/api.py:220` → `runtime_service.get_task_events`，响应透传 `window_start`）。
- `approval.list`：HTTP 暴露 approval.py 三个 pending 路由。
- **`session.list` / `session.history`：❌ 仅进程内服务方法，无任何 HTTP 路由调用。**
  - `grep` `list_sessions|get_session_history` 仅命中 `server/runtime/service.py` 定义 + `test/runtime_standalone_checks.py:273/282/290/300`（测试）。
  - Web/API v1 的会话查询走的是**旧链路**，未复用公共入口：
    - `server/conversation.py:571` `GET /api/conversations` 直接 `terminal.get_conversations_list(...)`
    - `server/api_v1.py:351` `GET /workspaces/<id>/conversations` 直接 `conv_dir.glob("conv_*.json")`；`:385` `GET .../<conv_id>` 直接 `json.loads(_conversation_path...)`
  - 即：`list_sessions`/`get_session_history` 是「为 CLI/定时任务预留的进程内直调入口」，但当前 Web/CLI 实际消费会话列表/历史仍在走旧 Web 路由。

### 声明 D：事件协议 task 级 idx/offset + meta.window_start 缺口；Web 与 CLI 客户端缺口处理

**结论：✅ 属实的服务端/Web 实现；⚠️ CLI 缺口处理不完整（见差距清单）。**

**证据：**
- 服务端：`server/runtime/service.py:get_task_events` 返回 `meta.window_start`（缺口水位）；`server/tasks/api.py:220-225` 从 `ev_meta` 取 `window_start` 透传 HTTP。
- Web：`static/src/stores/task.ts`
  - 轮询 `/api/tasks/<id>?from=<offset>`（`:191`）
  - 读 `data.window_start`（`:264`）
  - 缺口检测 `if (fromOffset > 0 && fromOffset < windowStart)` → 发合成事件 `event_window_gap`（`:263-285`）
  - `static/src/app/methods/taskPolling/lifecycle.ts:395-408` `case 'event_window_gap'` → 跳过重建期后 `this.fetchAndDisplayHistory({ force: true })` + toast（i18n `stores.eventWindowGap*` 在 `locales/zh-CN/stores.ts:63-64`/`en-US/stores.ts:63-64`）。**缺口 → 快照对账链路真实存在。**
- CLI：`cli/src/App.tsx:491-503` `pollTask` 读 `result.window_start`，`if (offset > 0 && offset < windowStart)` → 提示「部分实时输出已被裁剪…以最终结果为准」并 `offset = windowStart` 对齐续读。**检测+对齐窗口存在，但仅提示不对账（见 §3 差距 2）。**

### 声明 E：CLI 当前是「Web API 消费者」（fetch 127.0.0.1:8091 + cookie/CSRF）

**结论：✅ 属实（且依赖度比声明更重）。**

**证据（`cli/src/api.ts`）：**
- 认证：`ensureConnected` 调 `/api/csrf-token`（`fetchCsrf`）、`/host-login`（`hostLogin`）；请求头携带 `Cookie` + `X-CSRF-Token`（`request`/`captureCookies`，`:160-215`）；401 时自动 `fetchCsrf`+`hostLogin` 重试。
- 直连目标：`createDefaultApiClient` 默认 `http://127.0.0.1:${port}`（`AGENTS_API_PORT || WEB_SERVER_PORT || 8091`）。
- 若连不通，`startServer` 直接 `spawn('python3', ['-m', 'server.app', ...])`（`:52-75`）拉起完整 Flask/SocketIO Web 服务；`server/app.py` → `app_legacy.run_server`（含 socketio）。

---

## 2. 发现的漏网路径 / 不一致清单

| # | 类型 | 描述 | 影响 | 代码证据 |
|---|---|---|---|---|
| 1 | **双轨会话查询** | `session.list/history` 公共入口 `list_sessions`/`get_session_history` **无任何 HTTP 路由调用**；Web（`server/conversation.py:571`）与 API v1（`server/api_v1.py:351/385`）会话查询仍在直读磁盘/terminal，绕过公共入口 | 会话能力未统一到协议面；「新客户端可经公共协议查询会话」目前仅能靠进程内直调 | protocol §4 表 `session.list/history`（`runtime_service.list_sessions/get_session_history`）+ `grep` 无 HTTP 调用方 |
| 2 | **API v1 会话路由旧实施** | `GET /workspaces/<id>/conversations`、`GET .../<conv_id>` 用 `Path.glob("conv_*.json")` / `json.loads` 直读，独立于 `runtime_service` 且未做属主/工作区一致性校验的同构去重 | 与公共 `list_sessions` 载荷/校验不一致；多端字段未统一 | `server/api_v1.py:351-404` |
| 3 | **自动审批直调 manager** | `modules/auto_approval_service.py:89` `tool_approval_manager.decide(...)` 绕过 `resolve_approval` | 统一裁决语义分叉（审批条目来源/回调/审计若日后收敛到公共入口会遗漏该路径） | `modules/auto_approval_service.py:72-96`，仅被 `chat_flow_tool_loop.py:894/1166` 调用 |
| 4 | **旧 worktree/实验脚本直调** | `.claude/worktrees/stoic-matsumoto-ac8ca5/server/…` 多处 `task_manager.create_chat_task`；`_experiments/verify_conversation_level_terminal.py:83,93` | 不在当前主工作树/非生产；仅提示清理，不构成本机违规 | grep `.create_chat_task(` 结果 |
| 5 | **run.guide / queue 端点存在但 CLI 未消费** | `/api/tasks/<id>/runtime_guidance`（tasks/api.py:282）、`runtime_queue`（:310/338/360）已有 HTTP 路由，但 CLI `api.ts` 无对应方法 | CLI 作为待实现「Runtime Client」缺失 run.guide/追问队列能力 | `server/tasks/api.py:282-364` vs `cli/src/api.ts` |

---

## 3. CLI 成为「独立 Runtime Client」的差距清单

| # | 差距 | 证据 | 说明 |
|---|---|---|---|
| 1 | **依赖 Web 专属认证流程** | `cli/src/api.ts:48-90` 用 `/api/csrf-token` + `/host-login` + Cookie/CSRF 头 | 协议 §6 明确的 Web 适配层认证；非通道无关 Token。独立客户端不应走 CSRF。 |
| 2 | **缺口只对齐不重同步** | `cli/src/App.tsx:497-502` 检测 `offset < windowStart` 仅提示并 `offset=windowStart`；**未触发 §5.4 快照对账**（Web 走 `fetchAndDisplayHistory({force:true})`） | 协议 §5.2 要求缺口必须「走重新同步(§5.4)」；CLI 会丢被裁剪的中间内容，仅以「最终结果为准」凑合。 |
| 3 | **仅支持 tool 审批，缺 plan/question** | `cli/src/App.tsx:505-511` 只处理 `tool_approval_required`；`api.ts:244` 只打 `/api/tool-approvals/<id>/decision` | approval.resolve 的 plan（submit_plan）/question（ask_user）能力 CLI 不可用。 |
| 4 | **自拉起完整 Web 服务** | `api.ts:startServer` spawn `python3 -m server.app`（SocketIO Web 全量） | 协议 §6「禁止每个客户端各起一份独立状态 Runtime 却宣称共享 Gateway」的边界上；本地单机场景可用，但非「只连公共 Gateway」。 |
| 5 | **依赖大量 Web-only 端点完成配置/调研** | `api.ts` 中 `/api/status`、`/api/conversations*`、`/api/model`、`/api/thinking-mode`、`/api/permission-mode`、`/api/execution-mode`、`/api/tool-settings`、`/api/path-authorization`、`/api/host/workspaces` 等 | 这些不受 protocol Commands/Queries 覆盖；独立客户端需等价 run.*/session.* 能力或明确这些为 Web adapter 私有。 |
| 6 | **无 run.list（Run 发现）消费** | `cli/src/api.ts` 未调用 `/api/tasks` GET 或 `list_runs` | 主 Run 发现能力（审核 F1 已落在服务层）CLI 未使用；不利于 A/B 观察场景。 |

---

## 4. 总体判断

**「①②③ 链路（Client ↔ Gateway ↔ Runtime）已贯通」：⚠️ 部分成立，未达到「新客户端可不复制 Web 专属流程直接接入」的程度。**

- **服务端收敛度**：高。主 Run 创建的 6 个入口全部经 `runtime_service.create_task`；approval.py 六路由全部经 `resolve_approval`/`list_pending_approvals`；run.list/get/events/cancel/guide/queue 均有 HTTP 公开路由并透传 `window_start`。声明 A、B、D(服务端/Web)、E 属实。
- **Web 消费面**：已消费公共协议面的 HTTP 端点（task 轮询 + approval + 缺口快照对账），基本属「公共协议能力消费方」。
- **主要缺口集中在「会话查询未传输暴露」与「CLI 独立性」两点**：
  1. `session.list/history` 与 `session.create/load` 显式命令**没有 HTTP 传输暴露**，Web/API v1 仍在走旧会话路由——协议表虽标「✅/⚠️」但事实是**只有进程内直调入口**。
  2. CLI 仍是「Web API 消费者」：cookie/CSRF/hostLogin + 自拉起完整 Web 服务，仅 tool 审批、缺口不重同步、依赖大量 Web-only 端点。
- **结论**：Gateway（服务层公共入口）本身已基本贯通且 Web 消费面已对齐；但「新客户端不复制 Web 专属流程即可接入」的**完整性目标未达成**，卡在 (a) 会话查询/创建的传输暴露、(b) CLI 认证与启动方式的 Web 耦合、以及 (c) 审批三类型覆盖与缺口快照对账。严格意义下应表述为「服务端公共入口贯通完成，客户端侧仍处『Web adapter 消费者』过渡态」。

---

### 附：未验证项
- 未在本次审核中执行真实运行验收（如 ApprovalWaitChainTest / ProtocolSmokeChainTest 的运行通过性），仅静态符号与行段核对；测试伪实现（`test_runtime_service.py` 曾把 `_run_chat_task` 替换为 lambda）的历史问题本报告未复验。
- HTTP 端点归属（哪些路由挂载到哪个 blueprint / app_legacy）的注册细节未逐一核对，仅核对路由定义文件内部映射。
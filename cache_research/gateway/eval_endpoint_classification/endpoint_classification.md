# Astrion `server/` 196 个 API 端点职责分类盘点报告

> 任务：运行时边界整理（Gateway/RuntimeService）前置普查 —— 回答「196 个端点里到底有多少需要动」。
> 分析方法：只读代码分析（grep 全部 `@<bp>.route` 装饰器后逐个读取端点函数体），未修改任何文件。
> 统计口径：以 Flask 路由注册（Methods 合并计数）为准，共 **196** 个，与已知分布完全一致（逐文件核验过）。

---

## 0. 结论速览（先给答案）

| 类别 | 数量 | 占 196 的比例 | 是否要动 |
|---|---|---|---|
| **T1 任务受理**（创建/启动一轮 Agent 任务） | **3** | **1.5%** | ✅ 必须迁移到 RuntimeService 公共入口 |
| **T2 任务控制**（取消/停止/审批回答/提问回答） | **13** | **6.6%** | ✅ 必须迁移（收敛为公共控制接口） |
| **T3 任务观察**（轮询状态/事件流 idx/offset） | **14** | **7.1%** | ⚠️ 可不迁移：只读 REST 语义，HTTP 适配层保留即可 |
| **C CRUD**（会话/工作区/配置/文件/页面） | **131** | **66.8%** | ❌ 不动 |
| **S 状态查询**（系统状态/git/docker/usage） | **20** | **10.2%** | ❌ 不动 |
| **A 认证管理**（登录/token/用户/API 用户） | **15** | **7.7%** | ❌ 不动 |
| **合计** | **196** | 100% | — |

**核心答案：真正需要「Gateway 化」的最低必要集合 = T1 + T2 = 16 个端点，占 196 的 8.2%。**
若把任务观察（T3，共享事件流读取协议）也纳入公共接口设计边界，则为 30 个端点（15.3%），
但 T3 中绝大多数只是读内存任务记录/磁盘状态，**可以不动**——观察类本来就是 REST 语义本身，
Gateway 只需在 T1 受理时返回 task_id，CLI/定时任务各自复用 T3 的轮询协议即可。

> **审阅注释（2026-09-07｜R1 范围）**：此分类用于估算 HTTP 改动面，不能据此判断运行时职责是否已经收敛。保留查询端点/轮询协议与提供内部查询接口不冲突；定时触发器不应被迫通过 HTTP 才能观察任务。迁移完成标准应来自状态责任表和调用链验收，而非固定端点数量。

10 个 socketio 事件中，与任务运行耦合的仅 2 个：`send_message`（T1，已废弃短路）与 `stop_task`（T2）。

---

## 1. 分类统计总表

### 1.1 类别 × 数量 × 文件分布

| 类别 | 数量 | 分布（文件：数量） |
|---|---|---|
| **T1 任务受理** | 3 | api_v1.py:1，tasks/api.py:1，workflow_runtime_api.py:1 |
| **T2 任务控制** | 13 | tasks/api.py:5，conversation.py:3，chat/approval.py:3，api_v1.py:1，workflow_runtime_api.py:1 |
| **T3 任务观察** | 14 | conversation.py:4，tasks/api.py:3，chat/approval.py:3，api_v1.py:1，workflow_runtime_api.py:1，multi_agent.py:1，conversation_bootstrap.py:1 |
| **C CRUD** | 131 | conversation.py:26，api_v1.py:19，admin.py:17，multi_agent.py:11，chat/permission.py:10，workflow_page.py:6，status/host_workspace.py:6，chat/settings.py:5，files.py:5，status/docker.py:5，status/file_open.py:4，chat/files.py:3，chat/misc.py:2，status/sandbox.py:1，status/app.py:1，auth.py:7，app_legacy.py:2，tasks/skills.py:1 |
| **S 状态查询** | 20 | status/base.py:4，status/git.py:2，status/sandbox.py:2，chat/misc.py:3，status/docker.py:1，status/app.py:1，usage.py:1，admin.py:3，auth.py:1，api_v1.py:1，chat/terminal.py:1 |
| **A 认证管理** | 15 | auth.py:8，admin.py:6，chat/terminal.py:1 |
| **合计** | **196** | 26 个文件（与任务给定的分布完全一致） |

### 1.2 按文件展开

| 文件 | 总数 | T1 | T2 | T3 | C | S | A |
|---|---|---|---|---|---|---|---|
| conversation.py | 33 | 0 | 3 | 4 | 26 | 0 | 0 |
| admin.py | 26 | 0 | 0 | 0 | 17 | 3 | 6 |
| api_v1.py | 23 | 1 | 1 | 1 | 19 | 1 | 0 |
| auth.py | 16 | 0 | 0 | 0 | 7 | 1 | 8 |
| multi_agent.py | 12 | 0 | 0 | 1 | 11 | 0 | 0 |
| chat/permission.py | 10 | 0 | 0 | 0 | 10 | 0 | 0 |
| tasks/api.py | 9 | 1 | 5 | 3 | 0 | 0 | 0 |
| workflow_page.py | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| status/host_workspace.py | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| status/docker.py | 6 | 0 | 0 | 0 | 5 | 1 | 0 |
| chat/approval.py | 6 | 0 | 3 | 3 | 0 | 0 | 0 |
| files.py | 5 | 0 | 0 | 0 | 5 | 0 | 0 |
| chat/settings.py | 5 | 0 | 0 | 0 | 5 | 0 | 0 |
| chat/misc.py | 5 | 0 | 0 | 0 | 2 | 3 | 0 |
| status/file_open.py | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| status/base.py | 4 | 0 | 0 | 0 | 0 | 4 | 0 |
| workflow_runtime_api.py | 3 | 1 | 1 | 1 | 0 | 0 | 0 |
| status/sandbox.py | 3 | 0 | 0 | 0 | 1 | 2 | 0 |
| chat/files.py | 3 | 0 | 0 | 0 | 3 | 0 | 0 |
| status/git.py | 2 | 0 | 0 | 0 | 0 | 2 | 0 |
| status/app.py | 2 | 0 | 0 | 0 | 1 | 1 | 0 |
| chat/terminal.py | 2 | 0 | 0 | 0 | 0 | 1 | 1 |
| app_legacy.py | 2 | 0 | 0 | 0 | 2 | 0 | 0 |
| usage.py | 1 | 0 | 0 | 0 | 0 | 1 | 0 |
| tasks/skills.py | 1 | 0 | 0 | 0 | 1 | 0 | 0 |
| conversation_bootstrap.py | 1 | 0 | 0 | 1 | 0 | 0 | 0 |
| **合计** | **196** | **3** | **13** | **14** | **131** | **20** | **15** |

---

## 2. T1 / T2 / T3 类端点详细清单

> 内部调用均指**端点函数体最终触达的运行时函数**。所有任务受理/控制最终收敛到
> `server/tasks/models.py` 的单例 `task_manager`（TaskManager）：
> `create_chat_task` 创建 TaskRecord 并 spawn 后台线程 `_run_chat_task` → `chat_flow.run_chat_task_sync` 执行；
> 控制类调 `cancel_task / enqueue_runtime_guidance / enqueue_runtime_pending_message / remove_runtime_pending_message / promote_runtime_pending_to_guidance`；
> 观察类调 `get_task / get_events_since / list_tasks / get_conversation_running_status`。
> 这正是「公共任务入口（RuntimeService）」要收敛的核心面。

### 2.1 T1 任务受理类（3 个）—— 必须 Gateway 化

| # | 路径 | 方法 | 文件:行 | 内部调用 | 迁移复杂度 |
|---|---|---|---|---|---|
| T1-1 | `/api/tasks` | POST | tasks/api.py:111 `create_task_api` | `task_manager.create_chat_task(...)`（前置：goal 状态清理、`_build_skill_context_messages` 技能上下文构建、conversation 补建、`_normalize_media/files_payload` 媒体归一化） | **中**：受理本身已是 task_manager 薄封装，但 HTTP 层有 ~100 行参数归一化/技能上下文/补建对话逻辑可下沉给 RuntimeService 统一处理（CLI/定时任务同样需要） |
| T1-2 | `/api/v1/workspaces/<id>/messages` | POST | api_v1.py:254 `send_message_api` | `ensure_conversation_loaded` + prompt/personalization 应用到 `terminal.context_manager` + `apply_personalization_preferences` + `task_manager.create_chat_task(...)` | **中**：prompt/personalization 文件校验与应用属于 HTTP 适配层职责（可保留），核心受理改为调用 RuntimeService |
| T1-3 | `/api/workflow/activate` | POST | workflow_runtime_api.py:38 `api_activate_workflow` | `activate_workflow()`（modules/workflow_flow）→ `try_acquire_main_task_gate` 门闸 → `task_manager.create_chat_task(..., message_source="workflow", session_data=含 gate_token)` | **大**：工作流激活语义（会话补建/模式继承/msg_index 游标）+ 主任务门闸 token 移交任务线程，逻辑最重；建议 HTTP 层保留状态机编排，仅把 `create_chat_task` 调用统一切到 RuntimeService 入口 |

### 2.2 T2 任务控制类（13 个）—— 必须 Gateway 化

| # | 路径 | 方法 | 文件:行 | 内部调用 | 迁移复杂度 |
|---|---|---|---|---|---|
| T2-1 | `/api/tasks/<task_id>/cancel` | POST | tasks/api.py:284 `cancel_task_api` | `task_manager.cancel_task()` + `GoalStateManager.mark_stopped(REASON_USER_CANCEL)` 停止对话残留目标 | **小** |
| T2-2 | `/api/v1/tasks/<task_id>/cancel` | POST | api_v1.py:444 `cancel_task_api_v1` | `task_manager.cancel_task()` | **小** |
| T2-3 | `/api/tasks/<task_id>/runtime_guidance` | POST | tasks/api.py:305 `enqueue_runtime_guidance_api` | `task_manager.enqueue_runtime_guidance()`（向运行中任务注入引导消息） | **小** |
| T2-4 | `/api/tasks/<task_id>/runtime_queue` | POST | tasks/api.py:333 `enqueue_runtime_queue_message_api` | `task_manager.enqueue_runtime_pending_message()`（运行中追问入队） | **小** |
| T2-5 | `/api/tasks/<task_id>/runtime_queue/<message_id>` | DELETE | tasks/api.py:361 `delete_runtime_queue_message_api` | `task_manager.remove_runtime_pending_message()` | **小** |
| T2-6 | `/api/tasks/<task_id>/runtime_queue/<message_id>/guide` | POST | tasks/api.py:383 `guide_runtime_queue_message_api` | `task_manager.promote_runtime_pending_to_guidance()` | **小** |
| T2-7 | `/api/workflow/deactivate` | POST | workflow_runtime_api.py:210 `api_deactivate_workflow` | `deactivate_workflow_by_user()`；若池中有通知且门闸空闲则再 `task_manager.create_chat_task(...)` 派发一轮 | **中**：控制 + 条件受理二合一，建议保留编排、收敛 Task 调用 |
| T2-8 | `/api/sub_agents/stop_all` | POST | conversation.py:2059 `stop_all_sub_agents` | `sub_agent_manager.soft_stop_all_agents()` / `sub_agent_manager.terminate_sub_agent()`（多智能体软停 / 传统模式终结） | **小** |
| T2-9 | `/api/sub_agents/<task_id>/terminate` | POST | conversation.py:2108 `terminate_sub_agent` | `sub_agent_manager.terminate_sub_agent(task_id=...)` | **小** |
| T2-10 | `/api/background_commands/<command_id>/cancel` | POST | conversation.py:2222 `cancel_background_command` | `background_command_manager.cancel_command(command_id)` | **小** |
| T2-11 | `/api/user-questions/<question_id>/answer` | POST | chat/approval.py:63 `answer_user_question` | `user_question_manager.answer(...)`（回答喂回运行中任务工具循环） | **小** |
| T2-12 | `/api/plan-approvals/<approval_id>/answer` | POST | chat/approval.py:107 `answer_plan_approval` | `plan_approval_manager.answer(...)`（计划通过后工具循环切 execute 模式） | **小** |
| T2-13 | `/api/tool-approvals/<approval_id>/decision` | POST | chat/approval.py:150 `decide_tool_approval` | `tool_approval_manager.decide(...)` | **小** |

### 2.3 T3 任务观察类（14 个）—— 可不迁移（只读，HTTP 适配层保留）

| # | 路径 | 方法 | 文件:行 | 内部调用 | 迁移复杂度 |
|---|---|---|---|---|---|
| T3-1 | `/api/tasks` | GET | tasks/api.py:34 `list_tasks_api` | `task_manager.list_tasks()`（任务列表/状态过滤） | 小（不需迁移） |
| T3-2 | `/api/conversations/<cid>/running-status` | GET | tasks/api.py:56 | `task_manager.get_conversation_running_status()` 聚合主任务+子智能体+后台命令+多智能体 | 小 |
| T3-3 | `/api/tasks/<task_id>` | GET | tasks/api.py:229 `get_task_api` | `task_manager.get_task()` + `get_events_since(rec, offset)`（**事件流 idx/offset 轮询协议**） | 小 |
| T3-4 | `/api/v1/tasks/<task_id>` | GET | api_v1.py:415 `get_task_events` | `task_manager.get_task()` + `get_events_since(rec, offset)`（同一协议） | 小 |
| T3-5 | `/api/workflow/status` | GET | workflow_runtime_api.py:295 | `WorkflowStateManager.progress_snapshot()` | 小 |
| T3-6 | `/api/sub_agents` | GET | conversation.py:1892 `list_sub_agents` | `sub_agent_manager.get_overview()` + 通知去重计算 | 小 |
| T3-7 | `/api/sub_agents/<task_id>/activity` | GET | conversation.py:2001 | 读 `progress.jsonl` 活动记录（含 limit 上限） | 小 |
| T3-8 | `/api/background_commands` | GET | conversation.py:2139 | `background_command_manager.list_records()` | 小 |
| T3-9 | `/api/background_commands/<command_id>` | GET | conversation.py:2184 | `background_command_manager.get_record_with_output()`（实时输出） | 小 |
| T3-10 | `/api/user-questions/pending` | GET | chat/approval.py:48 | `user_question_manager.list_pending()` | 小 |
| T3-11 | `/api/plan-approvals/pending` | GET | chat/approval.py:92 | `plan_approval_manager.list_pending()` | 小 |
| T3-12 | `/api/tool-approvals/pending` | GET | chat/approval.py:135 | `tool_approval_manager.list_pending()` | 小 |
| T3-13 | `/api/multiagent/active_sub_agents` | GET | multi_agent.py:483 | `sub_agent_manager.get_multi_agent_state()`（子智能体实例+token 统计） | 小 |
| T3-14 | `/api/conversations/<cid>/bootstrap` | GET | conversation_bootstrap.py:146 | 聚合 meta+messages+`task_manager.get_conversation_running_status()`+task_replay（纯只读） | 小 |

---

## 3. socketio 事件盘点（10 个）

| # | 事件 | 文件:行 | 职责 | 分类 |
|---|---|---|---|---|
| S1 | `connect` | socket_handlers.py:24 | socket_token 握手鉴权、绑定 user/terminal | A |
| S2 | `disconnect` | socket_handlers.py:76 | 清理 sid→user 映射、stop flag 生命周期 | A |
| S3 | `stop_task` | socket_handlers.py:128 | 置 sid 级 stop flag，指挥运行中任务停流（唯一 WS 任务控制通道） | **T2** |
| S4 | `terminal_subscribe` | socket_handlers.py:152 | 订阅真实终端输出流 | S/C |
| S5 | `terminal_unsubscribe` | socket_handlers.py:198 | 取消订阅 | S/C |
| S6 | `get_terminal_output` | socket_handlers.py:211 | 拉取终端输出片段 | S |
| S7 | `send_message` | socket_handlers.py:240 | **WS 聊天入口（已废弃）**：函数体已短路返回 `DEPRECATED`，死代码保留紧急回退（原路径：`start_chat_task` → 任务线程）；任务受理已全部改走 REST `/api/tasks` | **T1（废弃）** |
| S8 | `client_chunk_log` | socket_handlers.py:372 | 前端分块渲染日志上报（限流写盘） | C |
| S9 | `client_stream_debug_log` | socket_handlers.py:386 | 前端流调试日志上报 | C |
| S10 | `send_command` | conversation.py:2462 | 系统命令 `clear/status/terminals`（只读/清空历史，无 Agent 执行）；REST 对应 `/api/commands` | C |

**WS 侧结论**：10 个事件中仅 `stop_task`（T2，活跃）与 `send_message`（T1，已废弃）与任务运行时耦合，且 `send_message` 已是死代码。
事件流观察（task/terminal output）由 WS 直接转发 HTTP 轮询结果，无需 Gateway 化。

---

## 4. 结论：真正需要「Gateway 化」的端点占比与理由

### 4.1 数字结论

- **必须迁移到 RuntimeService 公共入口（受理 + 控制）= T1(3) + T2(13) = 16 个 = 8.2%**
  - 其中 T1 仅 **3 个（1.5%）**：`POST /api/tasks`、`POST /api/v1/.../messages`、`POST /api/workflow/activate`。
  - 这 16 个端点的函数体**最终都调用同一批 task_manager / sub_agent_manager / background_command_manager / approval 方法**，
    即「运行时控制面」已经收敛在几个单例方法上——Gateway 化的实质就是把这几个方法提升为 RuntimeService 的公共入口，
    Web / CLI / 定时任务复用，HTTP 端点从「直接调 manager」改为「调 RuntimeService」。
- **可迁移但不强制 = T3(14) = 7.1%**：纯只读。轮询协议（task_id + from/offset + events）本身就是 REST 语义，
  CLI/定时任务可直接复用相同协议，HTTP 适配层保留即可；未来若要统一查询接口可让 RuntimeService 暴露 `get_task_events`，但**不是本次改造的必要条件**。
- **完全不需要动 = C(131) + S(20) + A(15) = 166 = 84.7%**：CRUD、状态查询、认证管理与 Agent 运行时零耦合。

> **审阅注释（2026-09-07｜R1 配置耦合）**：“零耦合”与下文灰区说明不一致。窄核验确认 `server/chat/permission.py:166–185`、`:265–284`、`:352–368` 分别排队修改运行中的权限、执行环境和网络权限；work-mode 则在 `:420–445` 拒绝运行中切换。可保留 HTTP 路由，但这些状态修改/校验规则需要明确归属。建议理解为“多数无需改动外部接口，涉及运行态的内部调用按契约评审”，不是排除在运行时审查之外。

### 4.2 剩余 166 个端点为何可以不动

1. **C（131）**：会话/工作区/文件/配置/工作流定义/多智能体角色与设置的增删改查，全部是磁盘读写 + terminal 状态读写，
   不触发、不控制、不观察任何 Agent 任务执行。典型如 `POST /api/conversations`（有活跃任务时只建视图文件，不启动任务）、
   `POST /api/multiagent/conversations`（仅建对话并写 metadata，不发派 agent）、`PUT /api/workflows/<name>`（WORKFLOW.md 落盘）。
   唯一接近灰区的是 `POST /api/conversations/<id>/compress`（深层压缩会同步 `asyncio.run(run_deep_compression)` 调用 LLM，
   但**不创建 task_manager 任务、不经事件流、无状态轮询**，属于 HTTP 层可同步执行的对话维护操作，判定为 C 并建议改造时单独评审）。
2. **S（20）**：status/docker/git/usage/sandbox/health 等只读查询，返回宿主环境与系统状态，与任务运行时无关。
3. **A（15）**：登录/注册/CSRF/socket-token/API 用户管理/二级口令，纯认证域。
4. **页面/静态路由**（已计入 C）：`/`、`/new`、`/workflows`、`/multiagent/*`、`/admin/*` 等 17 个 SPA 入口与静态资源路由，只是 `send_static_file`。
5. **灰区说明（已统计进 C）**：`chat/permission.py` 的 3 个模式切换 POST（permission-mode / execution-mode / network-permission）在任务运行期间会把切换**入队（queue_permission_mode_change 等 pending 机制）**由工具循环消费，对运行中任务有延迟影响；但它们的本质是**配置写入**（空闲时立即生效、运行中排队后生效），与「取消/停止/审批」这类任务控制操作性质不同，故判 C。若 RuntimeService 需要支持「运行中改权限」，仅需把这 3 个方法也纳入公共入口的可选能力，不构成本次改造的必要条件。

### 4.3 改造建议要点（基于分类的推论）

- RuntimeService 首批公共方法最小集：`create_task(受理)`、`cancel_task`、`enqueue_runtime_guidance`、`enqueue_runtime_pending_message(+remove/promote)`、
  以及可选的 `get_task_events`（观察，供 CLI 复用）。
- `chat/approval.py` 的 3 个 answer/decision（T2）与 `conversation.py` 的 3 个 stop/terminate/cancel（T2）走同一批 manager 方法，
  属于同一控制面，建议一并收敛（或至少让 CLI 具备「提交审批答复」能力时复用）。
- workflow 激活/停用的状态机编排（门闸 token、会话补建、通知派发）建议保留在 HTTP 层，只下沉 Task 创建调用，控制迁移风险。

> **审阅注释（2026-09-07｜R2 过渡边界）**：认可作为分步迁移手段。长期职责应让工作流服务承接激活、会话补建、门闸移交与失败回滚，HTTP 只解析请求和映射响应；若暂不迁移，应标注剩余兼容依赖。同样，API 入口中的模型/偏好应用等业务规则是否留在路由，应以非 HTTP 调用是否需要复用来判断。

### 4.4 方法与口径声明

- 全部 196 个端点经 `grep -E "@[a-zA-Z_]+\.route"` 逐文件提取并核对数量（26 文件合计 196，与任务给定的分布逐文件一致）。
- 分类依据为端点函数体实际行为（阅读每一处路由函数），非凭路径猜——凡能触发 `task_manager.create_chat_task` / 后台线程 / 任务控制方法者才归入 T1/T2。
- WS 侧 `send_message` 已确认函数体首行即短路返回废弃提示，其 T1 归类基于历史职责（死代码保留的回退分支），已单独标注。

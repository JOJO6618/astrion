# 支撑链路耦合点盘点：事件推送 / 审批 / 取消 / 保存

- 任务：为「运行时边界整理（RuntimeService 公共任务入口）」评估四条支撑链路的复用可行性
- 性质：**只读静态代码分析**（基于当前 checkout 源码阅读与 grep，未做运行复现；行号均指当前文件版本）
- 范围：server/、modules/、utils/、core/
- 结论确定性说明：文中「确认」指源码直接可见；「推断」指由代码结构推导、未运行时验证；「待核验」指存在多解释、需复现确认。未特别标注处为源码直接确认。

---

## 0. 四条链路一图快照

| 链路 | 核心载体 | 耦合标识符 | 等待/反馈机制 | 关键文件:行号 |
|---|---|---|---|---|
| 事件推送 | `TaskRecord.events`（有界 deque maxlen=20000）+ 每任务 `next_event_idx` | task_id（idx+轮询）、username（socket 房间）、conversation_id（事件补全） | 拉：GET /api/tasks/<id>?from=offset；推：socket room `user_{username}` | tasks/models.py:86/:762、context.py:33 |
| 审批/提问 | `ToolApprovalManager` / `PlanApprovalManager` / `UserQuestionManager`（纯内存存储） | username + conversation_id（manager 键）；approval_id/question_id（等待键） | 执行侧 `asyncio.sleep` 轮询（0.2~0.3s，默认超时 3600s）；回答走 REST 写 manager | chat_flow_tool_loop.py:232、chat/approval.py |
| 取消 | `state.stop_flags`（Dict[client_sid→entry]） | task_id（REST 任务即 client_sid）；socket 场景为 request.sid | 标志位轮询（100ms 工具期）+ 硬取消（asyncio task.cancel） | state.py:135、models.py:245、chat_flow.py:178 |
| 保存 | `ContextManager` → conversation 文件（merge-on-save） | conversation_id + message_id（合并键）；对话文件路径 | 每消息 append → auto_save（持 `_io_lock` 原子写） | message_mixin.py:127、crud_mixin.py:335 |

关键结论先行：**四条链路均以「任务记录/内存对象」为中心，标识符语义基本收敛（task_id / username / conversation_id），无对 Flask request / socket sid 的硬依赖（执行线程用 test_request_context 包装属「软依赖」，可剥离）；但审批/提问链路在无人值守下会阻塞直到 3600s 超时，且超时语义是「拒绝该工具、任务继续」而非「结束任务」，需改造。总体判定：事件/取消/保存三条可直接复用（需适配层），审批链路需改造。**

---

## 1. 事件推送链路

### 1.1 事件如何产生与编号

- 每个 REST 任务 = 一个 `TaskRecord`，事件存于 `self.events: deque(maxlen=20000)`（**有界**，超出丢弃最旧；注释说明 1000→20000 是为刷新恢复时前端从事件流重建长流式输出）——tasks/models.py:86。
- 事件编号：`TaskRecord.next_event_idx`（初始 0，models.py:95）单调递增；`_append_event`（models.py:762-783）在全局锁内取出 idx → `rec.next_event_idx += 1` → 追加 `{"idx", "type", "data", "ts"}`，并在 `data` 上 `setdefault(task_id / conversation_id / workspace_id)`。**idx 是 task 级单调序号，无全局唯一性需求**，客户端按 offset 断点续读，因此 idx 语义只对「同一 task 的事件流」成立（任务清理后失效，前端靠对账接口兜底）。
- 执行期事件来源分两路（都汇到同一个 recorder）：
  1. `_run_chat_task` 内部定义的 `sender(event_type, data)`（models.py:907-928）：追加事件 + `socketio.emit(event_type, data, room=f"user_{username}")`；
  2. 执行链内部大量直接调 `sender(...)`（chat_flow_task_main.py 的 thinking_chunk/text_chunk/tool_preparing/update_action/system_message/task_complete 等，见 chat_flow_task_main.py:1478-1540 的 sender 包装）；
  3. token_update 等 context_manager 回调事件：`_run_chat_task` 在运行期间把 `terminal.context_manager._web_terminal_callback` 临时切到任务 `sender`（models.py:935-936），结束后恢复（models.py:1011-1013）。
- **补齐 conversation_id 的层次**（双重包装）：
  - 对话级 terminal 广播：`_wrap_callback_with_conversation_id`（context.py:75-90）为 dict 事件 `setdefault(conversation_id)`；
  - 任务执行链：`handle_task_with_sender` 内层 sender（chat_flow_task_main.py:1516-1546）为全部事件补 conversation_id，为 error/quota_exceeded/task_stopped/task_complete 补 task_id/client_sid。
  - 因此事件流里 conversation_id **不是权威来源**（只是为前端定位/过滤而注入），权威在 conversation 文件侧。

### 1.2 事件如何到达 socketio 客户端

- 广播目标 = 用户房间 `user_{username}`，见 context.py:33-42（`make_terminal_callback`）与 models.py:926、:1048。socket 前端 connect 时 `join_room(f"user_{username}")`（socket_handlers.py:45）。
- **房间粒度是「用户」，不是「对话」也不是「任务」**：同用户全部对话的事件都发到同一房间，前端按 payload 的 conversation_id 过滤、按 task_id 区分任务流。
- Web 聊天（socket 模式）另有 `send_to_client` 直发 `room=request.sid`（socket_handlers.py:315-322），此为单连接定向广播，与 REST 任务的用户房间广播是两条独立路径。

### 1.3 客户端轮询端点如何按 offset 读

- `GET /api/tasks/<task_id>?from=<offset>`（tasks/api.py:231-287）：`get_events_since(rec, offset)`（models.py:226-235，锁内 O(n) 浅拷贝快照再过滤 `e["idx"] >= offset`）→ `next_offset = events[-1]["idx"] + 1`。
- 前端 task 轮询 250ms 一次、带 `X-Task-Poll` 头与 `from` 游标（static/src/stores/task.ts:64/:156），另有 running-status 对账接口（tasks/api.py:38-89）作正确性兜底。

### 1.4 耦合点清单（事件链路）

| # | 耦合对象 | 位置 | 说明 |
|---|---|---|---|
| E1 | **task_id** | models.py:71(:TaskRecord.task_id)、:762-770(_append_event 写入 data)、api.py:231-287(poll 寻址)、models.py:940(client_sid=rec.task_id) | 事件 idx/缓冲/轮询游标全部挂在 TaskRecord 上，一切以 task_id 寻址 |
| E2 | **username（socket 房间）** | context.py:38、models.py:926/:1048、socket_handlers.py:45 | socket 推送按 `user_{username}` 房间；离线时无人接收（事件仍落 deque） |
| E3 | **conversation_id（事件补全，非权威）** | context.py:75-90、chat_flow_task_main.py:1516-1546、models.py:765-769 | 事件内注入用；权威在对话文件 |
| E4 | **terminal + conversation 配对（回调临时劫持）** | models.py:935-936/:1011-1013（set_web_terminal_callback 切换/恢复） | REST 任务运行期独占该对话级 terminal 的 context 回调；对话级隔离下安全，但新调用方若复用同一对话级 terminal 需注意「同一时刻一个主任务」约束（main_task_gate） |
| E5 | 有界 deque 容量/清理 | models.py:86（maxlen=20000）、:108-128（cleanup_old_tasks 终态>3600s 清理） | 事件只存活于任务生命周期，重启/清理后消失；前端需对账恢复（todo：probe.ts 对账属前端侧） |
| E6 | 事件类型契约 | chat_flow_task_main.py 各 sender 调用点、socket 前端监听 | 事件 type 字符串是前后端隐式契约（无 schema/版本），新增事件类型要与前端联调 |

### 1.5 新调用方（如定时任务触发器）能否直接复用？

**判定：可直接复用，适配点在「构造 TaskRecord + 会话上下文」。**

- 链路本身不依赖 HTTP request：`_run_chat_task` 里的 `test_request_context`（models.py:790-797）只用于从 `rec.session_data` 回填 Flask session 以兼容下游读取，属于可剥离的软耦合（阶段二已计划消除，models.py:785 注释确认「只包装不算解耦」）。
- 已有非 Web 触发先例：完成通知链（chat_flow_task_main.py:613 派发 `create_chat_task` + `session_data["main_task_gate_token"]` 移交门闸）、workflow_runtime_api.py:184/:264、api_v1.py:320——它们服务的对象仍是「用户在线场景」。
- **新调用方必须提供/生成的耦合键**：`username`（principal）、`workspace_id`、`conversation_id`（可让运行时补建）、`task_id`（uuid，事件/取消/轮询共用）。不需要 terminal_id——terminal 由 `get_user_resources(username, workspace_id, conversation_id)` 派生（对话级缓存键 = `username::workspace_id::conversation_id`，context.py:62-71）。
- 需要适配/确认的两点：
  1. 事件仍会 `socketio.emit` 到 `user_{username}` 房间——用户离线时无影响（无人接收，deque 仍在），但用户在线时会收到定时任务的事件（**前台干扰**：需要确认产品预期，或给事件加 source 字段让前端过滤）；
  2. 与在线聊天任务的并发约束：`create_chat_task` 的单对话互斥（models.py:158-177）已覆盖 chat 类型；通知链已演示通过 `main_task_gate_token` 预占门闸的模式，定时任务的会话策略（独立会话 or 复用会话）需显式定义后才能确定复用模式。

---

## 2. 审批链路（tool 审批 / plan 审批 / ask_user 提问）

### 2.1 pending/answer 机制（三个 manager 同构）

- `ToolApprovalManager`（modules/tool_approval_manager.py，92 行）：`create_request`（:17-40）生成 `approval_id = approval_{uuid}`，条目含 username / conversation_id / task_id / tool_call_id / tool_name / arguments / preview，status=pending；`decide`（:56-92）把 status 置 approved/rejected。**纯内存 dict + threading.Lock，无等待原语、无 TTL、无清理**。
- `PlanApprovalManager`（modules/plan_approval_manager.py）：`create_request`（:25-64）存计划文档内容（截断 20000 字符）；`answer`（:88-100）置 approved/rejected + comment。同样纯内存。
- `UserQuestionManager`（modules/user_question_manager.py）：`create_question`（:70-121）支持 batch（batch_id/batch_index/batch_total，前端一次弹多问）；`answer`（:137-176）支持 option / free_text / dismissed。
- 三者都提供 `list_pending(username, conversation_id)`——**关联键是 username + conversation_id**。

### 2.2 等待/回答如何回到执行链路

- **等待原语不是 asyncio.Event，而是 asyncio.sleep 轮询**：
  - `_wait_for_tool_approval`（chat_flow_tool_loop.py:232-263）：每 0.2s `tool_approval_manager.get(approval_id)` 看 status；**默认超时 3600s**，超时返回 decision=rejected + code=approval_timeout；
  - `_wait_for_user_questions`（:265-292）：0.2s 轮询多问题批量等到全部 answered，超时置 timeout；
  - `_wait_for_plan_approval`（:294-309）：0.3s 轮询，超时返回 status=timeout。
- 调用点：plan :435、ask_user :574、tool :883 与 :1153（:883 走 `run_auto_approval`——若权限模式为 auto_approval，则由 `ApprovalAgent` 审核决定，modules/auto_approval_service.py:56-105，期间仍可人工接管 `_manual_takeover`）。**各调用点均未传 timeout_seconds，一律吃默认 3600s**。
- **前端如何收到审批请求**：执行链路 `sender('tool_approval_required' / 'plan_approval_required' / 'user_questions_required', ...)`（chat_flow_tool_loop.py:429/:569/:851/:1121），事件汇入任务事件流（REST 轮询可见）+ socket 用户房间。另有 REST 端点轮询 pending：`/api/tool-approvals/pending`、`/api/user-questions/pending`、`/api/plan-approvals/pending`（server/chat/approval.py:51/:95/:138）。
- **回答如何回到执行链路**：REST 端点 `/decision`、`/answer`（chat/approval.py:67/:111/:154）只是「写 manager 条目」；执行侧的 sleep 轮询下一次 tick 读到 status 变化即继续。**没有事件总线回环、没有回调**——执行线程与回答线程仅通过 manager 内存对象耦合。

### 2.3 耦合点清单（审批链路）

| # | 耦合对象 | 位置 | 说明 |
|---|---|---|---|
| A1 | **approval_id / question_id（等待键）** | chat_flow_tool_loop.py:232-309；manager get() | 等待循环只认 id，轮询 manager |
| A2 | **username + conversation_id（授权与归属）** | manager list_pending/decide/answer（三处 username 校验）；chat/approval.py | 回答端点校验当前登录用户==条目 username；前端按 conversation_id 过滤 |
| A3 | **task_id 字段（实际恒 None）** | chat_flow_tool_loop.py:411/:545/:845/:1115（`getattr(web_terminal, "task_id", None)`） | 全仓 grep 未发现 `web_terminal.task_id` 的赋值点（core/web_terminal.py 无此属性）→ 审批条目里的 task_id 目前**始终为 None**（高置信推断，未排除动态 setattr 路径）；关联任务仅靠事件流里的 task_id 补全（_append_event setdefault），manager 本身不按 task_id 检索 |
| A4 | **执行线程阻塞挂起** | 等待循环捕获不到 stop 标志（见 2.4） | 主任务线程在等待期间处于 sleep 轮询，软取消不可中断等待 |
| A5 | **manager 内存常驻，无 TTL/清理** | modules/tool_approval_manager.py、plan_approval_manager.py、user_question_manager.py | 未决条目永久留在 `_items`（无 reaper）；长时间运行会累积 |
| A6 | **REST 端点依赖 @with_terminal** | chat/approval.py（装饰器） | 回答/列等待办的端点需会话上下文；`with_terminal` 内部按 session 归属取 terminal |

### 2.4 无人值守场景（定时任务）的具体改造点

现状行为（确认）：
1. 无人回答 → 等待循环跑满 3600s 才按「超时=拒绝该工具」放行，**任务继续执行**（并不结束任务）；
2. 期间用户若点了停止：REST 取消会 `loop.call_soon_threadsafe(task.cancel)` **硬取消**，CancelledError 会打断 sleep 轮询 → 任务收尾成 stopped（这条路径可用，但依赖「有人发起取消」）；socket 的软 stop 标志（仅 set flag）**不会**打断审批等待——下一次工具调用的行首检查（:600）才生效；
3. `auto_approval` 权限模式（若已配置）由审核智能体决策，天然不依赖人在线——这是无人值守可复用的现成机制，但仅覆盖「需要审批」的工具，且默认各用户是否启用取决于权限策略配置。

改造点（按最小改动排序）：

1. **给等待循环注入可配置超时**（必须）：`_wait_for_tool_approval / _wait_for_user_questions / _wait_for_plan_approval` 均已有 `timeout_seconds` 参数，但调用点（:435/:574/:883/:1153）没传。改造 = 从 `TaskRecord.session_data` 传入（如 `{scheduled: True, approval_timeout_seconds: N}`），经 `handle_task_with_sender → handle_task_with_sender args → _execute_tool_calls_impl` 透传。无人值守建议超时远小于 3600s（如 60~300s）。
2. **定义超时语义**（需要产品决策，当前语义=拒绝该工具继续跑）：定时任务场景两种候选——(a) 超时=拒绝该工具、任务继续（现状，风险：任务在无监督下继续下一步）；(b) 超时=结束整个任务（需在等待返回后检查 `timeout` 状态并向运行循环抛「终止」信号，或直接复用取消链路 `task_manager.cancel_task`）。方案不同，改动位置不同：前者零改动（只调参），后者要动 chat_flow_tool_loop 的返回路径。
3. **等待循环内增加 stop 检查**（推荐顺带）：与工具执行期 100ms 停止轮询（:1018-1045）对齐，在三个 `_wait_*` 循环里轮询 `get_stop_flag`，让软取消也能中断等待——这同时修复「在线用户停止按钮在审批等待期间无效」的现状 gap（确认：现状软 stop 确实无法中断等待）。
4. **manager 条目 TTL/清理**（工程债）：未决条目需按创建时间清理（与任务 cleanup 类似），避免定时任务多次触发后 pending 条目膨胀；超时/取消时应把条目置终态（目前只有 decide/answer 能置终态，等待方超时不回写 manager——`approval_timeout`/`timeout` 只存在于返回值）。
5. **定时任务的会话策略决定审批是否可达**：若定时任务复用用户既有对话，用户上线时仍可在该对话看到审批请求（事件进用户房间 + pending 端点）；若用户长期离线，则依赖超时/auto_approval。建议首版：定时任务会话默认不产生人工审批（策略层在派发前预检权限模式，需要审批的工具直接失败重试或跳过），把「no-human-approval」作为定时任务会话的硬约束写进会话策略。
6. 仅当需要「审批请求在离线后也能被用户补答」时才考虑持久化 manager（当前全是内存态，重启即丢）——阶段三路线已把「无人在线遇审批/提问按超时结束，不扩大权限」列为原则，与上述 1/2 一致。

### 2.5 复用判定（审批链路）

> **审阅注释（2026-09-07｜R5 产品策略）**：§2.4 的“禁止人工审批”“60–300s 超时”等均为待讨论选项，不是定时任务的技术前提。拒绝当前工具后继续运行仍受权限限制，不自动等于不安全；用户也可能希望任务等自己上线回答。工具审批、计划审批和用户提问应分别确定超时处理。客户端离线但服务仍运行时，现有 pending 可保留；跨服务重启保留记录与恢复等待执行是另外两层需求。

> **审阅注释（2026-09-07｜R6 取消与条目状态）**：§2.4 所称“停止按钮无效”应限定为只置软停止标志的路径；本报告 §3.1 已说明标准按钮使用 REST 硬取消，可打断等待。条目生命周期应先定义超时/取消终态与迟到回答处理，再设置保留期清理，不能只按 TTL 删除仍被等待的 pending。task_id 恒 None 保持“静态疑点”定性，待运行时关联验证。

- **不可直接复用**（无人值守语义不成立）：等待循环机制本身与任务执行深度内嵌（定义在执行链工具循环里），但**存储 manager 可复用**（与 Web 前端共用 pending/answer 数据），需新增：超时策略注入 + 超时语义决策 + stop 检查 + 条目清理。
- 计划审批/提问同理：都是「模型工具阻塞等人工」，无人值守下按同一套超时策略处理；auto_approval 分支（ApprovalAgent）是唯一现成的无人工决策路径，但只覆盖 tool 审批（plan/ask_user 无 agent 替代）。

---

## 3. 取消链路

### 3.1 停止按钮到任务取消的完整链路

- **REST（统一入口）**：前端停止按钮现在的标准路径 = `POST /api/tasks/<task_id>/cancel`（tasks/api.py:289-320）→ `task_manager.cancel_task(username, task_id)`（models.py:245-322）：
  1. 按 task_id 取记录（校验 username 归属）；
  2. 若 stop_flags entry 里还持有 asyncio loop/task 引用 → `loop.call_soon_threadsafe(task.cancel)` **硬取消**；
  3. 置 `stop_flags[task_id] = {'stop': True, ...}` + `rec.stop_requested = True`；
  4. 停止该对话目标模式（GoalStateManager）；
  5. 丢弃 runtime_guidance_queue，status 置 `cancel_requested`（**秒级瞬态**，_run_chat_task 收尾定终态）。
- **Socket（旧路径，语义=停最近任务）**：`stop_task`（socket_handlers.py:128-151）`get_stop_flag(request.sid, username)` 置 stop 标志；disconnect 兜底（:96-118）仅在「无其他连接且无 REST 运行任务」时 stop + hard cancel。
- **运行期检查点**（全部 `include_user=False`，只查任务级键）：
  - 流式输出循环：chat_flow_stream_loop.py:59/:287；
  - 主任务循环：chat_flow_task_main.py:996/:1140/:2058；
  - 工具循环：chat_flow_tool_loop.py:600（每个 tool_call 前）、:1018-1045（工具执行中 100ms 轮询并 `tool_task.cancel()`）；
  - 重试延迟：chat_flow_task_support.py:568-582（wait_retry_delay）；
  - 入口处预读：models.py:813（stop_hint）。
- **收尾**：`_run_chat_task` finally（models.py:1018-1049）：canceled_flag 为真 → status 一律 `stopped` + `task_stopped` 事件（含 has_running_sub_agents/has_running_background_commands 让前端决定停止按钮显隐）；:1088 `stop_flags.pop(rec.task_id)` 清理。
- **后台任务/子智能体如何响应**：主任务只停主智能体（models.py:245-249 注释明确）；后台命令与子智能体有独立 API（/api/sub_agents/stop_all、/api/background_commands/stop_all）；`_cleanup_background_tasks`（models.py:633-682）负责把相关后台命令置取消状态。

### 3.2 取消寻址依据

- **寻址依据 = `username + task_id`**（REST cancel 校验两条）；底层 stop_flags 的**任务级 key 就是 client_sid（REST 任务下即 task_id）**，用户级 `user:{username}` 仅是 socket 索引，不参与运行期判定（state.py:135-159、记忆 stop_flags_per_task_isolation 确认）。
- 不依赖 terminal_id；conversation_id 只用于副作用（停目标模式）。

### 3.3 耦合点清单（取消链路）

| # | 耦合对象 | 位置 | 说明 |
|---|---|---|---|
| C1 | **task_id（=stop_flags 键）** | state.py:135-159、models.py:245-322、chat_flow.py:178 | 停止状态唯一真相；REST 任务 task_id 即 client_sid |
| C2 | **asyncio loop/task 引用** | chat_flow.py:166-179（entry 持有 loop+task）、models.py:281-287（硬取消用） | 每个任务独立事件循环（process_message_task 内 `asyncio.new_event_loop`）；硬取消 = loop.call_soon_threadsafe(task.cancel) |
| C3 | **checkpoint 散点** | 见 3.1 运行期检查点 | 软停止靠各检查点 100ms~每轮粒度生效 |
| C4 | `cancel_requested` 瞬态语义 | models.py:309-319、:1018-1035 | 活跃集合成员判定（pending/running/cancel_requested），收尾统一置 stopped |
| C5 | 后台任务独立性 | models.py:633-682、socket_handlers/API 层 | 停止主任务不自动停后台；公共服务需保留这两类独立控制入口 |

### 3.4 公共入口要支持取消需保留什么

- **判定：可直接复用**（REST cancel 路径对调用方透明——只要持有 task_id）。
- 需保留/注意：
  1. **task_id 必须回传并持久化给发起方**（定时任务 Occurrence 记录 run_id=task_id，取消才可寻址）；
  2. 保留 `process_message_task` 的「独立事件循环 + entry 持有 loop/task」模式——硬取消依赖它；公共入口若换线程池/事件循环策略，需保持该契约；
  3. 保留任务级 stop 键语义（不能让定时任务与同用户在线任务共享 stop 键）；`cancel_task` 已是 task_id 精确取消，天然满足；
  4. 移除/包装对 `session` 的依赖后，`cancel_task` 中取 terminal 的副作用路径（get_user_resources）要用显式上下文调用（它已按 rec.workspace_id/conversation_id 构造，仅需注入 username 上下文）。

---

## 4. 保存链路（对话历史写入）

### 4.1 保存保护机制（三层）

- **merge-on-save（防缩减）**：`save_conversation`（crud_mixin.py:298-360，merge 自 :335）默认把磁盘旧消息与内存新消息**按 message_id 合并**（`_merge_messages_by_id`，:252-296：磁盘独有保留、内存独有追加、无 message_id 防御性跳过），并带守卫断言——merge 后消息数若缩减且非 `allow_shrink`（仅检查点恢复豁免）→ **拒绝保存**（:355-360）。
- **I/O 锁**：`_save_conversation_file`（crud_mixin.py:194-203）与 `_update_index`（:205-244 读-改-写整体持锁）共用 `self._io_lock = threading.RLock()`（base.py:47）——锁在 ConversationManager 实例上，**同一对话同一 manager 内的并发写被串行化**（跨进程/跨实例不覆盖）。
- **原子替换**：`_atomic_write_json`（index_mixin.py:104-133）：同目录 NamedTemporaryFile（唯一前缀）+ json.dump + flush/fsync + `replace_with_retry`（Windows 持锁退避），失败转存 `.last_failed.tmp` 留证。

### 4.2 任务执行写对话历史的入口是否唯一收敛

- **执行链写入口收敛**：任务执行内全部走 `web_terminal.context_manager.add_conversation(...)`（message_mixin.py:127-229）：主任务各路径 chat_flow_task_main.py:1611/:1641/:1657/:1670/:1832/:2000/:2346；工具循环 chat_flow_tool_loop.py:674/:743/:784/:826/:916/:1068/:1186/:1402；注入路径 chat_flow_task_support.py:121/:455。`add_conversation` 每次 append 后**立即 `auto_save_conversation()`**（:229 → conversation_mixin.py:385-414 → crud_mixin.save_conversation），即每条消息落盘一次。
- **但存在执行链之外的第二批写者**（同一 conversation 文件的其他写入口，均复用同一 save_conversation 保护，但语义不同）：
  - 用户设置/恢复：server/chat/settings.py:63/:164、server/conversation.py:1573、core/main_terminal_parts/commands.py:142/:189/:391（CLI 命令交互）、core/web_terminal.py:308（terminal 内部管理）、edit_summary.py:203、compression_mixin.py:203（压缩续接）、todo_annotation_mixin.py:131。
- 因此：**「任务执行写历史」收敛于 context_manager，但「conversation 文件写」不是全局唯一收敛**——存在多处直接调 save_conversation/auto_save 的外部写者。保护正确性靠：merge-on-save + 每对话 manager 级 _io_lock + 对话级主任务门闸（main_task_gate.py，一个对话同一时刻一个主任务）三者叠加。
- 风险点：`_io_lock` 是 manager 实例级，**两个不同 ConversationManager 实例写同一对话文件**（如对话级 terminal 与工作区级服务实例双持同一对话）时锁不互斥；代码已有对策（对话级隔离 + create_chat_task 单对话互斥 + 主任务门闸），但这是「并行写保护依赖多道防线而非单一锁」的现状，新入口必须遵守同一套防线（见 4.3）。

### 4.3 耦合点清单（保存链路）

| # | 耦合对象 | 位置 | 说明 |
|---|---|---|---|
| S1 | **conversation_id（文件/目录定位）** | conversation_manager 各 load/save | 保存寻址键 |
| S2 | **message_id（合并键）** | crud_mixin.py:252-296、message_mixin.py:142 | `_generate_message_id()` 生成；合并去重依赖其唯一性与新消息必带 | 
| S3 | **manager 实例级 `_io_lock` (RLock)** | base.py:47、crud_mixin.py:194-203/:205-244 | 读-改-写互斥；跨实例不互斥 |
| S4 | **对话级主任务门闸** | main_task_gate.py（process_message_task 统一获取/释放，chat_flow.py:139/:258） | 对话单写者约束，与保存锁互补 |
| S5 | **ContextManager 实例与对话绑定** | context.py:62-71（缓存键含 conversation_id） | 每对话独立 terminal/context_manager 是并发写隔离的前提 |
| S6 | **自动保存频率** | message_mixin.py:229（每消息 auto_save） | 长流式回复高频落盘；性能与原子写并存的既定取舍 |

### 4.4 新调用方复用是否有额外前提

- **判定：可直接复用**（写入口已收敛、保护机制完善），前提：
  1. 必须走「对话级 terminal + 该对话专属 ContextManager」路径（`get_user_resources(username, workspace_id, conversation_id)`），不要落到工作区级服务实例，否则双持同一对话时锁不互斥（S3 边界）；
  2. 必须遵守主任务门闸：一次性主任务在 process_message_task 统一获取；若新调用方是「轮询器预占→移交 token」模式，按通知链现有模式（session_data["main_task_gate_token"]，chat_flow_task_main.py:613-620）复用；
  3. 定时任务的会话策略（使用独立会话 or 复用既有对话）决定消息写入哪个 conversation_id；若复用用户对话，注意与用户在线任务的互斥（create_chat_task 单对话互斥会直接拒绝并发 chat 任务，models.py:158-177——**这是现成防线，保持即可**）。

---

## 5. 结论：复用现有链路的总体可行性评估

1. **事件链路：直接复用（需适配层）**。核心载体 TaskRecord（deque+idx）对调用方不可见，调用方只需 `create_chat_task` 拿到 task_id 即可轮询/取消；socket 推送按用户房间，离线无影响。适配点：消除对 Flask session 的隐式读取（test_request_context 包装）、定义定时任务事件的来源标识（防前台干扰）。
2. **取消链路：直接复用**。task_id 精确取消已达成「寻址=username+task_id」，不依赖 terminal/conversation（conversation 仅副作用）；保留「独立事件循环+entry 持 loop/task」即可支持硬取消。公共入口需把 task_id 持久化到 Occurrence。
3. **保存链路：直接复用，前提明确**。写入口收敛于 `context_manager.add_conversation`，merge-on-save + 锁 + 门闸三道防线可迁移；新调用方必须(1)走对话级 terminal、(2)遵守主任务门闸、(3)会话策略决定 conversation_id。注意保存链路存在执行链之外的次要写者（设置/压缩/CLI），它们共用同一保护，非执行链耦合。
4. **审批/提问链路：需改造**。存储 manager 可复用，但等待循环(1)默认超时 3600s 且调用点不传参、(2)超时语义=拒绝工具继续跑而未结束任务、(3)等待期间软停止无效、(4)manager 条目无 TTL。无人值守改造点按 §2.4：注入超时参数（session_data 透传）、定义超时语义（拒绝继续 or 结束任务）、等待循环加 stop 检查、条目清理、会话策略禁止人工审批或依赖 auto_approval 分支。

**总体**：以 `create_chat_task + run_chat_task_sync` 为骨架抽 RuntimeService 的方案成立——事件/取消/保存三链的标识符语义（task_id/username/conversation_id）已收敛且耦合点可枚举；审批链路是唯一需要先改造语义（超时）再复用的链路。改造顺序建议：审批超时策略 → 公共入口 → 定时任务派发（与 gateway_runtime_work_plan 三阶段路线一致）。

> **审阅注释（2026-09-07｜实施顺序）**：现有审批语义可在阶段二解耦上下文时先保持兼容；无人值守超时策略在阶段三实现前确定即可，不必阻塞公共入口整理。三条可复用支撑链也需核验异常回退是否仍有任务记录、事件和取消关联，不能把“可复用”理解为无需验证。

---

## 附：主要引用文件清单

- server/tasks/models.py（TaskRecord/deque/idx/清理、_append_event、sender、cancel_task、_run_chat_task 收尾）
- server/tasks/api.py（poll 端点、cancel 端点、running-status 对账）
- server/context.py（make_terminal_callback、attach_user_broadcast、_wrap_callback_with_conversation_id、get_user_resources）
- server/state.py（stop_flags/set/get/clear）
- server/chat_flow.py（process_message_task、run_chat_task_sync）
- server/chat_flow_task_main.py（handle_task_with_sender、sender 包装、通知链 create_chat_task）
- server/chat_flow_tool_loop.py（三个 _wait_*、审批事件发送、工具期停止轮询）
- server/chat_flow_stream_loop.py / server/chat_flow_task_support.py（运行期停止检查点）
- server/socket_handlers.py（connect/disconnect/stop_task）
- server/chat/approval.py（三个 pending/answer REST 端点）
- server/main_task_gate.py（对话级主任务门闸）
- modules/tool_approval_manager.py / plan_approval_manager.py / user_question_manager.py / auto_approval_service.py
- utils/context_manager/message_mixin.py、conversation_mixin.py
- utils/conversation_manager/crud_mixin.py、index_mixin.py、base.py

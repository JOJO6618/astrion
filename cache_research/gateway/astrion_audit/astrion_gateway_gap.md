# Astrion Gateway 视角现状盘点（只读审计）

> 审计范围：工作区根目录 `<workspace>`
> 审计时间：2026-09-07（代码快照以当日工作区为主）
> 审计方式：只读分析，未修改任何文件
> 分析视角：把 Flask server 渐进升级为 "Runtime/Gateway"——session/run/approval/event 的唯一 owner，
> Web/CLI/Desktop/Android 仅作状态投影。以下结论均以具体文件/函数/类名佐证。
>
> 说明：本报告为“现状盘点 + 差距清单”，属于**静态代码分析结论**，未做运行时验证；
> 涉及“重启丢失”“断线行为”等结论源自代码路径推演，标注了不确定度。

---

## 1. API 表面清单

### 1.1 蓝图注册（REST 面）

注册点：`server/app_legacy.py:298-310`（`app.register_blueprint(...)`），共 12 个蓝图：

| 蓝图 | 定义文件 | 覆盖类别 | 代表性端点 |
|---|---|---|---|
| `auth_bp` | `server/auth.py` | 登录/注册/会话/CSRF | `POST /login`（L123）、`POST /host-login`（L223）、`POST /register`（L291）、`GET /api/csrf-token`（L109）、`GET /api/session-status`（L369） |
| `files_bp` | `server/files.py` | 文件浏览/上传下载 | `GET /api/files`、`GET/POST /api/gui/files/text` |
| `admin_bp` | `server/admin.py` | 管理后台/策略/API 用户 | `GET /api/admin/dashboard`、`GET/POST /api/admin/api-users`、`GET /api/admin/api-users/<u>/token` |
| `conversation_bp` | `server/conversation.py` | 对话 CRUD/草稿/压缩/版本化 | `GET/POST /api/conversations`（L571/L639）、`GET /api/conversations/<id>/messages`（L1137）、`GET/PUT /api/input-draft`（L431/L457）、`/api/conversations/<id>/versioning*` |
| `chat_bp` | `server/chat/{approval,permission,settings,terminal,files,misc}.py` | 审批/模式/设置/终端/杂项 | `GET/POST /api/permission-mode`（permission.py L138/152）、`GET/POST /api/work-mode`（L404/420）、`GET /api/plan-approvals/pending`（approval.py L92）、`GET /api/socket-token`（terminal.py L64）、`GET /api/gui/monitor_snapshot`（misc.py L62） |
| `usage_bp` | `server/usage.py` | 用量配额 | `GET /api/usage` |
| `status_bp` | `server/status/{base,app,docker,file_open,git,host_workspace,sandbox}.py` | 健康/状态/项目/工作区/沙箱 | `GET /api/health`（base.py L69）、`GET /api/status`（L90）、`GET /api/projects`（docker.py L91）、`GET /api/host/workspaces`（host_workspace.py L57）、`GET /api/sandbox/status`（sandbox.py L30） |
| `tasks_bp` | `server/tasks/{api,skills,media}.py` | **REST 任务轮询主线** | `POST /api/tasks`（api.py L111）、`GET /api/tasks/<id>?from=idx`（L229，事件轮询核心）、`POST /api/tasks/<id>/cancel`（L284）、`GET /api/conversations/<id>/running-status`（L56，对账接口）、`POST /api/tasks/<id>/runtime_guidance`（L305） |
| `api_v1_bp` | `server/api_v1.py` | 面向 API/CLI 的 v1（Bearer token） | `GET /tools`（L24）、`POST /workspaces/<ws>/messages`（L254）、`GET /workspaces/<ws>/conversations`（L346）、`GET /tasks/<id>`（L415）、`GET /models`（L718，**无鉴权**）、`GET /health`（L742，无鉴权） |
| `multi_agent_bp` | `server/multi_agent.py` | 多智能体角色/设置/对话创建 | `POST /api/multiagent/conversations`（L288）、`GET /api/multiagent/active_sub_agents`（L483） |
| `workflow_page_bp` / `workflow_runtime_bp` | `server/workflow_page.py` / `server/workflow_runtime_api.py` | 工作流页面与运行时 | `POST /api/workflow/activate`、`GET /api/workflow/status`、`GET/PUT/DELETE /api/workflows/<name>` |
| `conversation_bootstrap_bp` | `server/conversation_bootstrap.py` | 对话首屏恢复 | `GET /api/conversations/<id>/bootstrap` |

**REST 为主**：AGENTS.md §1 明示 "REST 任务轮询为主，Socket.IO 主要用于兼容与实时辅助通道"；`server/tasks/api.py` 注释同（"将聊天任务与 WebSocket 解耦，支持后台运行与轮询"）。

### 1.2 Socket.IO 事件面

注册点：`server/socket_handlers.py`（全部 `@socketio.on(...)`）。Socket=连接面，**不是业务消息主通道**：

- **客户端→服务端**：`connect`（L20，携带 `socket_token` 认证）、`disconnect`（L79）、`stop_task`（L130）、`terminal_subscribe`（L154）、`terminal_unsubscribe`（L199）、`get_terminal_output`（L208）、`send_message`（L241，**已废弃**，直接返回 `DEPRECATED` 提示）、`client_chunk_log`（L338）、`client_stream_debug_log`（L358）。
- **服务端→客户端（房间规则，见 §4）**：
  - 用户房间广播：`user_{username}`（任务/流事件、通知），`user_{username}_terminal`（终端事件）
  - 终端专属房：`user_{username}_terminal_{session_name}`（订阅某个持久终端）
  - `terminal_subscribers` 全局房（`app_legacy.py:terminal_broadcast`，L1039-1048）
  - 全局广播（不带房间）：`token_update` / `todo_updated` / `edited_files_updated`

**业务事件类型**（emit 点见 §2）：
- 流式：`thinking_start/thinking_chunk/thinking_end`、`text_start/text_chunk/text_end`、`tool_intent/tool_preparing/tool_status/tool_start/update_action`、`api_request_start`、`stream_reset`、`error`、`task_stopped`
- 任务：`user_message`、`task_complete`、`system_message`、`quota_notice/quota_exceeded`
- 审批：`tool_approval_required/tool_approval_resolved`、`plan_approval_required/plan_approval_resolved`
- 系统：`system_ready`、`status_update`、`conversation_changed`、`conversation_list_update`、`conversation_resolved`、`conversation_loaded`、`token_update`、`context_warning`、`todo_updated`、`edited_files_updated`、`terminal_list_update`、`terminal_started`、`terminal_history`、`terminal_output_history`、`compression_state/compression_finished`、`trim_memory_message` 等

结论：**API 面 = 12 个蓝图 REST（其中 tasks_bp 是轮询主线）+ 1 个 Socket.IO 辅助通道**；消息发送与进度事件均已收敛到 `POST/GET /api/tasks` 轮询模型，Socket 通道退化为“实时补充 + 终端 + 系统广播”。

---

## 2. 事件通道现状

### 2.1 agent loop 进度事件的两条出口

**单一 sender 抽象**：任务线程内定义局部 `sender(event_type, data)`，把【写入事件流】与【Socket 实时推送】合并为一个动作：

- 主任务：`server/tasks/models.py::TaskManager._run_chat_task` 内 `def sender`（L907-931）：
  1. `self._append_event(rec, event_type, data)`——写入任务事件流（供轮询）
  2. `socketio.emit(event_type, data, room=f"user_{username}")`——同键实时推送
- Socket 兼容链路 `server/socket_handlers.py::handle_message` 里的 `send_to_client/send_with_activity`（L306/L324，仅发 socket、不写事件流）——**但该入口已废弃**。
- 通知池轮询器 `server/chat_flow_task_main.py::poll_completion_notifications`（L960 附近）与 `poll_multi_agent_notifications`（L1115 附近）各自内联 `sender`（只 socket，不写 task events；其中前置通知的**消息本身**随后续任务经 `_dispatch_completion_user_notice`/`inject_multi_agent_master_message` 注入历史/事件流，见 AGENTS.md §11.3）。
- 标题后台生成：`server/chat_flow_helpers.py::generate_conversation_title_background`（L100 起）——找到 running task 时 `task_manager._append_event(running_task, 'conversation_changed', ...)`（L105-120）；**若没有 running task 则只走 socket 广播（L130-138），REST-only 客户端收不到**。

### 2.2 事件流存储（idx/offset 机制）

实现文件：**`server/tasks/models.py`**：

- 存储：`TaskRecord.events: deque(maxlen=20000)`（L82），纯**进程内内存**，随任务存活；`TaskManager.cleanup_old_tasks(max_age_seconds=3600)`（L108）1 小时清理已完成任务。
- 序号：`TaskRecord.next_event_idx`（L92），`_append_event`（L762-782）为每条事件分配单调递增 `idx`（从 0 起，`{idx, type, data, ts}`）。**序号是 per-task 的，任务之间无全局序号**。
- 消费：`TaskManager.get_events_since(rec, offset)`（L226-234）在锁内快照后过滤 `e["idx"] >= offset`。
- API：`GET /api/tasks/<task_id>?from=<offset>`（`server/tasks/api.py::get_task_api` L229）→ 返回 `events + next_offset`（=最后一条 idx+1；无新事件则原样返回 offset）。
- 并发保护：追加与快照都在 `threading.Lock` 下，注释明确说明 deque 并发迭代会 `RuntimeError`（L227-231）。

### 2.3 断线重连后客户端如何追数据（三层机制）

1. **事件流偏移继续**：Web 前端 `static/src/stores/task.ts` 每 250ms 轮询 `?from=next_offset`（L167-177，`pollingIntervalMs: 250`）；CLI `cli/src/api.ts::pollTask`（L233）+ `cli/src/App.tsx::pollTask`（L491-516，700ms 间隔）。轮询与 socket 相互独立，socket 断线期间错过的块由下一次轮询补齐。
2. **对账接口兜底**：`GET /api/conversations/<id>/running-status`（`server/tasks/api.py` L56-108）聚合主 task/子智能体/后台命令/多智能体四类“正在运行”状态；前端每 2.5s 调一次 `startRunningStateReconcile`（`static/src/app/methods/taskPolling/probe.ts` L47-120）。若发现服务端有活动主任务而本地没在轮询 → `resumeTask(main_task_id, { resetOffset: true })` **从 0 全量重放事件**。
3. **前端去重**：`_processedEventIndices: Set`（`lifecycle.ts` L105-120 附近）以 `task_id:idx` 为 key 去重，重放事件第二次出现直接跳过。
4. **Socket 重连**：`static/src/composables/useLegacySocket.ts` — `reconnect_attempt` 时重新取一次性 socket-token（L588-597），`connect` 后 `resetAllStates` + `scheduleHistoryReload`（L599-624）从 REST 重拉对话历史。

**结论**：
- 有“每任务单调 idx”，**没有全局事件序号**；socket 事件不携带 idx，两条通道的合并去重完全靠前端（轮询模式甚至直接跳过 socket 流事件，`useLegacySocket.ts`：`if (ctx.usePollingMode && !ctx.waitingForSubAgent) return;`）。
- 事件流是**任务作用域 + 内存 + 1h TTL + 20000 条上限**，任务结束后即失去可追性；重启服务后所有事件流丢失，客户端只能靠对话文件重建最终态。

---

## 3. 状态归属清单（“真状态”在哪）

| 状态类别 | 真状态位置 | 佐证（文件:函数/行） |
|---|---|---|
| **对话历史（持久）** | 文件：`{data_dir}/conversations/{workspace_id}/{conversation_id}.json` + `index.json` | `utils/conversation_manager/base.py` L46/L54-55（conversations_root/index_file）、`metadata_mixin.py` L57（`f"{conversation_id}.json"`）、`crud_mixin.py` L298/L589（save/load_conversation） |
| **对话历史（运行时缓存）** | 每对话一个 WebTerminal 实例的 `context_manager.conversation_history` 内存列表 | `core/web_terminal.py` L673（`conversation_count: len(self.context_manager.conversation_history)`） |
| **终端缓存（key 设计）** | `server/state.py::user_terminals` 进程 dict，key=`username::workspace_id::conversation_id`（不传 conversation_id 时两段 key） | `server/context.py::_make_terminal_key` L57-70、`get_user_resources` L226-236 等；24h TTL 回收 `context.py` L856-935（`CONVERSATION_TERMINAL_TTL_SECONDS`） |
| **任务状态** | `server/tasks/models.py::TaskManager._tasks` 进程内 dict（`threading.Lock` 保护），TaskRecord 含 status/events/next_event_idx；1h 清理 | `models.py` L101-107（TaskManager.__init__）、L108（cleanup_old_tasks）；**进程重启丢失** |
| **审批状态（tool / user-question / plan）** | 三个**进程内单例**：`server/state.py` L30-32 `tool_approval_manager / user_question_manager / plan_approval_manager`；各自 `_items` dict + lock；绑定 `username + conversation_id` | `modules/tool_approval_manager.py` L11-30（create_request 字段）、`modules/plan_approval_manager.py` L22-58、`modules/user_question_manager.py`；**重启丢失** |
| **权限模式 / 执行环境 / 网络权限** | WebTerminal 实例属性（`current_permission_mode`、`current_execution_mode`、`host_network_permission`、`pending_*` 排队态）；对话 metadata 持久化（`pre_plan_*`、`pre_readonly_execution_mode` 等）；个性化默认值 | `core/main_terminal_parts/tools_policy.py` L236-330（get/set/switch_work_mode、set_permission_mode）；`core/main_terminal.py` L290-326（get_pending_runtime_modes / queue_*）；AGENTS.md §10.6 |
| **work_mode（plan/ask/execute）** | 同上：terminal 实例 `current_work_mode` + 对话 metadata `work_mode` + 个性化 `default_work_mode` | `tools_policy.py` L256-330；`server/chat/permission.py` L404-420（`GET/POST /api/work-mode`，运行中 409） |
| **子智能体任务** | `SubAgentManager`（`modules/sub_agent/manager.py` L52-105）挂在对话级 WebTerminal 上：内存 `tasks`/`_running_tasks`/`conversation_agents` + 文件 `{data_dir}/sub_agents.json`、`{data_dir}/sub_agent_tasks/` | manager.py L63-64（state_file/base_dir）、L105-122（_load_state/reconcile/restore） |
| **多智能体会话运行态** | `GLOBAL_MULTI_AGENT_STATES: Dict[conversation_id, MultiAgentState]` **进程级注册表**（特意从 manager 实例属性提升，根治多副本分裂） | `modules/multi_agent/state.py` L668-677（注册表 + RLock），L262-277（MultiAgentState.__init__：conversation_id/pending_master_messages），L605-612（to_snapshot） |
| **模型选择** | 对话 metadata `model_key` 为权威（对话级 terminal 加载恢复）+ terminal 实例 `model_key` + flask session `model_key`（仅工作区级） | `core/web_terminal.py` L503-508（加载对话模式）、L272-315（create 路径）；`core/main_terminal.py` L105-106（默认模型） |
| **personalization** | 文件：每工作区 `{data_dir}/personalization.json`（`default_run_mode`/`default_permission_mode`/`default_work_mode`/`review_agents` 等） | `modules/personalization_manager.py` L36（`PERSONALIZATION_FILENAME`）、L196（load_personalization_config）；`server/chat/settings.py` L279-327（`GET/POST /api/personalization`） |
| **运行时模式（fast/thinking/deep）** | terminal 实例 `run_mode`/`thinking_mode`/`reasoning_effort` + 会话同步 + drift 通知基线 | `core/web_terminal.py` L618-660（get_status）；`server/chat_flow_task_main.py` L1702-1732（collect_runtime_mode_drift） |
| **monitor 快照** | `server/monitor.py` 进程内缓存 `MONITOR_SNAPSHOT_CACHE`（上限 120） | `server/monitor.py` L10-60 |

总评：**“真状态”分散在 4 层**——①进程内内存对象（terminal、task_manager、三个 approval manager、GLOBAL_MULTI_AGENT_STATES、usage trackers、socket token/stop flags）、②运行态文件（conversation/*.json、sub_agents.json、personalization.json、settings.json）、③Flask session（登录态、workspace_id、model_key 等）、④前端本地（渲染消息列表、输入草稿 `input-draft` 文件在 `server/conversation.py` L298-327、对话类型 localStorage）。目前没有一个“系统级 owner”统一持有 session/run/approval/event。

---

## 4. 多客户端假设排查

### 4.1 Socket.IO room / 命名空间的使用（无 conversation 级隔离）

- 房间全部以**用户**为粒度：`user_{username}`、`user_{username}_terminal`、`user_{username}_terminal_{session_name}`、全局 `terminal_subscribers`（`server/socket_handlers.py` L62-73、L154-198；`server/app_legacy.py` L1039-1048）。
- **没有 per-conversation room/命名空间**。对话级终端事件靠 `server/context.py::_wrap_callback_with_conversation_id`（L72-86）把 `conversation_id` 塞进事件 payload，由**前端自行过滤**（`useLegacySocket.ts` 每个 handler 开头 `if (data?.conversation_id && data?.conversation_id !== ctx.currentConversationId) return;`）。
- 这意味着服务端不知道“哪个客户端正在看哪个对话”，广播只能“发给全用户，客户端自己挑”。多客户端（尤其不同对话的多标签页）会收到彼此对话的全部事件流量。

### 4.2 终端会话绑定

- socket 事件处理都通过 `get_terminal_for_sid` → `get_user_resources(username, conversation_id=...)` 解析终端（`server/socket_handlers.py` L156/L211）；`terminal_subscribe` 带 `conversation_id` 只是用于“选对终端实例”，房间名仍是用户级。
- 持久终端（`TerminalManager`）挂在**对话级 WebTerminal** 上，key=`username::workspace_id::conversation_id`（§3）——**同对话的多个客户端共享同一个 WebTerminal 实例**，这方向正确，但该实例是多写者可变对象：`attach_user_broadcast(terminal, username)` 在**每个请求**都会重绑回调（`context.py` L509 等），多端并发请求会互相重置 terminal 上的会话级状态（已用 conversation-bound 保护模型，但模式/message_callback 仍是共享可变面）。

### 4.3 socket token 发放互踩（多客户端缺陷）

`server/chat/terminal.py::issue_socket_token`（L62-80）：先 `prune_socket_tokens()`，然后**清空该用户名下所有旧 pending token**，再发一个新 token（45s TTL，`SOCKET_TOKEN_TTL_SECONDS`）。同一用户两个客户端几乎同时请求 socket-token 并握手时，后发请求会让先发 token 失效，导致先连的 socket 认证失败。单用户单端假设的残留。

### 4.4 断线即停任务假设

`server/socket_handlers.py::handle_disconnect`（L77-125）：只有“同用户**没有其他活跃连接**且**没有 REST running 任务**”时才置停标志/取消任务。这隐含“socket 是任务生命周期的一部分”的旧假设；当前主链路任务实际由 REST 发起（task_id 级 stop flag，`server/state.py::make_stop_keys` L140-155），socket 侧的 stop 只是兼容索引。Gateway 化后任务必须与任何客户端连接解耦。

### 4.5 审批弹窗状态

- 审批请求体绑定 `username + conversation_id`（非 sid）：`modules/tool_approval_manager.py::create_request` L16-45 —— 方向上利于任意同用户端审批（CLI 也能通过事件流 `tool_approval_required` 渲染审批 UI，`cli/src/App.tsx` L503-512）。
- 但**等待方**是任务线程对进程内存 dict 的轮询（`server/chat_flow_tool_loop.py::_wait_for_tool_approval` L232-244、`_wait_for_plan_approval` L294-303），审批状态无文件/DB 持久化，**服务重启后 pending 审批全部丢失**，等待中的任务线程只能得到 `approval_missing` 拒绝结果。
- 前端弹窗 UI（`PlanApprovalDialog.vue` 等）是各端本地渲染；多端同时打开同一对话时，审批事件会广播给所有端，谁先 answer 谁生效（同 username 校验，`tool_approval_manager.py::decide` L78-91），无“审批被某端认领”的概念。

### 4.6 stream 进行中的对话切换

前端已做双向防护（这是现状中做得最完整的部分）：
- `static/src/app/methods/taskPolling/lifecycle.ts`：conversation_id 不匹配丢弃（L50-90）、task_id 不匹配丢弃（L96-133）、`_processedEventIndices` 去重（L105-120）。
- `static/src/stores/task.ts` L223-236：轮询在途响应若发现 `currentTaskId` 已变则整包丢弃（stale-response-ignored）。
- `static/src/app/methods/conversation/action.ts` L99：“创建新对话只切换视图，不再取消后台任务；停止当前轮询避免事件串写”。
- 回到正在运行的对话时由 2.5s 对账循环 `resumeTask(resetOffset: true)` 全量重放（§2.3）。

### 4.7 其它隐含“单客户端”的点

- **输入草稿**：`/api/input-draft` 以 username/workspace 为粒度写文件（`server/conversation.py::_resolve_input_draft_path` L298-309），多端同用户共用会互相覆盖。
- **`active_polling_tasks: Dict[conversation_id, bool]`**（`server/state.py` L39）仍以“一个对话一个轮询者”建模。
- **CSS/前端状态**：`currentConversationType`/`newConversationType`/`sidebarConversationType` 等 localStorage 键（AGENTS.md §11.0）都是“每浏览器”视角，与服务器状态无关（对投影架构不构成阻塞，但说明端状态模型是单端假设的）。

---

## 5. 认证现状

| 链路 | 实现 | 佐证 |
|---|---|---|
| **Web 用户体系** | Flask session（cookie）+ `login_nonce` 双因子：`is_logged_in()` 要求 `session.username` 且 nonce 在 `state.active_login_nonces` 集合内；邮箱+密码 `user_manager.authenticate`；IP/账号维度限流与锁定（5 次/300s） | `server/auth_helpers.py` L15-28；`server/auth.py::login` L123-221；`server/security.py` L101-155 |
| **host-login** | `POST /host-login`：仅 `TERMINAL_SANDBOX_MODE=host` 且回环地址（127.0.0.1/::1/localhost）可调；免密创建 admin 会话 `username=host, role=admin, host_mode=True` | `server/auth.py::host_login` L224-270 |
| **CSRF** | `X-CSRF-Token` header（或表单字段）比对 session 内 token；`requires_csrf_protection`：`/api/*` 前缀全部受检（`CSRF_PROTECTED_PREFIXES`），`/api/v1/*` 与 `Bearer` 请求豁免；`/login /register /logout /host-login` 受检 | `server/security.py` L157-190；`server/state.py` L103-108（常量） |
| **API token（/api/v1）** | `/api/v1/*` 全部 `api_token_required`：`Authorization: Bearer <token>` → SHA256 匹配 `api_user_manager` 存储的 `token_sha256` → 写入 session + `g.api_username`；**两个例外** `GET /api/v1/models`、`GET /api/v1/health` 无鉴权 | `server/api_auth.py` L12-54；`server/api_v1.py` L718/L742（无装饰器） |
| **socket 认证** | `GET /api/socket-token` 发一次性 token（45s TTL，绑定 username + User-Agent fingerprint）；connect 时 `consume_socket_token` 一次性消费，失败则 disconnect | `server/chat/terminal.py` L64-80；`server/security.py` L191-214；`server/socket_handlers.py::handle_connect` L20-73 |
| **CLI 用的链** | host-login（无凭证，回环）→ 拿到 session cookie + CSRF token → 全部走 **Web API（非 /api/v1）**：`/api/status`、`/api/tasks` 轮询、`/host/workspaces` 等；REST 轮询 700ms | `cli/src/api.ts` L38-52（fetchCsrf）、L96（hostLogin）、L226-239（createTask/pollTask）、L310（默认 8091） |

要点：ClI（及未来 Desktop）复用“会话 cookie + CSRF”的**宿主态**，不是独立的运行时身份；`/api/v1` Bearer token 体系与 Web/CLI 会话体系**并存但互不相通**（API 用户有自己的工作区目录 `api/users/`，见 AGENTS.md §1.5.1）。Android 是 WebView 壳（`android-webview-app/`，Brige 走 `$BASE_URL`），认证跟随 Web 前端会话。

---

## 6. 差距清单（距“任意客户端投影同一状态”还差什么）

按严重度排序：

1. **无统一事件序号 / 无事件总线**。事件 idx 是 per-task 的（`TaskRecord.next_event_idx`，`server/tasks/models.py` L762-782），socket 事件不带 idx；任务之间、通道之间无法对齐全局时间序。Gateway 需要对话级（或全局）持久化事件日志 + 全局 seq/游标，客户端按游标订阅/追平。
2. **事件流是任务作用域、内存态、会蒸发**。`deque(maxlen=20000)` + 1h cleanup（`server/tasks/models.py` L108/L82）：任务结束/进程重启后事件流即失，后续客户端只能靠对话文件重建“最终态”，无法重放“过程”。断线追赶完全依赖前端“从 0 重放 + 去重”的脆机制（`probe.ts::resumeTask(resetOffset)`）。
3. **审批/提问/计划批准 = 进程内存 + 绑定 username+conversation_id（无端认领）**。`tool/user_question/plan_approval_manager` 三个单例（`server/state.py` L30-32）重启即失；等待侧在任务线程轮询内存 dict（`server/chat_flow_tool_loop.py` L232-244）。Gateway 需要把 approval 做成持久化实体 + 审计 + 多端可见的“认领/副作用”语义；目前任何同用户端可 answer，但无“哪个端在展示、谁决定”的记录。
4. **状态真源分四层、无唯一 owner**（§3 总评）。尤其工作区级 vs 对话级 WebTerminal、`GLOBAL_MULTI_AGENT_STATES`（key=conversation_id）、task_manager、session 四处都可写“运行模式/模型/任务”相关状态，未来任一 owner 化都要先收敛。
5. **终端缓存 key 已按对话隔离（好），但实例是共享可变对象**。`username::workspace_id::conversation_id`（`server/context.py::_make_terminal_key`）让同对话多端共享实例；但每请求 `attach_user_broadcast` 重绑回调 + `current_conversation_id`/`model_key`/模式属性可变，多端并发读写下仍有覆盖面（主任务写入已由 `server/main_task_gate.py` 单写者门闸保护，见 AGENTS.md §12——这是 gateway 唯一 owner 的一个雏形）。
6. **广播粒度是 user 房间，无 conversation 级订阅/投影**。服务端无法回答“谁在看对话 X”；`_wrap_callback_with_conversation_id` + 前端过滤是补丁不是投影。Gateway 需要 conversation 级 topic/room + 客户端显式订阅。
7. **socket-token 发放互踩**（§4.3）：`issue_socket_token` 清空同用户旧 token，多标签/多端并发建连会互相踢。
8. **“停止/活动”仍假设任务绑定连接**：`handle_disconnect` 的 `has_other_connection` / REST running 任务判断（`server/socket_handlers.py` L77-125）；Gateway 下任务生命周期必须独立于任何连接。
9. **REST-only 客户端存在事件盲区**：标题更新无 running task 时只走 socket（`server/chat_flow_helpers.py` L130-138）；`token_update/todo_updated/edited_files_updated` 是全局广播不一定进任务事件流（terminal_broadcast，`app_legacy.py` L1039-1048）。
10. **认证两套并存**：session+CSRF（Web/CLI/host-login）与 Bearer token（/api/v1）不互通；真要“多端以同一身份投影”，需要统一身份/会话模型（token 化 + 会话复用到 socket）。

---

## 现状速查表

| # | 项 | 现状一句话 |
|---|---|---|
| 1 | 传输主线 | REST 250ms(Web)/700ms(CLI) 轮询 `GET /api/tasks/<id>?from=idx`；Socket.IO 是兼容/辅助通道（`tasks/api.py`、`socket_handlers.py`） |
| 2 | 事件序号 | 仅 per-task `next_event_idx`（0 起，deque 20000 上限）；**无全局序号**；socket 事件无 idx（`tasks/models.py` L762-782） |
| 3 | 断线/换端追赶 | 2.5s `running-status` 对账 → `resumeTask(resetOffset=true)` 从 0 重放 + 前端 `task_id:idx` 去重（`probe.ts`、`lifecycle.ts`） |
| 4 | 对话历史 | 文件 `{data}/conversations/{ws}/{conv}.json` 权威；每对话 WebTerminal 内存缓存，24h TTL 回收（`conversation_manager/base.py`、`context.py` L856） |
| 5 | 任务状态 | `TaskManager` 进程内 dict，1h 清理、重启丢失（`tasks/models.py` L101-108） |
| 6 | 审批状态 | `tool/user_question/plan_approval_manager` 三个进程内单例，绑定 username+conversation_id，重启丢失（`state.py` L30-32） |
| 7 | 子智能体/多智能体 | SubAgentManager 挂对话级 terminal（内存+sub_agents.json）；MultiAgentState 走进程级 `GLOBAL_MULTI_AGENT_STATES`（key=conv_id）（`sub_agent/manager.py`、`multi_agent/state.py` L668） |
| 8 | 模式（work/permission/execution） | terminal 实例属性 + 对话 metadata + personalization.json 默认值；运行中切换用 pending 队列（`tools_policy.py`、`main_terminal.py` L290-326） |
| 9 | 认证 | Web=session+login_nonce+CSRF；host-login=回环免密 admin；/api/v1=Bearer token；socket=一次性 token 45s（`auth.py`、`api_auth.py`、`security.py`） |
| 10 | 多客户端 | 同用户同对话共享同一 WebTerminal；广播按 user 房间、前端按 conversation_id 过滤；socket-token 并发互踩、无 conversation 级订阅（`socket_handlers.py`、`chat/terminal.py` L64-80） |

---

### 附录：本报告结论的不确定度声明
- “状态分布”“事件序号”“认证实现”等结论来自**直接代码阅读**（证据如上），确定度最高。
- “重启丢失”“断线追数行为”“多标签互踩”等结论是**代码路径推演**，未经运行时复现验证；其中“重启丢失”由类定义（无文件落盘）可基本确证，“socket token 互踩”依赖时序竞争，标注为**很大概率**。
- 任务要求只读盘点，未运行服务；如需确认运行时行为，应以实测为准。
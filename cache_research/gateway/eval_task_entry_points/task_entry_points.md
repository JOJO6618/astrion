# create_chat_task 调用点盘点与「显式运行时上下文」迁移难度评估

- 分析范围：`server/` 目录（只读分析，未修改任何文件）
- 对象：`TaskManager.create_chat_task`（`server/tasks/models.py:132`）的全部 5 处真实调用点
- 目标：评估「抽 RuntimeService 公共入口（显式运行上下文）」的改造范围与难度
- 结论可信度：代码级事实（函数/行号/参数）经逐一阅读，**百分百确定**；迁移难度与工作量为分析师判断（清晰标注为估计）

> **审阅注释（2026-09-07｜证据口径）**：本报告为静态分析，“百分百确定”不适用于可达性、间接依赖和完整行为。下表按 5 类来源列出 6 处调用；数量应统一按“类别”或“代码位置”表述。下面对 Socket 活跃性的判断已有交叉核验修正。

---

## 0. 一张图看懂执行链（5 个调用点共用）

```
调用点(5 处)
   └─> task_manager.create_chat_task(...)            models.py:132
         ├─ ① 参数归一化 + 单对话互斥检查            models.py:151-172
         ├─ ② 构造 TaskRecord + session 快照         models.py:174-209  ← 隐式上下文在这里被固化
         └─ ③ threading.Thread(_run_chat_task).start() models.py:212    ← 每个任务一个 daemon 线程
               └─ _run_chat_task(rec, images, videos, files)            models.py:785-1096
                     ├─ test_request_context + 灌 session + get_user_resources  models.py:794-810  ← 唯一 Flask 隐式依赖
                     ├─ ensure_conversation_loaded / 模式覆盖 ...
                     └─ run_chat_task_sync(...)                          models.py:985
                           └─ process_message_task(...)                  chat_flow.py:133（run_chat_task_sync = process_message_task，chat_flow.py:280）
                                 └─ loop.create_task(handle_task_with_sender(...))  chat_flow.py:159-166
                                       └─ handle_task_with_sender(...)   chat_flow_task_main.py:1478 ← 真正的执行主循环
                                             └─ 尾部按需 spawn 两个通知轮询线程（socketio.start_background_task）L2643 / L2695
```

**要点**：5 处调用点中**没有任何一处直接 spawn `handle_task_with_sender`**——全部经由
`create_chat_task → 线程 → _run_chat_task → run_chat_task_sync → process_message_task → asyncio.create_task(handle_task_with_sender)`。
只有两处例外（非本次 5 点范围，但相关）：
- `chat_flow_task_main.py:664`：`_dispatch_completion_user_notice` 在 `create_chat_task` 抛异常时的**回退路径**，用 `asyncio.create_task(handle_task_with_sender(...))` 直接在当前事件循环执行；
- `socket_handlers.py:345` → `start_chat_task`（chat_flow.py:267，`socketio.start_background_task(process_message_task, ...)`）：WebSocket 实时消息**绕开 task_manager**、直连执行链的并行路径。

> **审阅注释（2026-09-07｜R3 可达性修正）**：`server/socket_handlers.py:259` 已直接返回 `DEPRECATED`，上述 Socket 路径属于不可达遗留代码，不应计为当前活跃入口。完成通知异常回退则仍可达（`server/chat_flow_task_main.py:645–675`），会绕过新的任务记录；外围已有门闸预占，不能仅据此断言并发写入错误。迁移时应明确异常回退的受理记录、取消关联和门闸收尾，而非默认原样保留直接执行即可。

---

## 1. 五个调用点逐一分析

### 1.1 调用点①：`server/tasks/api.py:200`（create_task_api，POST /api/tasks）

| 维度 | 内容 |
|---|---|
| 触发来源 | **Web HTTP POST 请求**（Flask 路由，真实 request context 内）。`@tasks_bp.route("/api/tasks")`（L110）、`@api_login_required`（L111）、`@rate_limited("chat_task_create",30,60)`（L112）、`def create_task_api`（L114） |
| 隐式上下文信息 | ① `username = get_current_username()`（L115）＝ `session["username"]`（auth_helpers.py:42-44）；② `workspace_id = session.get("workspace_id") or "default"`（L116）；③ **未传 `session_data`** → `create_chat_task` 内部直接快照真实 session（models.py:194-205）：`username / role / is_api_user / host_mode / host_workspace_id / workspace_id / run_mode / thinking_mode / model_key`；④ 请求体全来自 `request.get_json()`（L117） |
| 传给 create_chat_task 的参数 | L200-212：`username, workspace_id, message, images, conversation_id, videos=, model_key=, thinking_mode=, run_mode=, max_iterations=, message_source=, goal_mode=, skill_context_messages=, files=` |
| 其中来自隐式上下文的参数 | `username`（session）、`workspace_id`（session）、以及整个 session 快照（models.py:194-205，即 `record.session_data`） |
| handle_task_with_sender 触发方式 | 不自接 spawn：`create_chat_task`（L212 线程）→ `_run_chat_task` → `run_chat_task_sync` → `process_message_task` → `loop.create_task(handle_task_with_sender)`（chat_flow.py:159-166） |
| 迁移难点 | **小～中**。请求体字段已全部显式；只需把「session 快照」（models.py:194-205）改为构造显式上下文对象。注意 `get_current_username()` 不保证非空（None 时 create_chat_task 会照常登记，属既有行为，需在公共入口统一校验）。`role / is_api_user / host_mode` 三字段必须从 session 取出传入，否则后台线程会退化为网页默认语义 |

### 1.2 调用点②：`server/api_v1.py:320`（send_message_api，POST /workspaces/\<workspace_id\>/messages）

| 维度 | 内容 |
|---|---|
| 触发来源 | **Web HTTP POST 请求**（Bearer Token API）。`@api_v1_bp.route(...)`（L256）、`@api_token_required`（L257）、`@rate_limited("api_v1_send_msg",20,60)`（L258）、`def send_message_api`（L259） |
| 隐式上下文信息 | ① `api_token_required`（api_auth.py:47-49）把 **`session["username"]`、`session["role"]="api"`、`session["is_api_user"]=True`** 写入 session（复用现有上下文/工作区逻辑）；② `username = session.get("username")`（L260）；③ `workspace_id` 来自 URL 路径（`_resolve_workspace` L221-225 → `state.api_user_manager.ensure_workspace`）；④ **未传 `session_data`** → `create_chat_task` 快照 session（models.py:194-205），其中 `is_api_user=True、role="api"` 是 API 语义的关键字段 |
| 传给 create_chat_task 的参数 | L320-329：`username, workspace_id=ws.workspace_id, message, images, conversation_id, model_key=, thinking_mode=, run_mode=, max_iterations=`（**无 session_data、无 files/videos**） |
| 其中来自隐式上下文的参数 | `username`（session）、`role/is_api_user/host_mode/host_workspace_id/workspace_id/run_mode/thinking_mode/model_key`（session 快照，models.py:194-205） |
| handle_task_with_sender 触发方式 | 同 1.1（线程 → run_chat_task_sync → loop.create_task） |
| 迁移难点 | **中**。最大风险：`is_api_user=True / role="api"` 必须显式进入运行时上下文，否则后台任务线程内 `get_user_resources`（context.py:462）会把 API 用户当网页用户走 `user_manager` → 工作区解析错乱或抛错。另外 API 用户的 `get_user_resources` 要求 `workspace_id` 非空（context.py:465-468），本入口已满足。`terminal.user_role="api"`（context.py:525/538）还会影响配额/角色语义，上下文必须携带 `role` |

### 1.3 调用点③：`server/workflow_runtime_api.py`（工作流入口，2 处子调用）

**3a：L184（api_activate_workflow，POST /api/workflow/activate）**

| 维度 | 内容 |
|---|---|
| 触发来源 | **Web HTTP POST 请求**（slash 菜单激活）。route（L38）、`@api_login_required`（L39）、`@with_terminal`（L40）、`def`（L41）。真实 request context 内 |
| 隐式上下文信息 | ① `username`：`@with_terminal`（context.py:685-711）内 `get_current_username()`＝session；② `terminal/workspace`：`@with_terminal` 内 `get_user_resources`（context.py:699）——该函数自身大量读 session（`host_mode` L239、`host_workspace_id/workspace_id` L246-247、`is_api_user` L462、`run_mode/thinking_mode` L368-369、`model_key` L122）；③ `session_data` **显式构造**（L168-183：username、message_source="workflow"、main_task_gate_token、auto_user_message_event、auto_user_message_payload）——但**未含 host_mode** → `create_chat_task` L185-187 仍从真实 session `setdefault("host_mode", session.get("host_mode"))` 读取 |
| 传给 create_chat_task 的参数 | L184-192：`username, workspace_id(workspace.workspace_id), prompt, [], conversation_id, message_source="workflow", session_data=session_data` |
| 其中来自隐式上下文的参数 | `username`（session）；`session_data["host_mode"]`（create_chat_task L185 从真实 session 补）；gate token 预占在真实 request context 内完成（try_acquire_main_task_gate） |
| handle_task_with_sender 触发方式 | 同主链路（create_chat_task → 线程） |
| 迁移难点 | **小～中**。已约 80% 显式化。剩余：host_mode 的 session 读取（L185）、以及「门闸 token 预占 → 随 session_data 移交 → 任务线程认领释放」（main_task_gate.py）的隐式语义必须保留在运行时上下文中 |

**3b：L264（api_deactivate_workflow，POST /api/workflow/deactivate）**

| 维度 | 内容 |
|---|---|
| 触发来源 | **Web HTTP POST 请求**（用户主动"停止工作流"）。route（L210）、`@api_login_required`（L211）、`@with_terminal`（L212）、`def`（L213）。内部先 `wsm.poll_notices()` 取柔性通知；**仅当主任务门闸可预占**（try_acquire_main_task_gate 成功）时立即派发一轮任务，否则通知留池（多一轮轮询器/工具循环消费） |
| 隐式上下文信息 | 同 3a：username/terminal/workspace 来自 `@with_terminal`；`session_data` 显式（L251-262，含 main_task_gate_token），host_mode 由 create_chat_task L185 从真实 session 补 |
| 传给 create_chat_task 的参数 | L264-272：`username, workspace_id, notice_text, [], conversation_id, message_source="workflow", session_data=session_data` |
| 其中来自隐式上下文的参数 | `username`（session）；host_mode（L185 补） |
| handle_task_with_sender 触发方式 | 同主链路 |
| 迁移难点 | **小～中**。与 3a 同模式；额外注意派发失败时的 `restore_notices` 回滚与门闸释放语义（L277-303）——显式化改造不得吞掉这些副作用 |

### 1.4 调用点④：`server/chat_flow_task_main.py:613`（完成通知派发）

| 维度 | 内容 |
|---|---|
| 触发来源 | **内部后台轮询线程（任务完成通知派发链）**。`_dispatch_completion_user_notice`（L521，def）被 `poll_completion_notifications`（L961，L1064 处 await 调用）触发；该轮询器由 `handle_task_with_sender` 尾部（L2643）`socketio.start_background_task(run_completion_poll)` 在**独立线程**中启动（自带 asyncio 新事件循环 run_until_complete，5s 间隔轮询、最长 1h）。启动条件：主任务结束时检测到子智能体/后台命令仍在运行或有待通知项/工作流待通知（L2610-2626）。**触发源头仍是用户某次会话产生的后台工作者完成事件**，但执行时点与主请求已完全解耦 |
| 隐式上下文信息 | **无 Flask session/request 依赖**（后台线程内无请求上下文）。`session_data` 由 `web_terminal` 属性显式构造（L570-576）：`username / role=web_terminal.user_role / is_api_user=(user_role=="api") / host_mode=(workspace.username=="host") / host_workspace_id / workspace_id / run_mode / thinking_mode / model_key`。唯一残留：create_chat_task L185 的 `session.get("host_mode")` 在无上下文时抛 RuntimeError、被 except 吞掉后走显式值——**无副作用但属隐式残留** |
| 传给 create_chat_task 的参数 | L613-623：`username, workspace_id, user_message, [], conversation_id, model_key=session_data.get(...), thinking_mode=session_data.get(...), run_mode=session_data.get(...), session_data=session_data`（含 main_task_gate_token（L584，轮询器预占移交）、auto_user_message_event、auto_user_message_payload、preceding_user_notices） |
| 其中来自隐式上下文的参数 | 无（全部来自 web_terminal/workspace 已显式值） |
| handle_task_with_sender 触发方式 | 主链路（create_chat_task → 线程）。**另有回退**：L664 `asyncio.create_task(handle_task_with_sender(...))`，当 create_chat_task 抛异常时在当前轮询事件循环内直接执行（L663-674） |
| 迁移难点 | **小（五处中最顺）**。已全显式；只需把「web_terminal/workspace 属性 → session_data」的构造提取为共享 helper（如 `RuntimeContext.from_terminal(terminal, workspace, username, ...)`），并保留「gate token 移交 + 回退直接执行」两条语义 |

### 1.5 调用点⑤：`server/chat_flow_task_main.py:1416`（多智能体 idle 派发）

| 维度 | 内容 |
|---|---|
| 触发来源 | **内部后台轮询线程（多智能体 pending 消息派发）**。`_dispatch_multi_agent_idle_messages`（L1247，def）被 `poll_multi_agent_notifications`（L1101，L1189 处 await 调用）触发；该轮询器由 handle_task_with_sender 尾部（L2695）`socketio.start_background_task(run_ma_poll)` 在**独立线程**中启动。仅多智能体模式；主对话空闲且 `MultiAgentState` 有 pending_master_messages 时 drain 并触发新一轮工作（与调用点④的轮询器完全分离，避免竞争单工作区互斥，见 L2646 注释） |
| 隐式上下文信息 | **无 Flask session/request 依赖**。`session_data` 显式构造（L1351-1360，同调用点④模式）；`task_type="notice"`（L1427）用于**绕过普通 chat 任务的单对话互斥**（models.py:160-172 只对 task_type=="chat" 做互斥） |
| 传给 create_chat_task 的参数 | L1416-1428：`username, workspace_id, last["text"], [], conversation_id, model_key=session_data.get(...), thinking_mode=session_data.get(...), run_mode=session_data.get(...), session_data=session_data, task_type="notice"` |
| 其中来自隐式上下文的参数 | 无（显式） |
| handle_task_with_sender 触发方式 | 主链路（create_chat_task → 线程）。异常时直接 raise 回轮询器由调用方处理（L1429-1437） |
| 迁移难点 | **小**。注意保留 `task_type="notice"` 的互斥豁免语义与 `preceding_user_notices` 回放（L1398-1411）；无 gate token 参与 |

### 1.6 汇总表

| # | 文件:行 | 入口函数 | 触发来源 | 是否有隐式 Flask 依赖 | 显式化程度 | 迁移难度 |
|---|---|---|---|---|---|---|
| ① | tasks/api.py:200 | create_task_api | Web HTTP POST | 强（username/workspace_id/session 快照 L194-205） | 低（无 session_data） | 小～中 |
| ② | api_v1.py:320 | send_message_api | Web HTTP POST（Bearer token） | 强（session["username"/"role"/"is_api_user"]、session 快照） | 低（无 session_data） | **中** |
| ③a | workflow_runtime_api.py:184 | api_activate_workflow | Web HTTP POST | 中（username；host_mode 补读 L185；gate token 隐式移交） | 高（session_data 显式） | 小～中 |
| ③b | workflow_runtime_api.py:264 | api_deactivate_workflow | Web HTTP POST | 中（同上；+通知池/门闸回滚） | 高（session_data 显式） | 小～中 |
| ④ | chat_flow_task_main.py:613 | _dispatch_completion_user_notice | 内部后台轮询线程（socketio.start_background_task，L2643） | 极弱（仅 L185 异常吞掉的 session.get 残留） | 极高（全显式） | **小** |
| ⑤ | chat_flow_task_main.py:1416 | _dispatch_multi_agent_idle_messages | 内部后台轮询线程（L2695） | 极弱（同上） | 极高（全显式 + task_type="notice"） | **小** |

---

## 2. `create_chat_task` 函数本身与执行链路的关系

**位置**：`server/tasks/models.py:132-215`（TaskManager 方法）。

**签名**（L132-149）：
```python
def create_chat_task(self, username, workspace_id, message, images, conversation_id,
                     videos=None, model_key=None, thinking_mode=None, run_mode=None,
                     max_iterations=None, session_data=None, message_source=None,
                     goal_mode=False, skill_context_messages=None, files=None,
                     task_type="chat") -> TaskRecord
```

**做了什么**（按顺序）：
1. **参数归一化**（L151-158）：run_mode 白名单校验（fast/thinking/deep，非法即抛 ValueError）；task_type 归一化（默认 "chat"）。
2. **单对话互斥**（L160-172）：`task_type=="chat"` 时，同一对话（业务 id 去 `conv_` 前缀后比对）存在 `status ∈ {pending, running}` 且 task_type=="chat" 的任务 → 抛 `RuntimeError`（前端 409）。完成通知/多智能体派发用 `task_type="notice"` 豁免。
3. **构造 TaskRecord**（L173-175）：生成 uuid task_id（L173），`TaskRecord(...)`（L174）初始 status="pending"。
4. **session 快照**（L177-209）——**隐式上下文核心**：
   - `session_data is not None`（显式分支，工作流/通知/多智能体调用）：L178-191，`setdefault workspace_id/message_source/goal_mode/skill_context_messages`，并 **仍读 session**：L185 `snapshot.setdefault("host_mode", session.get("host_mode"))`、L187 `host_workspace_id ← session.get(...) or workspace_id`（无请求上下文时抛 RuntimeError 被 except 吞掉，走显式值）。
   - `session_data is None`（隐式分支，Web/API v1 调用）：L193-205，从**真实 Flask session** 快照 `username / role / is_api_user / host_mode / host_workspace_id / workspace_id / run_mode / thinking_mode / model_key`（均 session.get，可能为 None）+ message_source/goal_mode/skill_context_messages。
   - 快照结果存 `record.session_data`，后台线程据此**重建** session。
5. **登记 + 起线程**（L210-214）：锁内注册 `self._tasks[task_id]`；`threading.Thread(target=self._run_chat_task, args=(record, images, videos or [], files or []), daemon=True)`；status="running"；`thread.start()`；返回 record。

**与执行链路的关系**：`create_chat_task` 是 **「受理（互斥）+ 快照（隐式上下文固化）+ 执行（spawn 线程）」三合一的公共受理入口**。
线程内 `_run_chat_task`（L785-1096）完成：重建 session（L794-810）→ 解析终端/工作区 → 加载对话 → 覆盖模型/模式 → 注入 user_message 事件 → 挂 sender（事件入队 + socketio 推送）→ `run_chat_task_sync`（= `process_message_task`，chat_flow.py:280/133）→ asyncio `handle_task_with_sender`（chat_flow.py:159-166）→ 结束态处理（stopped/succeeded + work_timer + gate 兜底释放）。
因此「把 5 个调用点收敛到 RuntimeService」在实现上等价于：**保留 create_chat_task 的受理/执行骨架，把第 4 步的 session 快照替换为显式上下文对象**。

---

## 3. `test_request_context` 依赖清单（需要显式化的字段列表）

**唯一位置**：`server/tasks/models.py:794-810`（`server/` 与 `modules/` 全域 grep 仅此一处）。

**包装结构**：
```
792  # 为后台线程构造最小请求上下文，填充 session
793  from server.app import app as flask_app
794  with flask_app.test_request_context():
795      try:
796          for k, v in (rec.session_data or {}).items():   # 把快照灌回 session
797              if v is not None:
798                  session[k] = v
799          if session.get("host_mode"):
800              session["workspace_id"] = workspace_id
801              session["host_workspace_id"] = session.get("host_workspace_id") or workspace_id
802-807        write_host_workspace_debug(...)
808      except Exception:
809          pass
810      terminal, workspace = get_user_resources(username, workspace_id=workspace_id, conversation_id=rec.conversation_id)
```

**为什么需要它**：`_run_chat_task` 跑在**裸 `threading.Thread`** 里，没有 Flask 请求上下文。而 `get_user_resources`（context.py:221）内部以 `has_request_context()` 为开关读取 session：
- L239 `host_mode_session = bool(session.get("host_mode"))` → 决定是否走**宿主机多工作区解析路径**；
- L246-247 `session.get("host_workspace_id") / session.get("workspace_id")` → 宿主机路径下 workspace 选择兜底；
- L462 `is_api_user = bool(session.get("is_api_user"))` → 决定走 `api_user_manager` 还是 `user_manager`（**最关键，错选会串工作区**）；
- L368-369 `session.get('run_mode') / session.get('thinking_mode')` → 新建 terminal 的默认档；
- L122/L175-178 `session.get("model_key")`（`_apply_workspace_personalization_preferences`）→ 恢复会话模型；
- auth_helpers.py:51-52 `get_current_user_role` 读 `session["role"]` → 决定 `terminal.user_role`（context.py:525/538，影响配额/角色语义）。

若去掉该包装，`has_request_context()` 恒 False → host_mode、is_api_user 恒 False → 宿主机多用户、API token 用户的任务会解析到错误工作区，或直接 NoWorkspaceError。**它本质上是「把调用时点（请求上下文内）的身份/偏好搬运到异步执行时点（后台线程）的桥」。**

**包装体内读取/写入的 Flask 隐式状态**：
| 项 | 位置 | 方向 | 用途 |
|---|---|---|---|
| session[\*]（全部 session_data 键） | models.py:796-798 | 写 | 重建请求上下文 |
| session["workspace_id"] | models.py:800 | 写 | host_mode 下回写 |
| session["host_workspace_id"] | models.py:801 | 写+读 | host_mode 下回写 |
| session["host_mode"] | context.py:239 | 读 | 宿主机路径选择 |
| session["host_workspace_id"] | context.py:246 | 读 | 宿主机 workspace 兜底 |
| session["workspace_id"] | context.py:247 | 读 | 同上 |
| session["run_mode"] | context.py:368 | 读 | 新建 terminal 默认档 |
| session["thinking_mode"] | context.py:369 | 读 | 同上 |
| session["is_api_user"] | context.py:462 | 读 | api vs web 资源管理器 |
| session["model_key"] | context.py:122 | 读 | 恢复会话模型 |
| session["role"] | auth_helpers.py:51 | 读 | terminal.user_role |

**消除它需要显式传入的字段清单（RuntimeContext 最小字段集，9 项）**：
1. `username`（已显式：rec.username，L787）
2. `workspace_id`（已显式：rec.workspace_id，L788；get_user_resources L810 已显式传参）
3. `host_mode: bool` ← 现读 session_data["host_mode"]；决定宿主机路径
4. `host_workspace_id: Optional[str]` ← 现读 session_data；宿主机多工作区兜底
5. `is_api_user: bool` ← 现读 session_data；**决定 api_user_manager / user_manager**（最高风险）
6. `role: str` ← 现读 session_data；写入 terminal.user_role
7. `run_mode: Optional[str]` ← 现读 session_data；新建 terminal 默认档
8. `thinking_mode: Optional[bool]` ← 现读 session_data；同上
9. `model_key: Optional[str]` ← 现读 session_data；恢复会话模型

**附注（不需要显式化的）**：`message_source / goal_mode / skill_context_messages / main_task_gate_token / auto_user_message_event / auto_user_message_payload / preceding_user_notices` 已由调用方经 `session_data` 显式传入，可原样并入 RuntimeContext，只是换一个容器。

> **审阅注释（2026-09-07｜R4 职责分组）**：上述清单说明迁移时不能丢字段，不表示应全部并入一个公共 RuntimeContext。区分可信身份/资源范围、本次任务参数、内部执行信息；门闸 token 与通知回滚信息保持内部生成和传递，不能由普通客户端任意提交。迁移契约还需定义默认值、对话配置和请求覆盖的解析优先级，避免只替换字典名称。

**改造路径**：把 models.py:794-810 替换为：
`context = RuntimeContext.from_session_snapshot(rec.session_data)`（或直接由调用方传入）
`terminal, workspace = get_user_resources_explicit(username, workspace_id, conversation_id, runtime_context=context)`
并在 context.py 中为 `get_user_resources` 增加显式参数变体（web 路径保持不变）。

---

## 4. 总体结论

### 4.1 收敛为公共入口需要动多少处

| 面 | 位置 | 改动性质 | 量级 |
|---|---|---|---|
| 调用方（5 处 6 个函数位置） | tasks/api.py:200、api_v1.py:320、workflow_runtime_api.py:184 与 264、chat_flow_task_main.py:613 与 1416 | 把「各自取 session / 拼 session_data」改为构造统一 RuntimeContext 对象 + 调公共入口 | 6 处，各 **小**（合计小～中） |
| 受理入口 | models.py:132-215（create_chat_task） | 第 4 步 session 快照（L177-209）显式化；暴露公共入口签名（显式上下文入参） | **中** |
| 后台执行桥 | models.py:794-810（test_request_context） | 拆除，改传显式上下文调用资源解析 | **中** |
| 资源解析 | context.py:221 `get_user_resources` 及其内部 8-10 处 `session` 读取（L239/246/247/368/369/462/122 + auth_helpers role） | 增加显式上下文参数变体，has_request_context 分支与显式分支并存 | **中～大**（最高风险） |
| 关联但不在本 5 点内 | socket_handlers.py:345 → start_chat_task（socket 直连执行链）；chat_flow_task_main.py:664（通知回退直接执行 handle_task_with_sender）；main_task_gate 门闸移交/认领/释放语义 | 若要「全入口收敛」，socket 实时路径也需接入公共入口（否则公共入口不覆盖主交互通道） | 另计，**中** |

### 4.2 核心难点排序（按风险/工作量）

1. **`get_user_resources` 的 session 隐式读取参数化**（context.py）
   这是拆除 test_request_context 的前提，也是风险最高的点：`host_mode`（宿主机多工作区）与 `is_api_user`（API vs Web 资源管理器）两处分支选错即**静默串工作区**。需要新增显式上下文变体、保持 web 路径不动，改动面涉及其调用链上 `_apply_workspace_personalization_preferences`、`get_current_user_role` 等。
2. **API 身份字段（is_api_user / role）的正确传递**（api_v1.py 入口）
   调用点②是唯一没有 session_data 的 API 来源；快照逻辑（models.py:194-205）移到显式上下文后，必须保证这两个字段不丢、不误判，否则 API 用户后台任务退化为网页语义。
3. **main_task_gate 门闸移交语义**（workflow_runtime_api / 通知轮询链）
   「预占 → token 随 session_data 移交 → 任务线程认领 → finally 释放 / 失败回滚（restore_notices）」是跨线程的隐式协议，RuntimeContext 必须原样承载 token 与失败回滚语义。
4. **`task_type="notice"` 互斥豁免与单对话互斥的语义保持**（chat_flow_task_main.py:1416）
   多智能体 idle 派发依赖 notice 跳过 chat 互斥（models.py:160-172）；若公共入口重排互斥规则，需防止完成通知/多智能体链路的并发回退。
5. **各入口身份取数的三套来源统一**（web session / token session / web_terminal 属性）
   ①读真实 session、②读 token 注入的 session、④⑤读 web_terminal 属性——三者语义等价但取数路径不同，需收敛为同一个 `RuntimeContext.from_*` 构造 helper，避免迁移后行为漂移。

### 4.3 一句话总结

5 个调用点本身**都不难迁**（④⑤已全显式、①②只需打包 session 字段、③已 80% 显式），真正的大头在 `server/tasks/models.py` 的快照 + `test_request_context`（唯一隐式桥）与 `server/context.py` 的资源解析显式化；若把 socket 实时路径也纳入收敛，则为 6 个入口、整体工作量**中～大**，建议分两步：先做「RuntimeContext 对象 + 显式受理签名（保留 test_request_context 兜底期兼容）」，再拆 `get_user_resources` 的 session 读取。

# Runtime ↔ Execution Plane 链路审核报告

- 审核日期：2026-09-08
- 审核方式：只读（grep/符号定位 + 精确行段阅读 + 隔离 import 实验 + 子进程验收测试运行观察）
- 审核对象：Gateway（server/runtime/）→ Agent Runtime（server/chat_flow*.py、core/main_terminal.py）→ Execution Plane（core/main_terminal_parts/tools_execution.py、modules/execution_plane/）
- 结论分级：✅属实 / ⚠️部分属实 / ❌不属实 / 未验证
- 验收测试运行结果（本机 venv 实测）：
  - `check_lifecycle` → **PASSED (1.6s)**（无 Flask app 真实生命周期）
  - `check_fake_exec` → **PASSED (0.7s)**（替身执行 E1-E4，零真实副作用）
  - `check_chain` → **PASSED (2.7s)**（双客户端发现/观察/取消 + 历史 + 审批）
  - `check_approval_wait` → **PASSED (3.5s)**（执行中审批等待 → 公共入口批准 → 继续）

---

## 一、逐项声明验证结论

### 1. Runtime 层本体（server/runtime/context.py + service.py）

#### (a) 是否真的不 import flask —— ✅ 属实

证据：
- `server/runtime/context.py` 顶层 import 仅：`__future__` / `dataclasses` / `typing`（L14-17）。
- `server/runtime/service.py` 顶层 import 仅：`__future__` / `typing` / `modules.i18n.tr` / `server.runtime.context`（L13-18）。
- `server/runtime/` 全文 grep `flask|socketio`：唯一命中的是 `context.py:141` 的**注释文本**「本模块不 import flask」（说明性文档，非导入）。
- `grep -rniE "import flask|from flask|import socketio|from socketio" server/runtime/` → **零实际导入命中**。

import 实验佐证（本机 `.venv/bin/python3`）：
```
>>> import server.runtime.service
OK
>>> 'flask' in sys.modules        # False（未拉起）
>>> 'flask_socketio' in sys.modules  # False
>>> 'server.tasks' in sys.modules    # False（延迟导入未触发）
```
结论：Runtime 层本体零 Flask 依赖 ✅。注意：**模块本体不依赖 flask ≠ 调用链不依赖 flask**（见第 3 节 import 链实测）。

#### (b) TrustedPrincipal 只能由适配层构造？防自报 role 机制 —— ⚠️ 部分属实

证据：
- `context.py:20-41`：`TrustedPrincipal` 是普通 `@dataclass(frozen=True)`，**没有私有构造/工厂限制**——任何代码均可直接 `TrustedPrincipal(username=..., workspace_id=..., role="admin")` 自报角色。
- `validate()`（L30-34）只校验 `username`、`workspace_id` 非空，**不校验 role 白名单、不校验 is_api_user/host_mode 组合**。
- `RuntimeContext.from_terminal`（L79-105）与 `principal_from_session_snapshot`（L139-168）是仅有的两个"可信构造路径"：
  - `from_terminal`：host_mode 取 `workspace.username == "host"`、role 取 `terminal.user_role`（对话级 terminal 既有属性，非客户端自报）；
  - `principal_from_session_snapshot`：显式声明「调用方必须在完成认证后调用」，从适配层传入的 session dict 快照构造。
- 防御纵深确实存在：`service._resources_for_query`（service.py:269-283）校验 `principal.username == username` 与 `principal.workspace_id == workspace_id`，**跨身份/跨工作区查询抛 PermissionError**（测试 check_chain 第 5.5 节已验证此语义）。

结论：**「只能由适配层构造」是模块 docstring 与调用约定的强语义 + 查询侧纵深防御，不是 dataclass 层面的强制**。同一进程内任何持有代码写入权的调用方（或未来 Schedule payload）仍可直接构造高权限 principal。当前所有生产调用点均经由适配层（HTTP session 或 from_terminal），实战安全 ✅；但"防自报 role 的机制"属于**约定约束而非结构约束**，标注 ⚠️。

#### (c) service 方法集与文档声称一致性 —— ✅ 属实

文档声称：create_task / cancel_task / guidance / pending 队列 / get_task / get_task_events / list_runs / list_sessions / get_session_history / resolve_approval。

实测方法集（service.py）：
| 文档声称 | 实际方法 | 证据行 |
|---|---|---|
| create_task | `create_task(ctx)` + `_ensure_conversation_for_chat` | L47-57, L60-96 |
| cancel_task | `cancel_task(username, task_id)` | L100-103 |
| guidance | `enqueue_runtime_guidance` | L106-109 |
| pending 队列 | `enqueue_runtime_pending_message` / `remove_runtime_pending_message` / `promote_runtime_pending_to_guidance` / `get_runtime_pending_messages` | L112-124, L312-316 |
| get_task | `get_task(username, task_id)` | L318-320 |
| get_task_events | `get_task_events(username, task_id, offset)`（返回 events/next_offset/error/meta，含 window_start 缺口水位） | L323-342 |
| list_runs | `list_runs(username, workspace_id, conversation_id/status 筛选)`（复用 `task_public_payload` 单序列化实现） | L294-310 |
| list_sessions | `list_sessions(username, workspace_id, principal, ...)` | L158-174 |
| get_session_history | `get_session_history(username, workspace_id, conversation_id, principal)` | L177-192 |
| resolve_approval | `resolve_approval(kind, ...)`（tool/plan/question 三路由）+ `list_pending_approvals` | L127-156, L117-125 |

方法集与文档完全对齐 ✅（文档未列的 `_resources_for_query`/`_ensure_conversation_for_chat` 是私有实现细节）。

#### (d) service 是否确实不持有任务状态 —— ✅ 属实

证据：
- `service.py` docstring：L8-9「本服务只做受理裁决 + 显式上下文转发 + 控制委托，不持有任务状态」；L24「无状态：全部状态委托给 task_manager 单例」。
- 全部状态操作均**延迟导入**并委托：`from server.tasks import task_manager`（L52、L101、L107 等 8 处）、任务记录/事件流/门闸/保存保护由 TaskManager / main_task_gate / conversation_manager 承载。
- 方法体内无 `self._xxx` 状态字段；`RuntimeService` 类没有 `__init__` 之外的实例属性。
- 进程级单例 `runtime_service = RuntimeService()`（L353）可安全共享。

结论 ✅。

---

### 2. 执行链贯通验证

链：`server/tasks/models.py::create_chat_task`（L136）→ 线程 `_run_chat_task`（L771）→ 延迟 `from server.chat_flow import run_chat_task_sync`（L958-959）→ `run_chat_task_sync`（chat_flow.py:280-282）→ `process_message_task`（chat_flow.py:133）→ 门闸获取（L141）→ `handle_task_with_sender`（由 chat_flow_runner → chat_flow_task_runner re-export，定义于 chat_flow_task_main.py:1475）。

#### (a) session_data dict 零残留 —— ✅ 属实

grep `session_data`（server/ core/ modules/ utils/ config/ 全部生产代码，排除 __pycache__）命中仅 3 处，全部为**注释或 i18n key 文案，无 dict 状态**：
- `server/tasks/models.py:54` 注释「不再有 session_data 兼容快照 dict」；
- `server/tasks/models.py:148` `raise ValueError(tr("tasks.missing_session_data"))`（错误消息 key，语义已改为「缺少运行上下文，请通过公共任务入口提交」——见 modules/i18n_messages/api_tasks.py:105）；
- `server/runtime/service.py:11` 注释。
- `TaskRecord.__slots__`（models.py:35-66）为 `principal / task_params / directives / goal_progress` 四层结构化字段，`to_session_data()` 已不存在（grep 零命中）。

结论：生产代码 dict 零残留 ✅（i18n key 名称未改属命名瑕疵，不影响语义）。

#### (b) test_request_context 零残留 —— ✅ 属实

grep `test_request_context`（server/ core/ modules/）→ **零命中**（返回码 1 表示无匹配）。
`models.py:771-780` 任务线程装配：从 `rec.principal` 映射 `RuntimeIdentity(...)` 后直接 `get_user_resources(..., update_session=False, identity=identity)`——全程无 Flask 请求上下文。`get_user_resources` 内部（resources.py:140-143）：`explicit = identity is not None`、`can_write_session = (not explicit) and update_session and has_request_context()`——显式身份模式下与 session 完全解耦。

#### (c) RuntimeIdentity 驱动的资源解析链路 —— ✅ 属实

- `server/context/identity.py:19-31`：`RuntimeIdentity` dataclass（host_mode / host_workspace_id / is_api_user / role / preferred_* 偏好快照），docstring 明确「传入 get_user_resources 后，资源装配完全不读写 Flask session；为 None 时保持既有行为（HTTP 适配层在请求上下文内读取 session）」。
- `identity.py:38-44` `_resolve_user_role`：显式身份模式不触碰 session。
- `resources.py:118-123` `get_user_resources(username, workspace_id=..., update_session=True, conversation_id=None, identity=None)`——identity 参数化真实存在。
- 调用点：
  - 任务线程 `_run_chat_task`（models.py:782-795）传 identity；
  - service 查询面 `_resources_for_query`（service.py:276-288）传 identity；
  - service 补建对话 `_ensure_conversation_for_chat`（service.py:71-76）传 identity。

结论 ✅。

---

### 3. 独立启动验证

#### (a) 验收真实性 —— ✅ 属实（真生命周期，非假验收）

读取 `test/runtime_standalone_checks.py` 全文 + `test/test_runtime_standalone_lifecycle.py`：

- **没有将 `_run_chat_task` 替换为空 lambda**。薄壳 `test_runtime_standalone_lifecycle.py` 仅用 `subprocess.run([sys.executable, checks_script, mode])` 隔离执行（L26-36），验收逻辑全在 checks 脚本：
  - `check_lifecycle`：`from flask import has_app_context` 断言无 app 上下文 → 真实 `runtime_service.create_task(_make_ctx(...))` → 轮询 `get_task_events` 等 **`api_request_start` 出现（装配证据，历史/请求构造完成、模型调用前）** → 装配后主动取消 → 断言终态 → 断言对话级 terminal 存在（`resources.state.user_terminals.get(term_key) is not None`）→ 断言 `is_main_task_gate_busy(terminal)` 为 False（门闸释放）→ 结尾再断言无 app 上下文且 `ext.socketio.server is None`。
  - 审核 F4 的"假通过"修复点明确存在：`api_request_start 之前出现 error = 装配失败，验收必须明确失败`（L108-113）——杜绝"模型调用失败被误判为通过"。
  - `check_chain`：B 客户端不持 task_id，经 `runtime_service.list_runs` 发现 A 的活动 Run → 观察事件流等 user_message 落盘 → 跨端取消 → 会话 JSON 持久化断言 → 事件流 idx 单调/无重复/offset 续读 → 第二 Run 复用会话 → list_sessions/get_session_history + principal 不一致抛 PermissionError（跨用户 & 跨工作区两条）→ list_runs 归属/会话/状态筛选 → 审批公共入口（重复裁决返回现状、越权 PermissionError、未知类型 ValueError）。
  - `check_fake_exec`：真实 `terminal.handle_tool_call` 驱动 FakeExecutionBackend，断言替身收到 E1-E4 全部调用、输出结构被编排层消费、**零真实磁盘写入**（`assert not (_SMOKE_WORKSPACE / "fake_probe.txt").exists()`）、默认路径新建 terminal 的 `execution_backend is None`。
  - `check_approval_wait`：真实工具编排层 `_execute_tool_calls_impl` + approval 权限模式，线程执行中产生 `tool_approval_required` 事件 → 主线程经 `resolve_approval` 批准 → 替身收到命令、工具循环退出。

**本机实测（venv）**：四次检查全部 PASSED（lifecycle 1.6s / fake_exec 0.7s / chain 2.7s / approval_wait 3.5s）——独立 Gateway 在**无 Flask app 上下文**环境下完成了 受理→装配（api_request_start 证据）→事件→取消→终态→门闸释放 的全流程。模型调用因指向 127.0.0.1:9 连接拒绝失败属预期（外部依赖非验收对象），装配与生命周期真实发生。

#### (b) import 依赖链现状 —— ⚠️ 部分属实（存在实质缺陷）

实测追踪（import 钩子打印调用栈 + sys.modules 观察）：

**链路 A：`import server.runtime.service`** → 不拉起 flask / flask_socketio / server.tasks ✓

**链路 B：`from server.tasks import task_manager`（service 方法体内延迟触发）**：
```
>>> flask imported BY server.auth_helpers
>>> flask imported BY server.context.personalization
>>> flask imported BY server.context.upload
>>> flask imported BY server.context.conversation
>>> flask imported BY server.context.resources
>>> flask imported BY server.context.decorators
flask: True | flask_socketio: False | server.extensions: False | server.chat_flow: False
```
即：**server.tasks 的导入链仍会级联拉起 Flask 包本体**。具体断点：`server/tasks/models.py:16` 顶层 `from server.context import RuntimeIdentity, get_user_resources, ...` → `server/context/__init__.py` 顶层 re-export 会加载 identity/resources/personalization 等 → `server/context/identity.py:7` 顶层 `from server.auth_helpers import get_current_user_role` → `server/auth_helpers.py:5` 顶层 `from flask import session, redirect, jsonify`。`resources.py:15` 也直接 `from flask import session, has_request_context`。

且**在未安装 flask 的裸环境（系统 python3）中，`from server.tasks import task_manager` 直接 ModuleNotFoundError**——"server/tasks/__init__.py 注释声称『无 Web 依赖，可独立加载』"（__init__.py L5-8）与事实不符：不依赖 **Web app 初始化/Blueprint/SocketIO** ✅，但依赖 **flask 包本体** ❌。

**链路 C：`from server.chat_flow import run_chat_task_sync`（_run_chat_task 线程内延迟导入）**：
```
flask: True | flask_socketio: True | server.extensions: True | server.app: False | server.app_legacy: False
```
chat_flow.py:19 顶层 `from flask import ...` + L73 `from .extensions import socketio, run_background` → extensions.py:4 `from flask_socketio import SocketIO` + L7 实例化 `socketio = SocketIO(...)`。注意：**实例化 SocketIO 对象但不 init_app**（`socketio.server is None`），且不触发 `server.app` / `server.app_legacy`（无 Flask app 创建、无蓝图注册）——与"无 Flask app 上下文"验收一致。

**综合结论 ⚠️**：任务线程拆除 test_request_context、RuntimeIdentity 驱动、验收测试无 Flask app 全部属实；但**「Gateway 可脱离 Flask 初始化」的声明过头了**——真正的表述应为：
- ✅ Runtime 层本体（server.runtime）零 Flask 依赖，可 import；
- ⚠️ 一旦调用 create_task（延迟导入 server.tasks），**flask 包必然被拉起**（经 server.context.identity → auth_helpers），无 flask 环境直接 import 失败；
- ⚠️ 任务实际执行（_run_chat_task → 延迟导入 chat_flow）会拉起 **flask + flask_socketio + SocketIO 实例**（但不 init_app、不创建 app、不注册蓝图）；
- 即：**进程可无 Flask app 上下文运行，但无法在未安装 flask/flask_socketio 的纯环境运行**——装修层（Web 适配模块）侵入核心 import 链的实质未完全消除。

残留耦合点（建议未来拆解）：
1. `server/context/identity.py:7` 顶层 `from server.auth_helpers import get_current_user_role`——identity 是核心身份模型，却依赖 Web 认证适配层（即使显式身份路径不用它，_resolve_user_role 的 None 分支使用）；
2. `server/context/resources.py:15` 顶层 `from flask import session, has_request_context`——资源装配核心模块顶层依赖 flask；
3. `server/context/__init__.py` 一次 re-export 全量子模块，导致 identity/resources/personalization/upload/conversation/decorators 连带加载。

---

### 4. Runtime ↔ 执行层边界（modules/execution_plane/ + docs/execution_contract.md）

#### (a) ExecutionBackend 协议 E1-E4 方法签名 —— ✅ 属实

`modules/execution_plane/base.py`：
```
@runtime_checkable
class ExecutionBackend(Protocol):
    async def run_command(command, *, timeout, sandbox_write_access, network_permission) -> Dict    # E1
    def run_command_background(command, *, timeout, conversation_id, wait_seconds, network_permission, sandbox_write_access) -> Dict  # E2
    def write_file(path, content, *, mode="w") -> Dict                                                # E3
    def edit_file(path, replacements: List[Dict]) -> Dict                                              # E4
```
结果结构约定（docstring）：
- run_command: `{success, status, output, return_code, truncated, elapsed_ms}`，status ∈ completed/timeout/error/cancelled；
- run_command_background: `{success, command_id, status}`；
- write/edit: `{success, path, original_file, new_file}`。
`FakeExecutionBackend`（fake.py）实现全部 4 方法（内存文件系统 files dict + calls 记录 + background_commands dict），签名与协议逐一对应。

#### (b) MainTerminal.execution_backend 四注入分支真实存在且默认 None 走原路径 —— ✅ 属实

- 初始化：`core/main_terminal.py:149` `self.execution_backend = None`（注释：None = 现有 Host/Docker 真实链路；注入 ExecutionBackend 实现后 E1-E4 分支改走该后端）。
- 四分支（tools_execution.py `handle_tool_call`，L1057）：
  - **write_file**（L1635-1648）：`backend is not None` → `backend.write_file(path, content, mode)`（跳过浅备份）；else → `_track_shallow_versioning` + `self.file_manager.write_file`。
  - **edit_file**（L1669-1683）：backend → `backend.edit_file(path, replacements)`；else → `_track_shallow_versioning` + `self.file_manager.replace_many_in_file`。
  - **run_command 后台**（L1923-1932）：backend → `backend.run_command_background(command, timeout=..., conversation_id=..., wait_seconds=5.0, network_permission=..., sandbox_write_access=...)`；else → `bg_manager.create_background_command(...)`。
  - **run_command 前台**（L1958-1966）：backend → `await backend.run_command(command, timeout=..., sandbox_write_access=..., network_permission=...)`；else → `await self.terminal_ops.run_command(...)`。
- 四分支共用前置：权限裁决在进入分支前完成（L1891-1906：`permission_mode = self.get_permission_mode()` → `sandbox_write_access = not (readonly or (approval/auto_approval and not write_granted_once))` → `network_permission` 解析）；后端分支**不重复裁决**（协议"只承诺执行语义，不构成安全边界"）。
- 默认路径回归验证：check_fake_exec 断言新建对话级 terminal 的 `execution_backend is None` → 真实链路。

#### (c) 权限裁决/沙箱计划在哪一层 —— ✅ 属实（Runtime 编排层裁决，执行层不重复）

- `tools_execution.py:391` `evaluate_tool_permission(tool_name, arguments)`：permission mode 语义（readonly/approval/auto_approval/unrestricted）完整在此层（L394-470 多档分支）。
- 注入分支处显式传递**裁决结论**（`sandbox_write_access` / `network_permission` 已解析），ExecutionBackend 收到的即结论，不再二次裁决（base.py docstring L8-10、execution_contract.md §4）。
- 先读后写拦截（`_check_read_before_edit_prerequisite`）与浅版本备份也在编排层（write/edit 分支前置）。
- **契约注记（执行层安全边界分工）**：命令校验（_validate_command/FORBIDDEN_COMMANDS）与路径授权（_validate_path/禁读清单）仍由旧 terminal_ops/file_manager 链路承担（execution_contract.md §4 + base.py docstring——审核 §4 修正后的表述），OS 层强制力（Seatbelt/bwrap/DAC+Landlock）是最终边界。

#### (d) Host/Docker 迁入还差什么 —— ✅ 属实（E5-E10 后置为独立工作）

依据 `docs/execution_contract.md` §3 表：
- 已入协议（✅）：E1 run_command（terminal_ops/run.py）、E2 run_command_background（background_command_manager.py）、E3 write_file（file_manager/crud_mixin.py）、E4 edit_file（file_manager/replace_mixin.py）。
- 后续纳入（⬜）：E5 read_file 三模式、E6 create/delete/rename/mkdir 族、E7 持久终端会话、E8 path_validate、E9 执行环境快照、E10 命令校验/超时钳制。
- 剩余工作（文档 §6 + 记忆 gateway_runtime_work_plan 中用户拍板）：
  1. HostDockerBackend 适配器（真实后端实现 E1-E10 + 保留现有校验分配）；
  2. 编排层全面切换（现仅 E1-E4 四分支注入，其余工具仍直挂 terminal_ops/file_manager）；
  3. 结构债：执行命令后端选择逻辑在 terminal_ops/run.py、background_command_manager.py、persistent_terminal/start.py、container_file_proxy.py **4 处各有一份复制**（契约 §5），E5-E10 接口面完整化后是天然收敛点。
- 生产路径不变：默认 `execution_backend=None`，无替换行为。

---

### 5. 智能体循环层独立性（core/ 对 Flask 依赖残留）

#### —— ✅ 属实（Agent Runtime 层零 Flask 反向依赖）

- `grep -rnE "^from flask|^import flask|^from flask_socketio|socketio" core/` → **零命中**（两次独立 grep 均为空，返回码 1）。
- `core/main_terminal.py` 顶层 import（L3-72）：全为 modules/*、utils/*、config/model_profiles、core.main_terminal_parts、modules.i18n——**无 flask/socketio**。
- `core/main_terminal_parts/tools_execution.py`：顶层无 flask/socketio（grep 零命中）；`core/main_terminal_parts/` 其余文件同样零命中。
- 唯一注意点：`tools_execution.py:2195/2498/2514` 有 `_is_web = '/web/users/' in _data_dir` 的**路径字符串判定**（Web/CLI 数据目录布局差异），属身份/路径耦合（记忆里已归入遗留），非 Flask API 依赖。

结论：Agent Runtime（③ 层）不反向依赖 Web 层（②）✅。

---

## 二、Gateway → Runtime → Execution 实际分层图（按代码事实）

```
┌──────────────────────────────────────────────────────────────────────┐
│ ① Client（Web 前端 / CLI / 未来定时触发器）                            │
└───────────────┬──────────────────────────────────────────────────────┘
                │ HTTP / 轮询事件流（idx/offset 协议）
┌───────────────▼──────────────────────────────────────────────────────┐
│ ② Gateway（server/runtime/）                                         │
│    context.py: RuntimeContext = TrustedPrincipal + TaskParams         │
│                + InternalDirectives（门闸 token 走 internal，不接受   │
│                客户端提交；principal 由适配层构造【约定级约束】）       │
│    service.py: RuntimeService 无状态薄层（受理/控制/查询/审批/会话，   │
│                全部延迟委托 task_manager）                            │
│    ─── 边界清晰 ⚠️：本体零 Flask；但延迟导入 server.tasks 链           │
│        仍级联拉起 flask 包（context.identity → auth_helpers）          │
└───────────────┬──────────────────────────────────────────────────────┘
                │ create_task → create_chat_task → 线程 _run_chat_task
                │ RuntimeIdentity 驱动，全程无隐式上下文 / 无            │
                │ test_request_context / 无 session_data                │
┌───────────────▼──────────────────────────────────────────────────────┐
│ ③ Agent Runtime（server/chat_flow*.py + core/main_terminal.py）      │
│    执行链：run_chat_task_sync → process_message_task（门闸获取/       │
│           finally 释放）→ handle_task_with_sender（模型循环）          │
│    → core/main_terminal_parts/tools_execution.py::handle_tool_call    │
│    ─── core/ 零 Flask 依赖 ✅ 边界清晰                                │
│    ⚠️ 残留：chat_flow.py/chat_flow_task_main.py 顶层仍在            │
│         （延迟导入 chat_flow 时拉起 flask+extensions）                 │
└───────────────┬──────────────────────────────────────────────────────┘
                │ E1-E4 注入分支（execution_backend：None → 原路径；
                │ 注入 → 替身/未来远端后端）；权限裁决【编排层】完成，
                │ 传裁决结论（sandbox_write_access/network_permission）
┌───────────────▼──────────────────────────────────────────────────────┐
│ ④ Execution Plane（modules/execution_plane/）                        │
│    ExecutionBackend 协议 E1-E4（Protocol, runtime_checkable）         │
│    FakeExecutionBackend 替身（内存，验收通过）                        │
│    Host/Docker 真实后端（terminal_ops/file_manager 直挂）             │
│    ─── 边界清晰但不完整：仅 E1-E4 协议化；E5-E10 + 4 处后端选择逻辑   │
│        复制收敛 = 后置独立工作                                        │
└──────────────────────────────────────────────────────────────────────┘

边界评级：
[①→②]  清晰（公共协议 + 事件流 + 三层上下文；但 principal 自报 role 为约定级防线）
[②→③]  ⚠️ 半清晰（任务线程纯 RuntimeIdentity；但 flask 包经 server.context
          identity/resources 顶层 import 侵入核心链，无 flask 环境无法运行）
[③→④]  半清晰（core/ 零 Flask ✅；E1-E4 注入 ✅；但权限校验分两处——
         编排层裁决 + 旧链路命令校验/路径授权，Host/Docker 未迁入协议）
```

---

## 三、与文档声称不符的发现清单

| # | 严重度 | 文档声称 | 代码事实 | 证据 |
|---|---|---|---|---|
| F1 | 中 | tasks/__init__.py 注释「本包只导出任务核心层…保证 server.runtime 在无 Web 应用初始化的进程中可独立加载使用」 | 可脱离 **Web app 初始化** 加载 ✅，但不可脱离 **flask 包** 加载：`from server.tasks import task_manager` 级联拉起 flask（经 context.identity→auth_helpers），裸环境直接 ModuleNotFoundError | identity.py:7 `from server.auth_helpers import get_current_user_role`；resources.py:15 `from flask import session, has_request_context`；auth_helpers.py:5 `from flask import session, redirect, jsonify`；裸 python3 import 实验 FAIL |
| F2 | 中 | 任务线程「全程 RuntimeIdentity 驱动」隐含"模块级已解耦 Web" | 线程内确实 RuntimeIdentity 驱动（无 test_request_context）✅；但**执行阶段** `_run_chat_task` 延迟导入 `server.chat_flow` → 拉起 flask_socketio + `SocketIO()` 实例（即时不 init_app、`has_app_context()` 仍 False，socketio.server=None 静默跳过） | models.py:958-959；chat_flow.py:19,73；extensions.py:4-7；venv import 实验3 |
| F3 | 低 | TrustedPrincipal「只能由适配层构造」 | **约定 + 校验侧纵深防御，非结构强制**：dataclass 无构造限制、validate() 不校验 role 白名单；任何代码可直接构造 admin principal | context.py:20-41 validate L30-34；service.py `_resources_for_query` L269-283 仅约束查询侧 |
| F4 | 低 | 「拆除了 session_data 兼容桥」的表述 | 属实 ✅；仅 i18n key `tasks.missing_session_data` 命名保留旧词（错误文案已更新为「缺少任务运行上下文，请通过公共任务入口提交」），语义无残留 | models.py:148；i18n api_tasks.py:105 |
| F5 | 低 | execution_contract.md §4「权限裁决在 Runtime 编排层」 | 属实但要精确：**编排层做①permission mode 裁决②审批一次性授权③网络权限解析**，但**命令校验（FORBIDDEN_COMMANDS）与路径授权（禁读清单）仍在旧 terminal_ops/file_manager 链路**，经本接口注入的后端分支不会自动获得这些校验（契约已加注记，与代码一致） | base.py docstring L9-11；execution_contract.md §4 注记；tools_execution.py:1891-1906 |

未标注项：1(a)(c)(d)、2(a)(b)(c)、3(a)、4(a)(b)(c)(d)、5 全部与文档一致 ✅。

**未验证项**：无（全部声明均已通过代码定位 + import 实验 + 验收测试运行验证；唯一无法在本环境复现的是「Web 生产环境真实运行中 socketio 推送」——需要用户重启服务后人工验证，属记忆 gateway_runtime_work_plan 中的遗留待办 N3）。

---

## 四、总体判断

**目标「③④ 层达到 Runtime 与执行环境可通过替身独立测试、换执行后端不碰智能体循环」——达到了 70% 达成度（③↔④ 边界达标；②→③ 尚有一处实质耦合）。**

逐项对照：

1. **「Runtime 与执行环境可通过替身独立测试」✅ 完全达成**：`check_fake_exec`（0.7s PASSED）与 `check_approval_wait`（3.5s PASSED）证明真实工具编排层 handle_tool_call + FakeExecutionBackend 可以零真实副作用跑通 E1-E4，且默认路径（backend=None）行为回归断言存在。

2. **「换执行后端不碰智能体循环」✅ 达成（限 E1-E4）**：注入点是 `MainTerminal.execution_backend` 属性 + handle_tool_call 四分支，智能体循环（模型循环/工具分派/事件）不感知后端具体实现；替换 Host/Docker 后端只需实现协议并注入。❌ **但完整达成需 E5-E10 全部协议化**——当前 read_file、mkdir、终端会话、路径校验等仍直挂 terminal_ops/file_manager，Host/Docker 真实后端「换后端不碰循环」仅对 E1-E4 成立。

3. **③④ 层的方向独立性已建立，但存在一个实质耦合点**：核心链 `server.tasks` → `server.context.identity` → `server.auth_helpers` → `flask` 使 **Gateway 进程仍必须安装 flask 包**（虽然不需要 Flask app / SocketIO 服务 / 蓝图）。独立启动验收之所以能过，是因为验收环境装了 flask+flask_socketio —— 若在未安装 Flask 的精简部署环境运行，`create_task` 将直接 ModuleNotFoundError。

4. **对「Gateway 独立启动（无 Web 初始化）」声明**：验收测试已证明无 app 上下文、无 SocketIO 服务、无蓝图注册、门闸/终态/事件流/审批全生命周期真实通过 ✅；但「零 Flask 依赖」不成立 ⚠️——准确表述应为「无 Flask **应用初始化** 依赖，仍有 Flask **包** 依赖」。

**建议（按优先级）**：
1. 拆 `server/context/identity.py:7` 顶层 `from server.auth_helpers import get_current_user_role`（改为延迟导入或在 context 包内实现角色默认逻辑），`resources.py:15` 的 flask session 依赖同理下沉到适配层——可一次性切断 ②→③ 链的 flask 包依赖；
2. server/context/__init__.py 目前一次 re-export 全量子模块（连带加载），可考虑按需分包或延迟绑定；
3. Host/Docker 迁入（E5-E10 + HostDockerBackend + 编排层切换 + 4 处后端逻辑收敛）按既定后置计划执行，与本轮结论无冲突。

---

### 附录：import 实验记录（本机 venv=python3.9.6 / 裸 python3=3.9.6）

| 实验 | 命令 | 结果 |
|---|---|---|
| 1 | `import server.runtime.service`（裸 python3） | OK；flask=False、flask_socketio=False、server.tasks=False |
| 2 | `from server.tasks import task_manager`（裸 python3） | **FAIL: ModuleNotFoundError 'flask'**（追踪：server.tasks→models→server.context→identity→auth_helpers→flask） |
| 3 | `from server.tasks import task_manager`（venv） | OK；**flask=True**、flask_socketio=False、extensions=False、chat_flow=False |
| 4 | `from server.chat_flow import run_chat_task_sync`（venv） | OK；**flask=True、flask_socketio=True、server.extensions=True**、server.app=False、app_legacy=False |

### 附录：验收测试实测记录

| 检查 | 用时 | 结果 | 关键证据 |
|---|---|---|---|
| check_lifecycle | 1.6s | PASSED | 无 app 上下文 → create_task → api_request_start 装配证据 → 取消 → 终态 → terminal 存在 → 门闸释放 → socketio.server is None |
| check_fake_exec | 0.7s | PASSED | E1-E4 四分支替身全部收到；零真实磁盘写入；backend=None 回归 |
| check_chain | 2.7s | PASSED | list_runs 发现 → 事件观察 → 跨端取消 → 历史落盘 → offset 续读 → 会话查询 → 审批语义 |
| check_approval_wait | 3.5s | PASSED | 执行中审批等待 → 公共入口批准 → 替身收到命令 → 循环退出 |
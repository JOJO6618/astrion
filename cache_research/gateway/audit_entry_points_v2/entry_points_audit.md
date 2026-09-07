# 任务入口收敛现状核查报告（v2）

- 审计范围：Astrion「Gateway 化改造」中「全部 6 处 create_chat_task 调用点已迁移到
  runtime_service.create_task()」这一声明的真伪核查。
- 审计方式：全程只读，未修改任何项目文件。
- 关键词库：`create_chat_task` / `runtime_service` / `test_request_context` / `session[`
- 说明：grep 结果中所有 `__pycache__/*.pyc` 二进制命中均已排除（为旧字节码缓存，不代表源码）。
- 审计时间：2026-09-07

---

## 0. 背景：RuntimeService / RuntimeContext 结构（evidence）

文件：`server/runtime/service.py`、`server/runtime/context.py`、`server/runtime/__init__.py`

- `server/runtime/service.py:114`：`runtime_service = RuntimeService()`（进程级单例）。
- `server/runtime/service.py:38`：`create_task()` 内部唯一真实调用点
  `return task_manager.create_chat_task(...)`（约 15 个显式参数，含
  `session_data=ctx.to_session_data()`）。即：**全项目唯一直接 create_chat_task 调用在 service.py**。
- `server/runtime/context.py：RuntimeContext` 由三层分离组成：
  - `TrustedPrincipal`（username / workspace_id / role / is_api_user / host_mode /
    preferred_model_key / preferred_run_mode / preferred_thinking_mode……）
  - `TaskParams`（message / images / videos / files / conversation_id / model_key /
    run_mode / thinking_mode / max_iterations / goal_mode / skill_context_messages /
    message_source / task_type / approval_timeout_seconds……）
  - `InternalDirectives`（main_task_gate_token / auto_user_message_event /
    auto_user_message_payload / preceding_user_notices）
- `RuntimeContext.from_terminal()`（`context.py:76`）：内部调用方专用入口，把对话级
  terminal/工作区逐字段映射为 principal + params + directives（含 host_mode、role、
  is_api_user、偏好快照）。
- `RuntimeContext.to_session_data()`（`context.py:133`）：兼容转换——把三层合并为
  session_data 快照 dict，承载身份/偏好/门闸 token/事件回放/terminal 属性设置。
- `principal_from_session_snapshot()`（`context.py:165`）：适配层工具，认证后从 Flask
  session dict 快照构造可信 principal（context.py 不 import flask）。
- `server/runtime/__init__.py:__all__` 导出：`InternalDirectives`、`RuntimeContext`、
  `RuntimeService`、`TaskParams`、`TrustedPrincipal`、`principal_from_session_snapshot`、
  `runtime_service`。（全 7 项）

---

## 1. 六处声称迁移点逐一核查结论表

| # | 声称位置 | 实际调用行号 | 实际调用方式 | 上下文构造方式 | 特殊语义保留 | 与声明是否一致 |
|---|---------|------------|------------|--------------|------------|--------------|
| 1 | `server/tasks/api.py`（Web 聊天 POST /api/tasks） | `server/tasks/api.py:220` | `runtime_service.create_task(ctx)` ✅ | `RuntimeContext(principal=principal_from_session_snapshot(session, workspace_id, username=username), params=TaskParams(...))`（api.py:211-219，无 directives） | Web 客户端不应提交 gate token，故无 InternalDirectives（符合低权限语义词）；session 快照承载 username/workspace_id/role 等 | ✅ 一致 |
| 2 | `server/api_v1.py`（API v1 Bearer token 路径） | `server/api_v1.py:335` | `runtime_service.create_task(ctx)` ✅ | `RuntimeContext(principal=principal_from_session_snapshot(session, ws.workspace_id, username=username), params=TaskParams(...))`（api_v1.py:330-338） | **is_api_user/role 传递保留**：`server/api_auth.py:43` 设 `session["is_api_user"]=True` → snapshot → to_session_data 写入 session_data；api_v1.py:321 注释明确「is_api_user=True / role="api"，丢失会静默串资源管理器」 | ✅ 一致 |
| 3a | `server/workflow_runtime_api.py`（workflow 激活） | `server/workflow_runtime_api.py:192` | `runtime_service.create_task(ctx)` ✅ | `RuntimeContext.from_terminal(terminal, workspace, username, params=TaskParams(...message_source="workflow"), directives=InternalDirectives(main_task_gate_token=..., auto_user_message_event=True, auto_user_message_payload=...))`（workflow_runtime_api.py:171-191） | **门闸 token 移交**（main_task_gate_token）+ **auto_user_message 事件回放** 均走 InternalDirectives | ✅ 一致 |
| 3b | `server/workflow_runtime_api.py`（workflow 停用/通知派发） | `server/workflow_runtime_api.py:269` | `runtime_service.create_task(ctx)` ✅ | `RuntimeContext.from_terminal(...)` + `InternalDirectives(main_task_gate_token, auto_user_message_event, auto_user_message_payload)`（workflow_runtime_api.py:253-268） | 门闸 token 移交 + auto_user_message 事件回放 | ✅ 一致 |
| 4a | `server/chat_flow_task_main.py`（完成通知派发 dispatch_completion） | `server/chat_flow_task_main.py:618` | `runtime_service.create_task(ctx)` ✅ | `RuntimeContext.from_terminal(web_terminal, workspace, username, params=..., directives=InternalDirectives(main_task_gate_token, auto_user_message_event=True, auto_user_message_payload, preceding_user_notices))`（chat_flow_task_main.py:597-616） | 门闸 token、auto_user_message、preceding_user_notices 回放；request source/terminal 属性传递 | ✅ 一致 |
| 4b | `server/chat_flow_task_main.py`（多智能体 idle 派发 dispatch_ma_idle） | `server/chat_flow_task_main.py:1417` | `runtime_service.create_task(ctx)` ✅ | `RuntimeContext.from_terminal(...)`，params 含 `task_type="notice"`，directives 含 auto_user_message_event/auto_user_message_payload/preceding_user_notices（chat_flow_task_main.py:1395-1416） | **notice task_type 豁免互斥**（models.py 单对话互斥对 notice 豁免）；多智能体事件回放 | ✅ 一致 |

> 结论：**全部 6 处调用点均已迁到 `runtime_service.create_task(ctx)`**，与声明一致。
> 无任何一处仍直接调用 `task_manager.create_chat_task`。

**各点快速证据（调用宏）**：`grep -n "runtime_service.create_task"`：
- `server/tasks/api.py:220`
- `server/api_v1.py:335`
- `server/workflow_runtime_api.py:192`、`:269`
- `server/chat_flow_task_main.py:618`、`:1417`

---

## 2. 漏网扫描结果

### 2.1 所有 `create_chat_task` 出现位置（排除 pycache）
- `server/tasks/models.py:128` —— **定义处**（`def create_chat_task(self, ...)`）。
- `server/runtime/service.py:38` —— **唯一直调处**（RuntimeService.create_task 内部委托）。
- 其余命中全部为**注释/文档字符串**：`chat_flow_task_main.py:613`（ma_debug 标签字符串
  "dispatch_completion_create_chat_task"）、`chat_flow_task_main.py:2636`（注释）、
  `server/main_task_gate.py:4`（注释）、`server/runtime/service.py:9/27`（docstring）、
  `server/runtime/context.py:133`（docstring）。

**→ 无任何“定义处 + service.py 委托处”之外的实体 create_chat_task 调用点。✅**

### 2.2 所有 `runtime_service` 出现位置
- `server/chat_flow_task_main.py:46`（import）、`:618`、`:1417`（调用）
- `server/tasks/api.py:27`（import）、`:220`（调用）
- `server/workflow_runtime_api.py:17`（import）、`:192`、`:269`（调用）
- `server/api_v1.py:13`（import）、`:335`（调用）
- `server/runtime/service.py:114`（定义单例）
- `server/runtime/__init__.py:9/:18`（导出）

**→ modules/、core/、utils/ 目录下均无 runtime_service 出现；所有调用点与 6 处迁移点完全重合。✅**

### 2.3 session_data 强制校验（models.py）
- `server/tasks/models.py:174`：`if session_data is None:` → `:175` `raise ValueError(tr("tasks.missing_session_data"))`
- 位置位于 `create_chat_task` 内、登记任务前；注释（models.py:172-173）明确
  「必须显式传入：禁止在受理层回退读 Flask session」。**确认强制显式 session_data，缺失抛 ValueError。✅**
- 测试覆盖：`test/test_runtime_service.py:161` `test_create_chat_task_requires_explicit_session_data`
  （:164 直调 `task_manager.create_chat_task("tester","default","msg",[],"conv_x")` 断言抛 ValueError）。

---

## 3. Flask 依赖核查

### 3.1 `test_request_context` 在 server/ core/ modules/ utils/ 下
grep 命中（排除 pycache）：
```
server/tasks/models.py:768:  # 直接驱动资源装配——不再伪造 Flask 请求上下文（原 test_request_context
server/runtime/service.py:10: 驱动资源装配（test_request_context 桥已拆除），session_data 快照仍承载
```
两处均为**注释**，无实体代码调用。→ **server/ core/ modules/ utils/ 下 test_request_context
实际调用 = 0。声明「已拆桥」在服务端源码层面属实。✅**

> ⚠️ 附注（不推翻结论，但需明示）：在 **test/** 目录下仍有 `test_request_context` 的实际代码使用：
> `test/test_runtime_identity_resources.py:170`：`ctx = app.test_request_context("/")`
> （该测试用于 host 工作区策略分支的 `get_user_resources` 测试，属于测试辅助构造 Flask 上下文，
> 不是任务线程桥；声明范围仅 server/core/modules/utils，故不违反声明。标注为「附带发现」。）

另：`test/test_runtime_service.py:4` docstring 提及 "无 test_request_context"，为说明性文字非调用。

### 3.2 `session[` 在 `server/tasks/models.py`
```
grep -n "session\[" server/tasks/models.py  → 0 命中（exit=1）
```
**→ 任务线程（models.py）不再读 Flask session。✅** 与声明一致。

---

## 4. 相关测试文件清单

- `test/test_runtime_service.py` —— 覆盖 RuntimeService / RuntimeContext，用例：
  - `RuntimeContextModelTest.test_validate_rejects_empty_username`（:53）
  - `RuntimeContextModelTest.test_to_session_data_carries_identity_and_preferences`（:59）
  - `RuntimeContextModelTest.test_to_session_data_directives`（:78）
  - `RuntimeContextModelTest.test_principal_from_session_snapshot`（:95）
  - `RuntimeServiceAdmissionTest.test_t01_create_task_without_http_context`（:132）
  - `RuntimeServiceAdmissionTest.test_t02_same_conversation_chat_mutex_and_notice_exempt`（:141，含 notice 豁免）
  - `RuntimeServiceAdmissionTest.test_create_task_validates_context`（:155）
  - `RuntimeServiceAdmissionTest.test_create_chat_task_requires_explicit_session_data`（:161）
  - `RuntimeServiceAdmissionTest.test_t04_cancel_task`（:168）
  - `RuntimeServiceAdmissionTest.test_get_task_events_offset_protocol`（:176）
- `test/test_runtime_identity_resources.py`（:170 使用 test_request_context）—— 见 §3.1 附注。
  其核心范围为 RuntimeIdentity + 资源装配（get_user_resources），非直接覆盖 runtime.service。

---

## 5. 总结

**入口收敛声明是否属实：属实。✅**

1. **6 处迁移**：全部 6 处调用点均已调用 `runtime_service.create_task(ctx)`，无一仍直调
   `task_manager.create_chat_task`；唯一真实直调位于 `server/runtime/service.py:38`（create_task 内部委托）。
2. **无漏网调用点**：`create_chat_task` 实体调用仅 2 处（定义 models.py:128 + service.py:38 委托），
   其余为注释/docstring；无任何第三处实体调用。
3. **上下文显式化**：所有调用点均走 RuntimeContext（三元组）构造，不再手工 session_data dict；
   特殊语义（notice 豁免 task_type="notice"、门闸 token 移交 main_task_gate_token、
   is_api_user/role 传递、auto_user_message/前置信事件回放）均在 context.py.to_session_data /
   from_terminal 中保留。
4. **session_data 强制**：models.py:174-175 强制显式 session_data，缺失抛 ValueError。
5. **Flask 拆桥**：server/core/modules/utils 下 test_request_context 实体调用 = 0（仅注释）；
   models.py 无 `session[` 读取。附带发现 test/ 下 test_runtime_identity_resources.py:170 有
   测试辅助用 test_request_context（非任务线程桥，不违反声明，特此标注）。

**未确认项**：无（全部结论均有 文件:行号 证据支撑）。

### 证据索引
- create_chat_task 定义：`server/tasks/models.py:128`
- create_chat_task 唯一委托直调：`server/runtime/service.py:38`
- session_data 强制校验：`server/tasks/models.py:174-175`
- 六处迁移调用：`tasks/api.py:220` / `api_v1.py:335` / `workflow_runtime_api.py:192,269` / `chat_flow_task_main.py:618,1417`
- is_api_user 注入：`server/api_auth.py:43`

# 阶段一/二实施计划记录

> 日期：2026-09-07
> 上游文档：`gateway_work_plan.md`（三阶段路线）、`eval_summary.md`（范围与复杂度评估，含 R1-R6 审阅修订）
> 范围：**阶段一（契约文档）+ 阶段二（公共任务入口）**；阶段三定时任务未讨论功能与实现，明确不做。

## 目标

按 work plan 阶段一/二的完成标准交付：

- 阶段一：`docs/runtime_contract.md` 落地——状态责任表、概念对齐、公共入口上下文契约、调用方迁移表、回归用例清单。目标入口的状态修改路径和执行裁决可定位；已有保障成为明确约束。
- 阶段二：不创建浏览器会话、不伪造 HTTP 请求，能通过显式身份和资源上下文启动一次受控任务、读取结果并取消；同对话重入仍受门闸保护；现有 Web/CLI 行为兼容。

## 阶段二设计决策（按 R4 审阅意见）

### RuntimeContext 三层分离

```
TrustedPrincipal   可信身份与资源范围：username / workspace_id / host_mode /
                   host_workspace_id / is_api_user / role
                   ——只能由适配层认证后构造，客户端不可自报
TaskParams         本次任务参数：message / images / videos / files / model_key /
                   run_mode / thinking_mode / max_iterations / conversation_id /
                   goal_mode / skill_context_messages / message_source
InternalDirectives 内部执行信息：main_task_gate_token / auto_user_message_event /
                   auto_user_message_payload / preceding_user_notices /
                   approval_timeout_seconds（仅透传机制，默认语义不变）
                   ——普通客户端不可提交，仅内部调用方（通知链/工作流/派发器）使用
```

默认值解析优先级：**本次显式传参 > 对话元数据绑定 > 会话/用户偏好快照 > 系统默认**。
会话配置恢复（模型/模式）仍在 `get_user_resources` + 会话加载链路内完成，RuntimeContext 携带的是「本次覆盖」与「身份快照」，不替代既有恢复逻辑。

### RuntimeService 最小接口集

- `create_task(principal, params, directives) -> task_id`（受理：互斥裁决 + 登记 + 起执行）
- `cancel_task(username, task_id)`
- `enqueue_runtime_guidance / enqueue_runtime_pending_message / remove_runtime_pending_message / promote_runtime_pending_to_guidance`
- `get_task / get_task_events(username, task_id, offset)`（内部查询接口，CLI/定时任务不必走 HTTP）
- 审批回答复用现有三个 manager，通过明确关联接入，不进 RuntimeService 首版。

### 两步走

- **步骤①（兼容期）**：RuntimeContext + RuntimeService 骨架落地，`create_chat_task` 接受显式上下文；6 处调用点迁移；`test_request_context` 桥**保留**作为兜底（显式快照灌回 session 的既有行为不变）。
- **步骤②（拆桥）**：`get_user_resources` 增加显式参数变体（principal + 快照参数），`_apply_workspace_personalization_preferences` 参数化，`auth_helpers` 提供显式 record/role 入口；`_run_chat_task` 改走显式路径后删除 test_request_context 桥；`ensure_conversation_loaded` 的 session 回写移出任务路径。

## 关键风险与对策

1. `get_user_resources` 参数化（最高风险）：host_mode/is_api_user 分支选错 → 静默串工作区。对策：显式变体与现有 web 路径并存，先任务线程单点切换，回归验证后再推广。
2. 门闸 token 移交语义：InternalDirectives 原样承载；通知链「预占→移交→认领→失败回滚」不变。
3. `task_type="notice"` 互斥豁免保留。
4. 异常回退路径（chat_flow_task_main.py:645-675 直接执行 handle_task_with_sender）：保留语义，验收覆盖其门闸/事件/取消生命周期。
5. 事件前台干扰：阶段二不加 source 字段（阶段三产品决策），现有行为不变。

## 验收

- `python3 -m py_compile` 触及文件全过
- `python -m pytest test/test_server_refactor_smoke.py -q` 通过
- 新增测试：显式上下文（无 HTTP 请求）受理任务 → 拒绝同对话并发 chat 任务 → 取消；不触达真实模型调用
- 服务重启与人工验证交用户执行（不擅自重启 8091/8092）

## 实施结果（2026-09-07 完成，待 commit）

**状态：阶段一/二代码工作全部完成，改造相关测试 24/24 全绿；工作区改动未提交。**

### 阶段一交付

- `docs/runtime_contract.md`：概念对齐（Session/Run/Schedule/Occurrence/Event）、10 项状态责任表、已有保障约束清单、RuntimeService 契约（含 RuntimeContext 三层模型定义）、调用方迁移表、12 项回归用例 T01-T12。

### 阶段二交付

- 新建 `server/runtime/` 包：
  - `context.py`：RuntimeContext 三层模型（TrustedPrincipal / TaskParams / InternalDirectives），`from_terminal()`、`principal_from_session_snapshot()`、`to_session_data()`。
  - `service.py`：`RuntimeService`（create_task / cancel / guidance / queue / get_task_events）+ 进程级单例 `runtime_service`。
- `TaskManager.create_chat_task` 强制显式 session_data（缺失即 `ValueError`，i18n key `tasks.missing_session_data`）。
- 6 处调用点全部迁移到 `runtime_service.create_task()`：`server/tasks/api.py`、`server/api_v1.py`、`server/workflow_runtime_api.py`×2、`server/chat_flow_task_main.py`×2（完成通知派发 :613、多智能体 idle 派发 :1416）。门闸 token 移交、`task_type="notice"` 互斥豁免语义原样保留。
- `server/context.py`（989 行）拆分为 `server/context/` 子包：identity / broadcast / personalization / usage / upload / conversation / resources / decorators / reaper 共 9 模块 + `__init__.py` 兼容 re-export（外部 import 路径不变）。
- `get_user_resources` 参数化：新增 `RuntimeIdentity` 显式身份快照（定义在 `server/context/identity.py`，runtime 包引用之，依赖方向自下而上无循环）。
- **test_request_context 桥已拆除**：`server/tasks/models.py::_run_chat_task` 不再建立 Flask 请求上下文，任务线程全程 RuntimeIdentity 驱动。
- 审批超时透传管道：terminal 属性 `_approval_timeout_seconds`（默认语义 3600s 不变），`_run_chat_task` setattr → `server/chat_flow_tool_loop.py::_approval_timeout_for()` helper → 4 个 `_wait_*` 调用点。
- 附带修复：`config/_load_dotenv` 对禁读 `.env` 的沙箱环境加 try/except（不再 PermissionError 崩溃）。

### 测试验收

- 改造相关 24/24 全绿：`test_server_refactor_smoke`（6）+ `test_runtime_service`（10，新增）+ `test_conversation_model_persistence`（4，patch 目标随迁）+ `test_runtime_identity_resources`（4，新增，覆盖 get_user_resources 的 web/host/api 身份路由）。
- 存量失败 4 项（conversation_workspace_storage / host_workspace_manager / skills_manager / token_usage_extractor）经甄别与本次改动零相关，未修。

### 遗留待办

1. 真实运行环境验证（Web 聊天 / 停止 / 审批 / workflow 激活 / 多智能体派发）需用户重启服务后人工完成——get_user_resources 分支选错会静默串工作区，这是最高风险点。
2. 审批条目 task_id 恒 None（静态疑点，待运行时验证）。
3. socket 软 stop 不打断审批等待（REST 硬取消可以），留待阶段三或独立决策。

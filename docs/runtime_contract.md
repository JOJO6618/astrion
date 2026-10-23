# Astrion 运行时内部服务契约（Runtime Contract）

> 版本：v1 草案（2026-09-07）
> 定位：描述 Astrion 进程内运行时服务的**内部契约**——状态归谁所有、谁可以改、新任务入口应该调用哪里。
> 本文不定义公开网络协议、握手流程，也不强制实体全面改名。
> 依据：`_experiments/cache_research/gateway/gateway_work_plan.md`（三阶段路线）与 `_experiments/cache_research/gateway/eval_summary.md`（范围评估，含 R1-R6 审阅修订）。
> 若与代码冲突，以代码为准并修订本文档。

## 1. 概念对齐

| 概念 | 对应现有实现 | 说明 |
|---|---|---|
| **Session（会话）** | conversation（`conversation_id`） | 一份对话历史 + 元数据，持久化为 JSON 文件 |
| **Run（运行）** | 一轮主任务（`TaskRecord`，`task_id`） | 从受理到终态的一轮 Agent 执行；`task_type ∈ {chat, notice, compression}` |
| **Schedule（计划）** | 阶段三新增 | 用户定时任务的计划实体（本文仅预留概念，不定义实现） |
| **Occurrence（触发实例）** | 阶段三新增 | Schedule 的一次到期触发，稳定标识 = schedule_id + 计划时间点 |
| **Event（事件）** | `TaskRecord.events` 条目（`idx`/`type`/`data`/`ts`） | 任务执行的变化通知，task 级单调 idx，客户端按 offset 续读 |

子智能体、后台命令与主任务的关系：

- **子智能体 / 后台命令**是主任务执行过程中派生的**后台工作者**，有独立的任务记录与控制入口（`/api/sub_agents/*`、`/api/background_commands/*`），不是主 Run 本身。
- **完成通知派发**（`task_type="notice"`）与手动压缩（`task_type="compression"`）同属主 Run，与 chat 一样遵守对话级受理互斥和单写者门闸；自动压缩属于原 Run 的活动阶段。
- 停止主任务**不**自动停止后台工作者（独立控制入口）；后台工作者完成后经通知链汇报。

## 2. 状态责任表

> 每项列明：权威来源、允许的修改方、持久化入口、缓存刷新、并发裁决、失效条件。
> 内存/文件/客户端缓存可共存，权威关系必须唯一。

| # | 状态 | 权威来源 | 允许的修改方 | 持久化入口 | 缓存刷新 | 并发裁决 | 失效条件 |
|---|---|---|---|---|---|---|---|
| S1 | 对话历史与元数据 | 磁盘 conversation JSON | 执行链统一走 `ContextManager.add_conversation`（`utils/context_manager/message_mixin.py:127`）；少量外部写者（设置/压缩/CLI 命令）复用同一保存保护 | `save_conversation`（`crud_mixin.py:298`：merge-on-save 按 message_id 合并防缩减 + `_io_lock` RLock + `_atomic_write_json` 原子替换） | 内存 ContextManager 随写即存 | 对话级主任务门闸（S4）+ `create_chat_task` 单对话互斥（S2） | 删除对话；检查点恢复是唯一 allow_shrink 豁免 |
| S2 | 任务记录（TaskRecord） | 内存 `task_manager._tasks` | 仅 `TaskManager`（`server/tasks/models.py`） | 任务内存态；未消费输入/引导由 `queue_state.py` 存入对话 metadata | 对话级持久队列在受理和 bootstrap 时恢复 | `_lock` + 所有主 Run 按对话互斥，含 cancel_requested | 任务清理/重启不删除持久队列；停止/失败暂停自动消费 |
| S3 | 任务事件流 | `TaskRecord.events`（有界 deque maxlen=20000） | 执行链经 `_append_event` | 无（内存态） | 客户端按 task_id+offset 轮询；显示恢复由统一 bootstrap 快照及绝对游标接续 | VIEW_LOCK → `_lock`，事务内发布显示与事件 | 事件裁剪／任务清理／重启；不靠事件重放重建全部历史 |
| S4 | 对话级主任务门闸 | terminal 对象属性 `_main_task_gate_token` | 聊天 `process_message_task`、手动压缩 `run_compression_task` 获取/认领相同门闸；通知链预占移交 | 无 | 无 | token 匹配才能释放（`main_task_gate.py`） | 任务结束/异常兜底释放 |
| S5 | 停止标志（stop_flags） | `state.stop_flags` dict，任务级键 = task_id（REST 任务即 client_sid） | RuntimeService.cancel_task → TaskManager.cancel_task，硬取消与软检查配合 | 无；目标暂停及未消费输入另行持久化 | 执行链检查点轮询（流式/工具/重试/行首） | 任务级身份隔离 | 任务收尾 pop |
| S6 | 审批/提问条目 | 三个内存 manager（tool_approval / plan_approval / user_question），键 = approval_id/question_id；list 按 username+conversation_id | 执行链 create_request；REST answer/decide 置终态（锁内单次裁决） | 无（内存态，无 TTL） | 执行侧 asyncio.sleep 轮询（0.2~0.3s，默认超时 3600s） | manager 内 threading.Lock，已决定返回现状 | 重启即失；**超时/取消不回写终态（已知缺口）** |
| S7 | 对话级 terminal 实例 | `state.user_terminals`，key = `username::workspace_id::conversation_id` | `get_user_resources` 创建/重建；回收器 24h 无活动关闭 | 无 | 容器句柄按工作区级共享 | 回收器 pop 前校验实例身份 | 24h 无活动回收；进程重启 |
| S8 | 权限模式 / 执行环境 / 网络权限 | 对话 metadata + terminal 当前值 | `server/chat/permission.py` 端点：空闲立即生效，**运行中修改入队由工具循环消费**（queue_permission_mode_change 等） | 对话 metadata 持久化 | 工具循环在检查点应用 pending 变更 | 运行中排队保证单写者 | 对话删除 |
| S9 | 多智能体实例状态 | `GLOBAL_MULTI_AGENT_STATES` 进程级单例 + 磁盘快照 | `SubAgentManager` / dispatch 链路 | 快照持久化（`mutiagents/`） | `_load_state` 恢复 + 任务记录校准终态 | `GLOBAL_MULTI_AGENT_STATES_LOCK` | terminate / 对话删除 |
| S10 | 用户偏好（模型/模式/个性化） | settings.json / personalization.json（运行态路径） | 设置类端点；执行链只读快照 | 配置文件原子写 | RuntimeContext.principal 偏好快照在受理时固化 | 文件锁 | 用户修改 |
| S11 | 对话级目标生命周期 | goal_states/<conversation_id>.json | 目标任务受理、审核／终态处理、显式控制端点 | GoalStateManager 原子替换 | bootstrap / running-status 读文件；goal_progress 事件投影 | 路径共享 RLock、run_id / task_id / revision；显示事务锁先于目标锁 | 关闭停止管理；重启活动态暂停；继续必须显式受理 |

**约束**：
- 新增状态必须先在表中登记权威关系，再实现。
- 「写入口分散」是已知现状（S1 的外部写者）；新增写入口必须复用同一保存保护，禁止旁路直写 conversation 文件。
- S2/S3/S6 为内存态：不能承诺重启续跑或无限期回放；也不能说任务一结束事件立即丢失（清理有 3600s 窗口）。

## 3. 已有正确性保障（不可回退的约束）

1. **单写者不变量**：一个对话同一时刻只允许一个主 Run。聊天和手动压缩获取/认领同一门闸并在 finally 释放；通知链保留预占、移交、失败回滚。
2. **受理互斥**：`create_chat_task` 对 chat/notice/compression 统一做同对话 pending/running/cancel_requested 排重。受理登记在同一锁内裁决；受理互斥与执行门闸是两层保护。
3. **保存保护**：merge-on-save（无 message_id 防御性跳过）+ manager 级 I/O 锁 + 原子替换 + 缩减拒绝。跨 manager 实例写同一文件不互斥——**前提是所有执行写都走对话级 terminal 专属 ContextManager**。
4. **客户端恢复**：task 级 idx + offset 轮询 + running-status 对账 + 按 task_id/idx 去重。过期响应过滤必须保留。
5. **审批单次裁决**：锁内裁决 pending，已决定时返回现状。
6. **异常回退路径**：普通完成通知派发在 `create_chat_task` 失败时仍保留直接执行 `handle_task_with_sender` 的旧回退（不生成 TaskRecord）；它必须保持门闸释放。活动或可继续目标禁止这条回退，受理失败向通知池上报并保留待派发项，避免绕过持久暂停、任务取消与错误收尾。
7. **目标管理独立于主任务终态**：关闭目标不取消 Run；停止 Run 持久暂停目标。网络失败的 Run 以 failed 结束，再由目标调度器在原 conversation_id 创建新的 notice Run，不修改原 Run 成功状态。重启不自动恢复目标执行。完整约束见 `docs/agents_details/goal_mode.md`。

## 4. 公共任务入口契约（RuntimeService）

### 4.1 运行上下文 RuntimeContext（三层分离）

```
┌─ TrustedPrincipal（可信身份与资源范围）─────────────────────┐
│  username / workspace_id / host_mode / host_workspace_id /  │
│  is_api_user / role                                         │
│  构造规则：只能由适配层在完成认证后构造；                     │
│  禁止接受客户端或 Schedule payload 自报的 role/is_api_user    │
├─ TaskParams（本次任务参数）─────────────────────────────────┤
│  message / images / videos / files / conversation_id /      │
│  model_key / run_mode / thinking_mode / max_iterations /    │
│  goal_action / goal_options / skill_context_messages /     │
│  message_source / approval_timeout_seconds                  │
├─ InternalDirectives（内部执行信息）─────────────────────────┤
│  main_task_gate_token / auto_user_message_event /           │
│  auto_user_message_payload / preceding_user_notices         │
│  构造规则：仅内部调用方（通知链/工作流/派发器）使用，         │
│  普通客户端提交一律拒绝或忽略                                 │
└─────────────────────────────────────────────────────────────┘
```

**默认值解析优先级**：本次显式传参 > 对话元数据绑定 > 用户偏好快照 > 系统默认。
会话级模型/模式恢复仍在资源装配与会话加载链路内完成；RuntimeContext 携带「身份快照 + 本次覆盖」，不替代既有恢复逻辑。

### 4.2 服务接口（首版最小集）

| 方法 | 语义 | 裁决/互斥 |
|---|---|---|
| `create_task(principal, params, directives=None) -> task_id` | 受理一轮 Run：互斥裁决 + 登记 + 起执行 | 同对话 chat 互斥（409）；notice 豁免；门闸在执行内获取或凭 token 认领 |
| `cancel_task(username, task_id)` | 停止主 Run（不触碰后台工作者） | 硬取消（loop.call_soon_threadsafe）+ 软标志 |
| `enqueue_runtime_guidance` | 运行中追加引导；可携带 images/videos/files，主输入栏以 media_paths_only 复用隐藏路径通知 | 按 task_id 寻址并核对 conversation_id，停止/关闭受理后拒绝 |
| `get_task(username, task_id)` / `get_task_events(username, task_id, offset)` | 内部观察接口；CLI/定时任务等后台调用方直接调用，**不必为观察任务发 HTTP 请求** | 只读 |

- 2026-10-23 移除提前输入排队、队列转引导及删除接口；未消费引导/旧提前输入只作为对话草稿恢复来源，原子保存草稿后确认移除，不自动续发。引导在整批工具结果回填后或无工具回复边界注入，仍由同一主任务写历史。
- 审批回答（tool/plan/ask_user）复用现有三个 manager 的 pending/answer，通过 approval_id 关联接入，不进 RuntimeService 首版。
- 内部错误使用稳定状态/错误码（`task_already_running` / `not_found` / `forbidden` / `invalid_params`…），HTTP 状态码由适配层映射。
- 事件协议沿用现有 idx/offset + sender 注入（task_id/conversation_id/workspace_id），本阶段不重写客户端。

### 4.3 适配层职责（HTTP / Socket / CLI / 触发器）

- 认证（Cookie/CSRF/Bearer）、HTTP 参数解析、请求体校验**全部在适配层**完成，向服务层传入可信 principal 与经校验的资源范围。
- 适配层保留 session 回写语义（如新建 terminal 后同步用户会话配置）；服务层与执行链路**不读写 Flask session**。
- 定时触发器（阶段三）作为新适配层接入：从 Schedule 配置解析 principal 与 params，调 `create_task`，把返回的 task_id 记入 Occurrence。

## 5. 调用方迁移表

| 调用方 | 现状入口 | 上下文来源 | 门闸获取/释放 | 取消传播 | 迁移方式 |
|---|---|---|---|---|---|
| Web 聊天 | `POST /api/tasks`（tasks/api.py:200） | Flask session 快照 | 执行内 process_message_task 获取/finally 释放 | REST cancel by task_id | 适配层构造 principal+params → create_task |
| API v1 | `POST /api/v1/workspaces/<id>/messages`（api_v1.py:320） | Bearer token 注入 session（is_api_user/role="api" 关键字段） | 同上 | 同上 | 同上；**is_api_user/role 必须进 principal，丢失会静默串资源管理器** |
| Workflow 激活/停用 | workflow_runtime_api.py:184/:264 | @with_terminal 显式构造 RuntimeContext | HTTP 层预占 + token 移交 + 失败回滚（restore_notices） | 同上 | 短期：仅下沉 Task 创建调用，编排留 HTTP 层；长期：会话补建/门闸移交/回滚下沉工作流服务 |
| 完成通知派发 | chat_flow_task_main.py:613 | web_terminal 属性显式构造 | 轮询器预占 + token 移交；**含异常回退路径（:645-675）** | 同上 | 构造 helper 统一（RuntimeContext.from_terminal） |
| 多智能体 idle 派发 | chat_flow_task_main.py:1416 | web_terminal 属性显式构造 + task_type="notice" | 无预占（notice 豁免 chat 互斥） | 同上 | 保留 notice 豁免语义 |
| CLI（未来） | —— | RuntimeService 内部接口直调 | 服务层获取 | 服务层 cancel | 阶段二验收的替身调用方 |
| 定时触发器（阶段三） | —— | Schedule 配置解析 | 服务层获取 + 重叠策略 | Occurrence.run_id → cancel_task | 阶段三新增 |

## 6. 审批与用户提问的语义边界

- 审批（tool/plan）与提问（ask_user）**共享** ID、关联、等待（asyncio.sleep 轮询）与回答（REST 写 manager）的基础设施，但**语义不同**：提问答案是任务输入，不能被当作工具执行授权；审批决定是授权，不能被当作任务输入。
- 无人值守场景（阶段三）的超时语义按三类分别定义（等待人工 / 到期终止 / 拒绝当前动作后继续），属产品决策；**不阻塞保持原有语义的阶段二上下文重构**（阶段二仅建立 `approval_timeout_seconds` 透传机制，默认 3600s 不变）。
- 已知缺口（记录待办，非本阶段修复）：超时/取消不回写 pending 终态；条目无 TTL；审批条目的 task_id 字段疑恒 None（静态疑点，待运行时验证）；socket 软 stop 不能打断等待（REST 硬取消可以）。

## 7. 回归用例清单

阶段二改造完成后必须可复现验证：

| # | 用例 | 验证点 |
|---|---|---|
| T01 | 显式上下文受理（无 HTTP 请求、无 test_request_context） | 直接构造 principal+params 调 create_task，返回 task_id |
| T02 | 同对话并发 chat 受理 | 第二个被拒绝（互斥错误码），notice 类型放行 |
| T03 | 不同对话并行 | 互不影响，各自 terminal 隔离 |
| T04 | 取消 | create_task 后 cancel_task，终态 stopped，stop_flags 清理 |
| T05 | 门闸保护 | 执行期间第二个主 Run 无法获取门闸；token 移交认领路径（通知链模式） |
| T06 | 异常回退路径 | create 失败回退直接执行时，门闸/事件/取消语义不破坏 |
| T07 | 保存不丢消息 | merge-on-save：磁盘独有保留、内存独有追加、缩减拒绝 |
| T08 | 审批重复回答 | 第二次 decide 返回现状，不改结果 |
| T09 | 偏移恢复 | get_task_events 按 offset 续读，idx 单调 |
| T10 | 资源解析回归矩阵 | host / web / API（is_api_user）三种身份 × 不同工作区 × 不同默认值来源，terminal 落到正确工作区与资源管理器（最高风险点） |
| T11 | Web 行为兼容 | 现有冒烟 `test/test_server_refactor_smoke.py` + 手工验证 Web 聊天/停止/审批 |
| T12 | 执行链路零 Flask 依赖 | `grep test_request_context server/ core/ modules/ utils/` = 0 命中；`grep "session\[" server/tasks/models.py` = 0 |

## 8. 本阶段明确不做

- 不重写 Agent loop、前端状态管理、部署拓扑；不换传输（SSE/WS）；不做事件持久化。
- 不实现 Schedule/Occurrence（阶段三）；不合并 Web/API 用户数据空间；不做设备配对/远程执行。
- 不把「门面转发成功」当作「状态唯一 Owner 完成」——迁移完成以本表逐项封闭旧写路径为准。

## 2026-10-23：当前 Run 的压缩请求

`RuntimeService.request_compression(username, workspace_id, conversation_id)` 按完整身份查找活动 Run：空闲返回 None，由原压缩端点创建手动任务；运行中返回 `{task_id, queued: true, compression}` 并通过当前任务发布排队状态。该操作只登记请求，不产生新的执行写入者。已停止／关闭受理的 Run 拒绝新请求。

TaskRecord 的 `compression_request`（空／pending／running）与 `compression_requests_closed` 在 `VIEW_LOCK → 任务锁` 下维护，完整响应及整批工具结果回填后消费，任务停止或终态清空。`mode="requested"` 深压缩后在原 Run 内续接并保留工作段锚点；排队状态从统一显示投影恢复。客户端收到 queued ACK 后沿用原游标，缺少订阅时重新取得显示快照，禁止从事件0恢复已有 Run。

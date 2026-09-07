# Gateway 改造实现审核记录

> 审核日期：2026-09-07
> 审核方式：主智能体综合审阅，3 个 Luna 子智能体分别核查服务初始化、协议与多端交互、执行环境边界。
> 审核基线：`b4deee0f`；目标提交：`d7cb854f`。结论只描述该提交快照，后续修复须重新验证。
> 本轮只审核并记录，未修改业务代码。本文不替换既有规划或子报告。

## 1. 范围与结论

本次审核以下 4 个提交：

| 提交 | 内容 |
|---|---|
| `d9bd599c` | Gateway 独立启动与公共协议落地 |
| `27aeed70` | Execution Contract 与替身执行器 |
| `4378eb6a` | 会话查询公共入口与代码就绪度收口 |
| `d7cb854f` | 审批/提问公共入口与①②③链路收口声明 |

对照依据：[当前状态规划](gateway_current_state.md) §7、[公共协议](../../docs/runtime_protocol.md)、[执行契约](../../docs/execution_contract.md)，以及前序关于四层职责、公共入口、状态归属和行为验收的约定。

**结论：改造已有实质进展，但现有证据尚不能支持“①客户端—②Gateway—③Runtime 已完整贯通”的完成声明。** 当前完成的是主要进程内服务能力、独立初始化相关拆分和 E1–E4 替身接入；公共任务发现、既有适配入口收敛、资源范围校验及验收可靠性仍需收口。

按规划中记录的本轮收口范围，生产 Host/Docker 迁入 ExecutionBackend、E5–E10 全量接入、正式 CLI/GUI/IDE 客户端开发均已后置。本文不把这些已明确后置的工作算作本轮违约，也不要求事件全量持久化、重启续跑或 Remote Worker。

## 2. 完成度核查

| 能力 | 审核结论 | 证据与限制 |
|---|---|---|
| 任务核心与 Web 路由加载拆分 | 已实现 | `server/tasks/__init__.py` 不再顶层装配 Blueprint 与路由；`server/tasks/web.py` 承担 Web 装配 |
| 非 Web 环境的消息输出与后台任务启动 | 已提供适配 | `server/extensions.py` 增加 `emit_event` / `run_background`；独立生命周期仍需加强验收 |
| Run、审批、会话查询公共方法 | 主要方法已实现 | `server/runtime/service.py`；存在 F1、F3，且 Web 调用尚未全部迁移（F2） |
| 事件 offset 与窗口水位 | 进程内接口已实现 | `get_task_events` 返回 `meta.window_start`；现有 HTTP 轮询未透传该信息 |
| 多客户端发现、操作与恢复闭环 | 部分完成，未充分验收 | 新客户端缺活动 Run 发现入口；测试没有覆盖真实 A 发起/B 操作和断连重连 |
| CLI/GUI/IDE 独立接入 | 部分服务基础已具备 | 进程内直调成功不等于已有独立传输适配器；正式客户端后置可以接受，但应准确限定“代码就绪度” |
| ExecutionBackend | E1–E4 替身接入已实现 | 默认 `execution_backend=None`；生产 Host/Docker 与 E5–E10 后置，符合当前范围记录 |

## 3. 本轮需要收口的发现

以下均列为 **P2：本轮完成验收前应修正或明确处理的事项**。其中 F1/F2 是能力与迁移缺口，F3 是新增公共接口的资源校验缺口，F4 是验证缺陷；不应全部表述为线上行为回归。

### F1：新客户端无法通过公共入口发现已有任务

- **位置**：`server/runtime/service.py:277`、`docs/runtime_protocol.md:49–56`。
- **现状**：RuntimeService 只有按已知 task_id 查询的 `get_task` / `get_task_events`，没有 `list_runs` / `list_tasks`。底层 `TaskManager.list_tasks` 已存在，旧 Web `/api/tasks` 也提供列表，但没有提升到公共服务。
- **触发场景**：客户端 A 发起任务后，客户端 B 新连接，尚不知道该 task_id。B 无法仅通过当前公共协议发现 A 的活动 Run，仍需旧 Web 列表接口或其他协议外信息。
- **影响**：仅验证 `get_task(task_id)`，不足以证明“B 经公共入口查询并操作”已经成立。
- **建议**：增加受授权的 Run 发现查询，可按工作区、会话和活动状态筛选；复用底层能力，返回适合公共协议的结果。
- **验收**：A 创建 Run；B 只持已授权身份及工作区/会话范围，通过公共查询取得该 Run，再观察、回答审批或取消，不从 A 的测试变量直接获取 task_id。

### F2：既有适配入口未统一，新恢复信息未到达实际客户端

- **位置**：`server/tasks/api.py:236/281/304/332/355/377`、`server/api_v1.py:424/432/453`、`server/chat/approval.py:56/71/100/115/143/159`。
- **现状**：Web/API 的事件轮询、取消、引导、队列操作仍有直接调用 task_manager 的路径；三类审批查询/回答仍直接调用对应 manager。
- **具体差异**：RuntimeService 的事件查询新增 `meta.window_start`，旧 HTTP 轮询仍用 `get_events_since` 构造响应，不包含该水位。新增协议的缺口检测信息尚未到达这些客户端。
- **影响**：“公共方法已提供”不能等同于“既有入口均已转调”。多条路径仍可独立演进，新增语义也不会自动共享。直接调用同一 manager 不等于出现多份状态，但不满足本轮入口收敛约定。
- **建议**：相关 Web/API 路由转调公共服务，保留现有认证及兼容响应；将事件窗口信息适配到响应，并验证消费者的缺口处理。更新完成速览，区分接口存在、适配完成和行为验收。
- **验收**：同一组受理、取消、队列和审批行为，经 Web 适配与直接服务调用得到等价结果；裁剪事件窗口后，调用方能检测缺口并按已定义规则重新同步，不仅验证未裁剪时 window_start=0。

### F3：会话查询未检查 principal 绑定的工作区

- **位置**：`server/runtime/service.py:250–270`，重点为 `:254–269`。
- **现状**：`_resources_for_query` 只检查 `principal.username == username`，没有检查 principal 的 workspace_id 与查询目标一致，随后直接按调用参数的 workspace_id 装配资源。
- **已复现**：主侧用资源解析替身调用 `_resources_for_query('alice', 'B', TrustedPrincipal(username='alice', workspace_id='A'))`，得到 `resource_workspace='B'`，没有被拒绝。
- **影响**：服务接口未守住 TrustedPrincipal 所声明的资源范围。当前是进程内可信调用接口，不能据此断言已有外部可利用漏洞；但新适配器将外部查询参数传入时，这个不一致必须被阻止或经明确重新授权处理。
- **建议**：查询目标与已授权 principal 范围保持一致；若允许切换工作区，应显式完成目标授权，而不是直接信任第二份 workspace_id 参数。
- **验收**：覆盖同名用户不同工作区、用户名不一致、正确范围查询，以及 host/API/web 身份。当前测试只检查用户名不一致。

### F4：独立验收测试存在假通过、隔离及配置依赖

- **位置**：`test/runtime_standalone_checks.py:25–27/83–144/147–258`、`config/__init__.py:51–52`。
- **假通过**：生命周期测试接受任意 failed 终态；terminal 不存在时跳过门闸检查。主侧在缺少模型配置、资源装配失败的环境中，观察到该生命周期测试仍通过。
- **交互覆盖不足**：chain 测试在 Run 结束后手工向 manager 创建审批，再验证公共入口路由。这能验证查询、裁决和重复回答的服务行为，但不能证明“执行中的任务进入等待→另一客户端回答→原任务继续”。提问与计划确认的完整等待链同样缺少对应证据。
- **隔离缺口**：测试设置临时 ASTRION_DATA_ROOT 后，config 仍会用源码目录 `.env` 的同名值覆盖。子审核在当前工作区执行时，观察到尝试访问真实运行态目录并被写权限拒绝；因此原隔离设置不能作为可靠保证。
- **配置依赖**：主侧在无 `.env`、无真实凭证的隔离源码副本中执行，fake_exec 因“未配置可用模型”失败，chain 因没有建立会话 ID 失败。测试未提供自包含的模型配置与受控响应。
- **建议**：提供确定的测试资源根、模型配置和模型响应替身，保留真实任务线程与资源装配。分别断言成功、取消、审批等待/继续、预期异常及清理结果，不用任意 failed 替代成功验收；强制确认 terminal、会话历史和门闸状态。
- **验收**：全新隔离目录、无真实凭证、禁用网络时可稳定复现；改变开发者 `.env` 不改变测试数据路径；人为使装配失败时，成功生命周期用例必须失败。

## 4. 已后置执行层工作的契约提醒

`modules/execution_plane/base.py:29` 声称路径授权与 `_validate_command` 已在 Runtime 编排层完成，但当前真实命令校验仍位于旧 `terminal_ops` 链路，文件路径检查也不能只依赖“先读后写”前置检查。注入 backend 的新分支未完整复用这些旧后端校验。

**当前默认生产路径未因此改变，不将其报告为现有 Host/Docker 权限绕过。** 但未来接入真实 ExecutionBackend 前，必须准确分配并保留命令校验、路径授权和实际执行限制；不能按照当前注释误认为上层已完成全部检查。

此项跟随已后置的③↔④工作处理，不作为本轮①②③收口的额外扩张。

## 5. 验证记录与限制

主侧测试方法：从目标 HEAD 导出临时源码副本（不带未跟踪 `.env`），指定临时数据/部署配置目录，移除继承的模型凭证，并通过测试进程的网络连接拦截禁止外部调用。未修改被审核源码。运行器使用仓库 `.venv/bin/python` 的 unittest discovery，未依赖 pytest。

运行以下 5 组测试，共 **28 项：26 通过、2 失败**：

- `test_server_refactor_smoke.py`
- `test_runtime_service.py`
- `test_runtime_identity_resources.py`
- `test_conversation_model_persistence.py`
- `test_runtime_standalone_lifecycle.py`

| 失败用例 | 观察结果 | 解释 |
|---|---|---|
| `ExecutionPlaneFakeBackendTest.test_runtime_with_fake_execution_backend` | get_default_model_key 抛“未配置可用模型” | 未进入替身工具断言，不能认定工具实现回归；证明测试配置不自包含 |
| `ProtocolSmokeChainTest.test_full_chain_run_history_offset_approval` | “Run 应建立会话 id”断言失败 | 本次隔离环境下会话装配未成功，不能作为全链路完成证据 |

对照观察：同一环境下 `StandaloneRuntimeLifecycleTest` 通过，结合其接受 failed 及可跳过 terminal 检查的逻辑，确认 F4 的验收假通过问题。

另外完成了 F3 的最小替身复现、适配入口静态对照及最近提交范围核验。未执行真实模型调用、真实多客户端联调或 UI 人工验收；不据上述测试结果声称这些场景已通过，也不将这两项失败直接归为生产业务回归。

## 6. 建议收口顺序

1. 补公共 Run 发现查询，修复会话查询的工作区范围校验。
2. 将本轮承诺的 Web/API 控制、审批与事件观察入口转调公共服务，落实事件缺口响应与处理。
3. 修复测试隔离和自包含配置，用受控模型响应验证真实装配、执行中审批/提问、取消及清理。
4. 用两个相互独立的客户端调用方完成 A 发起、B 发现并操作、断连及重连验收；传输适配尚未实现时，明确记录进程内调用的实际覆盖范围。
5. 根据证据更新 `gateway_current_state.md` 完成速览；其余生产执行后端与正式客户端工作继续按已后置范围推进。

完成上述收口后，再将“主要服务方法已具备”升级为“本轮①②③边界已按约定验收”。

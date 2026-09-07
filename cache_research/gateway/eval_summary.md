# Gateway 化改造范围与复杂度评估（汇总）

> 日期：2026-09-07
> 依据：主智能体核心链路精读 + 4 个子智能体只读盘点（静态分析，未做运行复现）
> 子报告：eval_task_entry_points/、eval_flask_context_deps/、eval_endpoint_classification/、eval_event_approval_coupling/
> 对照计划：../gateway_work_plan.md（三阶段路线）

> **审阅注释（2026-09-07｜阅读说明）**：以下保留原始评估，通过注释标出修正和设计边界。总体认可三阶段路线：阶段二是范围可控、少数枢纽风险较高的重构，阶段三是主要新增工作量；静态分析与端点计数不能替代行为验收。对照计划实际位于同目录 `gateway_work_plan.md`。下一步建议编写 `docs/runtime_contract.md`，不再扩大端点普查。

## 0. 核心结论（直接回答「工作量是否非常大」）

**「196 个 API 端点需要 Gateway 化」是误解。** 逐一读完 196 个端点函数体后确认：

| 类别 | 数量 | 占比 | 是否迁移 |
|---|---|---|---|
| T1 任务受理 | 3 | 1.5% | ✅ 必须（POST /api/tasks、/api/v1/.../messages、/api/workflow/activate） |
| T2 任务控制 | 13 | 6.6% | ✅ 必须（cancel×2、runtime_guidance/queue×4、workflow deactivate、sub_agents/background 停止×3、审批回答×3） |
| T3 任务观察 | 14 | 7.1% | ⚠️ 可不动（只读 REST，轮询协议天然可复用） |
| C CRUD | 131 | 66.8% | ❌ 不动 |
| S 状态查询 | 20 | 10.2% | ❌ 不动 |
| A 认证管理 | 15 | 7.7% | ❌ 不动 |

**必须迁移的最低集合 = 16 个端点（8.2%）**，且它们全部收敛到同一批 manager 单例方法（task_manager / sub_agent_manager / background_command_manager / 三个 approval manager）。Gateway 化的实质 = **把这几个 manager 方法提升为 RuntimeService 公共入口**，HTTP 端点从「直接调 manager」改为「调 RuntimeService」，而不是改写端点本身。

范围边界（按 R1 修订）：16 个是当前分类下的受理/控制端点集合，**不是架构改造完成的充分条件**。`server/chat/permission.py` 的权限/执行环境/网络权限变更端点在任务运行中会向运行态排队生效，必须进入阶段一的状态责任表；其余 166 个端点应描述为「多数可保持 HTTP 兼容不动」，不能概括为与运行时零耦合。查询端点可保留原 URL，但 RuntimeService 应同时暴露内部查询接口（如 `get_task_events`），后台调用方（CLI/定时任务）不必为观察任务再发 HTTP 请求。新增门面也不等于全部写入口已收敛。

> **审阅注释（2026-09-07｜R1 范围）**：16 个是当前分类下的受理/控制端点集合，不是架构改造完成的充分条件。`server/chat/permission.py` 的权限、执行环境和网络权限变更会向运行态排队，必须进入状态责任表；其余端点应描述为“多数可保持 HTTP 兼容”，不能概括为与运行时零耦合。查询端点可保留原 URL，但后台调用方应能通过内部接口查询，不必为了观察任务再发 HTTP 请求。新增门面也不等于全部写入口已经收敛。

迁移复杂度分布（16 个，按端点子报告明细表核对）：**12 小 / 3 中 / 1 大**（唯一的大项 = workflow activate，因门闸 token 移交 + 状态机编排）。过渡方案是只下沉 Task 创建调用、编排留 HTTP 层；按 R2，会话补建、激活、门闸移交和失败回滚属于业务流程，长期应按复用需求下沉到工作流服务供非 HTTP 入口调用（无需全部塞入 RuntimeService）。

> **审阅注释（2026-09-07｜R2 计数与编排）**：11 + 3 + 1 = 15；按端点子报告详细表应为 **12 小 / 3 中 / 1 大 = 16**，这仍是定性估算。Workflow 编排留在 HTTP 层只适合作为过渡：会话补建、激活、门闸移交和失败回滚属于业务流程，应按复用需求下沉到工作流服务，供非 HTTP 入口调用；无需全部塞入 RuntimeService。

Socket.IO 侧：10 个事件中仅 `stop_task`（T2）活跃；`send_message` 已短路废弃（死代码），聊天主交互全走 REST。

## 1. 关键利好：代码现状比预期更适合改造

1. **任务链路 Flask 依赖是「浅层入口型」**：B 类（必须消除）仅 6 项，集中在 2 个文件——`server/tasks/models.py`（15 行）+ `server/context.py`（31 行）。执行体完全干净：`core/`、`modules/`、`utils/` 零 Flask 依赖；9 个 `chat_flow*.py` 文件函数体内零 session 使用；WebTerminal 无 `self.session` 属性（同名变量全是容器句柄）。
2. **执行链已是单一收敛的（限正常受理路径）**：5 类来源、6 处调用位置共用 `create_chat_task → 线程 → _run_chat_task → run_chat_task_sync → process_message_task → handle_task_with_sender` 一条链。例外（按 R3 补充）：`chat_flow_task_main.py:645-675` 存在异常回退——任务创建失败后直接 `handle_task_with_sender`，**绕过 TaskRecord 登记**（无事件 deque、不可按 task_id 轮询/取消）；该回退在外围轮询器预占门闸保护下运行，不能仅凭此认定并发写入 bug，但**迁移验收必须覆盖回退路径的记录、事件、取消和门闸生命周期**。socket `send_message`（socket_handlers.py:259）已短路返回 DEPRECATED，属死代码，与该活跃回退是两回事。另按 R3：`core/modules/utils` 无直接 Flask 导入已确认，但不能外推为所有间接调用都不依赖请求上下文，需回归验证兜底。

> **审阅注释（2026-09-07｜R3 依赖与异常路径）**：本轮窄核验确认 `core/modules/utils` 无直接 Flask 导入，但不能外推为所有间接调用都不依赖请求上下文。“无旁路”须限定为正常受理路径：`server/chat_flow_task_main.py:645–675` 在任务创建失败后直接执行 `handle_task_with_sender`，绕过新的 TaskRecord 登记；外围轮询器有预占门闸，因此不能仅凭回退认定并发写入 bug。迁移验收必须覆盖异常回退的记录、事件、取消和门闸生命周期。Socket `send_message` 在 `server/socket_handlers.py:259` 已短路返回，应与该活跃回退分开。“5 个调用点”实际是 5 类来源、6 处调用位置。
3. **签名已大部分显式化**：`create_chat_task` 15 个参数、`process_message_task(terminal, message, sender, workspace, username, gate_token...)` 已是显式签名。
4. **门闸是独立干净组件**（main_task_gate.py 73 行，挂 terminal 对象，零 Flask 依赖），直接复用。
5. **支撑链路 3/4 可直接复用**：事件（task_id+idx+用户房间）、取消（username+task_id 寻址+硬取消）、保存（merge-on-save + I/O 锁 + 门闸三道防线）——耦合键语义已收敛为 task_id/username/conversation_id，无 terminal_id 耦合。

## 2. 真实工作量构成

### 阶段一：固定契约（文档为主）
- 产出 `docs/runtime_contract.md` + 状态责任表 + 概念对齐 + 调用方迁移表 + 回归用例
- **复杂度：低**。不写生产代码，但需要精读现状（本次盘点已完成大部分素材积累）

### 阶段二：公共任务入口（核心改造）
| 改动项 | 位置 | 复杂度 |
|---|---|---|
| 新建 RuntimeContext + RuntimeService 接口。RuntimeContext 按 R4 分三层：**可信身份与资源范围**（username/workspace_id/host_mode/host_workspace_id/is_api_user/role）/ **本次任务参数**（run_mode/thinking_mode/model_key/message 等）/ **内部执行信息**（门闸 token、通知回滚数据——**不得成为普通客户端可提交字段**）；明确默认值、对话配置与本次覆盖的解析优先级，避免仅将 session_data 大字典换名 | 新文件 | 小 |
| create_chat_task 快照显式化（删除 session 直读 else 分支与 setdefault 兜底，无上下文时拒绝受理而非静默空快照） | models.py:177-209 | 中 |
| 拆除 test_request_context 桥 | models.py:794-810 | 中 |
| **get_user_resources 参数化**（host/docker/api 三分支，is_api_user/host_mode 选错即静默串工作区） | context.py:221-535 | **中～大（最高风险）** |
| 迁移 6 个调用点到 RuntimeContext | tasks/api.py、api_v1.py、workflow_runtime_api.py×2、chat_flow_task_main.py×2 | 各小～中 |
| 审批超时参数透传（调用点补传 timeout_seconds） | chat_flow_tool_loop.py:435/574/883/1153 | 小 |
| 配套：`_apply_workspace_personalization_preferences` 参数化、`ensure_conversation_loaded` 回写上移、auth_helpers record/role 显式化 | context.py、auth_helpers.py | 小 |
| 回归测试（同对话并发/跨对话隔离/保存不丢消息/取消/审批重复回答/偏移恢复） | test/ | 中 |

- **复杂度：中**。触及生产代码约 6-8 个文件（估算），每处改动有明确的回退策略（先加显式参数变体、保留兼容期、再拆桥——两步法）。按 R4 补充：资源解析 `get_user_resources` 有约 170+ 调用处（含 83 处 @with_terminal 装饰器路径），影响面需通过 host/Web/API 身份 × 不同会话 × 不同默认值来源的回归验证覆盖，不宜承诺每处改动都小。

> **审阅注释（2026-09-07｜R4 上下文设计与工作量）**：9 字段是旧 session 依赖的搬迁清单，不是最终领域模型。至少区分“可信身份与资源范围”“本次任务参数”“内部执行信息”；门闸 token、通知回滚等内部信息不得成为普通客户端可提交字段。明确默认值、对话配置与本次覆盖的解析优先级，避免仅将 session_data 大字典换名。6–8 个文件属于估算，资源解析有约 170+ 调用处（含装饰器路径，见上下文子报告），影响面需通过 host/Web/API 身份、不同会话、不同默认值来源的回归验证；不宜承诺每处改动都小。

### 阶段三：定时任务（净新增子系统）
| 新增项 | 说明 | 复杂度 |
|---|---|---|
| Schedule/Occurrence 持久化 | 选型文件或 SQLite（先列原子更新/唯一性/查询/恢复要求），走运行态路径 | 中 |
| 调度器循环 | 单活动所有权防多进程重复派发；tick 扫描到期 Occurrence | 中 |
| 幂等与恢复 | 触发标识 = schedule_id+计划时间点；崩溃窗口对账；重启恢复计划与记录 | 中～大 |
| 审批无人值守语义 | 超时注入 + 超时语义决策（拒绝工具继续 vs 结束任务，**产品决策**）+ 等待循环 stop 检查 + 条目 TTL | 中 |
| 触发记录查询端点 | 新增 T3 类端点（旧任务清理/重启后仍能解释触发结果） | 小 |
| 前端 UI | 计划管理界面（创建/暂停/恢复/删除/触发历史） | 中 |

- **复杂度：中～大**。全新代码，但可与阶段二解耦验证（可控时钟 + 执行替身）

## 3. 风险与难点排序

1. **get_user_resources 参数化**（阶段二）——host_mode / is_api_user 分支选错会**静默串工作区**，是全改造最高风险点。缓解：先加显式参数变体与 web 路径并存，逐个调用方迁移。
2. **门闸 token 移交语义**（阶段二）——「预占→session_data 移交→线程认领→finally 释放/失败回滚」是跨线程隐式协议，RuntimeContext 必须原样承载。
3. **`task_type="notice"` 互斥豁免**（阶段二）——多智能体/完成通知链路依赖它跳过单对话互斥，重排互斥规则会引发并发回退。
4. **审批超时语义**（阶段三，按 R5 修订）——拒绝一个工具后继续运行不自动意味着不安全（后续动作仍受权限约束）；等待人工、到期终止、拒绝当前动作后继续是不同产品策略，「定时任务禁止人工审批」不是必选技术条件；工具审批、计划审批、用户提问应分别定义超时含义。**该决策不阻塞阶段二**（阶段二上下文重构保持原有语义即可先行）。

> **审阅注释（2026-09-07｜R5 审批产品边界）**：拒绝一个工具后继续运行不自动意味着不安全，后续动作仍受权限约束。等待人工、到期终止、拒绝当前动作后继续是不同产品策略，不能把“定时任务禁止人工审批”当作必选技术条件。工具审批、计划审批、用户提问也应分别定义超时含义。超时策略可在阶段三明确，不必阻塞保持原有语义的阶段二上下文重构。
5. **事件前台干扰**（阶段三）——定时任务事件会推到在线用户的 socket 房间，需加 source 字段或确认产品预期。
6. **三套身份取数来源统一**（阶段二）——web session / token session / web_terminal 属性，语义等价但路径不同，需收敛为 `RuntimeContext.from_*` 构造族。

## 4. 附带发现（与改造无直接依赖，建议独立处理）

- **疑似 bug（静态疑点，待运行时验证）**：审批条目的 `task_id` 字段实际恒 None——`getattr(web_terminal, "task_id", None)`（chat_flow_tool_loop.py:411/545/845/1115）全仓无赋值点。高置信推断，未排除动态 setattr；按 R6 应验证运行时载荷后定性。影响：审批无法按 task_id 检索关联。
- **静默降级隐患**：`create_chat_task` 无 session_data 时 `except Exception` 吞错后得到空快照——阶段三定时任务若直调旧入口会静默丢身份。阶段二的显式化会顺带消除。
- **审批等待期间软停止无效（按 R6 限定范围）**：标准停止按钮走 REST 硬取消，**可以**打断审批等待；缺口仅限「仅设置软停止标志」的路径（socket `stop_task` 软 stop），下一次工具调用行首检查才生效。另需跟进：超时/取消后 pending 条目的终态更新、审批与 Run 的关联；条目清理不能只靠 TTL 删除仍有合法等待者的请求。

> **审阅注释（2026-09-07｜R6 取消与生命周期）**：支撑链报告 §3.1 明确标准停止按钮走 REST 硬取消，能够打断审批等待；缺口应限定为仅设置软停止标志的路径，不能描述成所有停止按钮失效。另需跟进超时/取消后的 pending 终态更新和审批与 Run 的关联；task_id 恒 None 目前仍是静态疑点，应验证运行时载荷后定性。条目清理不能只靠 TTL 删除仍有合法等待者的请求。

## 5. 建议实施顺序

1. 阶段一契约文档（本次盘点报告可直接作为素材底稿）
2. 阶段二两步走：① RuntimeContext（三层分离）+ 显式受理签名（保留 test_request_context 兼容兜底）→ ② get_user_resources 参数化后拆桥。**审批超时语义决策不阻塞本阶段**（保持原有语义，仅建立参数透传机制）
3. 阶段三定时任务（可控时钟 + 执行替身先行验证，再接真实入口；审批/提问/计划三类超时含义在本阶段分别定义）

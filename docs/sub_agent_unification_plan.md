# 普通与多智能体合并：实施记录

整理：2026-10-21；实施更新：2026-10-22。

用户已授权修改实际代码，并补充要求：历史多智能体主对话通过独立脚本迁入主目录；业务代码不加入双目录读取或旧子任务恢复兼容。当前前后端实现已落地并通过本文列出的离线验证，真实原始数据尚未迁移，服务未重启，真实模型与界面仍需运行验收。

## 1. 最终产品行为

普通对话直接使用全部子协作能力，不再选择独立多智能体类型。子实例保留上下文，完成一轮后 idle，后续可发消息继续。

- 取消预设／自定义角色，通过 intelligence 选择模型。
- 子智能体全部后台执行；创建没有 run_in_background。
- 删除 finish_task、强制交付文件与结束文件。
- 每轮只通知最终回复，普通中间输出留在子活动记录。
- 最终通知以 user 消息进入主对话；主子、子子问答保留，子子通信也汇报主对话。
- 公开用 name 寻址；内部编号不对模型暴露。
- sleep 只保留短延迟与后台命令等待。
- MCP 为外置插件，不套内置工具目录写权限沙箱。

## 2. 工具与参数

| 工具 | 行为 |
| --- | --- |
| create_sub_agent | 创建命名子实例，后台开始，立即返回 |
| send_message_to_sub_agent | idle 时沿用上下文开启新轮；运行中作为安全边界引导 |
| get_sub_agent_status | 省略names查询全部；传入非空names批量查询指定实例，包含状态、轮次、结束原因和最近最终回复 |
| stop_sub_agent | 即时请求取消当前响应／工具，缺失结果标为被主智能体取消，保留上下文并进入idle |
| terminate_sub_agent | 终结实例，不再恢复或复用名称 |
| answer_sub_agent_question | 按 question_id 回填 ask_master 结果 |

子侧保留 ask_master、ask_other_agent、answer_other_agent、get_sub_agent_status。主子共用同一查询实现，统一results数组；原list_active_sub_agents删除。模型工具结果不返回task_id/conversation_id，内部记录与前端运行视图保留两者。

创建必填 name、summary、task、intelligence、access_level；可选 working_dir、timeout_seconds。不再提供 role_id、agent_id、deliverables_dir、run_in_background 或角色思考参数。

## 3. 单轮预算与消息边界

用户明确要求：预算到期时必须接完当前模型响应，执行该响应产生的整批工具并回填真实结果，再阻止下一次模型请求。预算不是工具取消信号，不使用硬超时销毁实例。

省略 timeout_seconds 表示本轮不限时；发送新任务时省略预算，新轮同样不限时。运行中追加引导不会重置当前预算。问答等待是已经开始执行的工具，预算到期不截断它；其自身问答超时仍按工具参数处理。

自然回复无 tool_calls 时结束本轮、进入 idle。空正文有明确结束提示。网络重试耗尽或暂停也产生带原因的最终通知；硬终结为吸收态。真实进程中断恢复遇到未回填工具结果时，明确标为执行结果未知，不能将未知伪装为已完成。

已接收工作保存到 pending_work；子事件循环在安全边界写入上下文，异步唤醒本身不能触发空的新一轮。恢复保留原消息、模型、用量和固定权限。

显式stop与预算不同：取消当前执行worker，保留实例监督循环；半截响应不提交，已完成工具结果保留，已提交批次的缺失结果补齐取消消息。工具发起为“被主智能体取消”，界面发起为“被用户取消”。停止后持久化work_paused，保留排队消息但不自动重启；收到新消息再继续。terminate优先且不可恢复。取消不撤销已发生的外部操作。

## 4. 名称、工作目录和权限

同一主对话内 name 去两端空白、1–80字符、大小写不敏感唯一；禁止控制字符及标签／路径分隔符。终结后不复用名称，不支持隐式重命名。

working_dir 可复用已有项目内目录；不传新建 `.astrion/sub_agent_work/agent_N[_suffix]/`。显式不存在目录可安全创建。项目外目录不在当前实现范围；目录被替换、重定向或丢失时不静默扩大权限。

| access_level | 内置工具写入边界 |
| --- | --- |
| working_dir_write | 仅子工作目录 |
| workspace_write | 当前项目工作区 |
| sandbox_write | 当前工作区＋沙箱授权可写路径 |
| full_access | 宿主完全访问，受现行平台可用性约束 |

工作区身份始终是原项目根，不能为了窄写权限把 workspace_root 改成工作目录。相对文件路径和默认命令 cwd 使用 working_dir。读取沿用项目与授权规则；写入由原生文件描述符检查及各平台沙箱共同限制。父对话切换权限不改已有子档位。MCP 不受本表的内置工具目录写沙箱约束。

## 5. MCP 接入

每子实例独立 manager／客户端，复用父注册表、工具协议和 alias，只装配父可见且未禁用的工具。调用不进入子内置 ExecutionScope；intent 不传给外置服务。

MCPRuntimeScope 增 actor_id 和 working_dir。默认 Playwright profile 的哈希加入子 task_id，使父／各子实例分开；同一子实例恢复复用原profile。管理员显式指定的共享 profile／CDP／远端服务不改变。

子 stdio 服务未显式设置 cwd 时使用子 working_dir；显式 cwd 保留。idle 保留实例连接；执行实例实际退出时关闭所属连接。工具重建先关闭旧 manager。结构化数据和模型支持的图片进入子上下文。

## 6. 单目录存储与历史迁移

所有主对话只读写普通 ConversationManager 的 conversations 目录。新建、列表、搜索、索引、bootstrap、前端缓存、快速对话和 Gateway 均取消类型参数和分流。

旧多智能体主对话离线迁移见 [迁移说明](sub_agent_migration.md)。独立脚本不 import 应用配置，不扫描猜测目录，默认预览，显式 --apply 才写。保留 ID、消息、待办和用量，清理旧对话类型／角色冻结字段；预检冲突、备份原 bytes、失败自动回滚。

新子状态仅使用 `<工作区 data_dir>/sub_agent_runtime.json`（version=1），子上下文在 `sub_agent_tasks/<task_id>/conversation.json`。不读取或迁移旧传统 sub_agents.json，不承诺旧子任务恢复。用户原始文件不因取消角色源码而删除。

## 7. 通知、并发和显示

每个工作区data_dir共享同一任务／实例／通信注册表。查询不得替换活动实例持有的记录，恢复串行化，后台任务在开始前登记句柄。

通知生产者入持久化队列。主任务运行中在完整工具批次后按顺序插入；主任务空闲时轮询预占主任务门闸，经 RuntimeService 派发新 notice。消息池有内容即可消费，不等所有 running 子实例退出，避免 ask_master 死锁。

前置通知在受理并认领门闸后才落盘；受理失败回队。逐条持久化失败只回队未写入尾段，不重复已成功消息。显示沿用现有快照与message_id，协作消息 `multi_agent_*` 是显示协议，不再是主对话类型。

idle 不算后台运行。保留传输字段 has_running_multi_agent 来表示运行子实例或待消费协作消息；不创建类型UI或第二套缓存。工作流／后台命令通知保持独立队列。目标模式暂停和主任务单写者约束保持。

## 8. 主要实现位置

- `modules/sub_agent/definitions.py`：工具签名；`named.py`：公开名称接口。
- `manager.py`／`persistence.py`：共享记录、恢复和原子持久化。
- `task.py`／`engine.py`／`runtime.py`：生命周期、完整工具批次、预算和MCP。
- `communication.py`／`model.py`／`history.py`：问答、API流、用量与上下文。
- `execution.py`／`execution_scope.py`／各平台runner：固定内置权限与工作目录。
- `server/chat_flow_task_*`、`server/tasks/models.py`：通知消费、门闸与事件。
- `static/src/stores/workspaceConversationCache.ts`：单工作区列表缓存。
- `static/src/components/chat/actions/subAgentResults.ts`：统一工具显示。
- `scripts/migrate_multi_agent_conversations.py`：离线主对话迁移。

详细开发硬约束见 [统一子智能体契约](agents_details/multi_agent.md)。

## 9. 已执行验证与边界

2026-10-23补验：统一子智能体23项分别在项目 `.venv` Python 3.9.6 和 Homebrew Python 3.11.13 下通过；固定权限／审批35项、工具动态加载23项在Python 3.11下通过。公开创建入口测试覆盖模型解析、后台创建、自然idle、名称续接和保留上一轮上下文。修正了 `intelligence.py` 中会在Python 3.9加载时报错的联合类型注解。

此前已通过MCP作用域9项、Linux适配21项（3项平台跳过）、显示快照及工作区整套回归。前端正式构建和本轮改动文件定向lint通过；全量lint为0 errors、171 warnings，因项目要求零warning而未通过，未做无关全仓格式化。完整命令及补充结果以 `test/2026-10-22_子智能体统一/README.md` 为准。

本机实际 host 数据：217条旧多智能体主对话、7工作区，在隔离副本完成迁移、备份bytes与历史／待办／用量校验；生产普通存储能列出并读取全部217条。原目录hash未变化。副本含真实私有对话，保留在被Git忽略的本地运行目录，不应发布。

离线验证不等同真实模型调用、UI视觉、真实Chrome或Windows／Docker／Linux各原生平台验收。未执行真实原目录迁移、服务重启、Git提交或推送。

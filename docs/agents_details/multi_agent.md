# 统一子智能体与协作通知（完整）

更新：2026-10-22。对应 `AGENTS.md` §11；旧文件名保留用于现有文档引用。

## 11) 单一对话与命名子实例

所有主对话共用普通 ConversationManager。创建、列表、搜索、删除、bootstrap、CLI/Gateway 和快速对话不再按对话类型分流；不写 `metadata.multi_agent_mode`，不创建或读取第二套主对话存储，不保留双类型前端缓存。

旧多智能体主对话通过 `scripts/migrate_multi_agent_conversations.py` 离线迁入主目录，操作说明见 `docs/sub_agent_migration.md`。业务代码不做双目录回退或旧子任务恢复。迁移保留主对话 ID、消息、待办和用量，不恢复旧传统子任务。

### 11.1 公开工具和身份

工具定义唯一来源 `modules/sub_agent/definitions.py`，主分派走 `modules/sub_agent/named.py`。

- 主侧：`create_sub_agent`、`send_message_to_sub_agent`、`get_sub_agent_status`、`stop_sub_agent`、`terminate_sub_agent`、`answer_sub_agent_question`。
- 子侧：`ask_master`、`ask_other_agent`、`answer_other_agent`、`get_sub_agent_status`，加内置工具白名单及允许的 MCP 映射。
- `get_sub_agent_status`是唯一查询工具：省略names返回当前主对话全部已创建实例（含idle、terminated），传入非空names按名称查询；统一返回success/results，每项found标记是否存在。主子共用签名和查询实现，删除原list_active_sub_agents。
- 模型可见的创建／查询结果不含task_id或conversation_id；两者仅保留在内部记录与前端运行视图，用于活动关联，不影响name寻址。
- 删除角色创建、角色列表、角色模型、`finish_task`、两种子智能体 sleep 等待和公开 `run_in_background` 参数。预设角色资源不再随版本打包。
- 主智能体指定 `name`，1–80 字符，去两端空白，拒绝控制字符和标签／路径分隔符；同一主对话中 casefold 唯一，终结后不复用名称。寻址参数只用 name/names；agent_id 是内部索引，task_id 只用于运行和活动记录。
- 模型由 `intelligence=same/high/medium/low` 选择；恢复时保持模型、推理配置、固定权限、工作目录及原上下文，不静默换模型。

### 11.2 轮次、暂停和恢复

- **实例容量（2026-10-23）**：每个主对话默认最多保留20个未终结实例，配置沿用`SUB_AGENT_MAX_ACTIVE`。按`status != terminated`计数，idle、running及异常failed均占名额，terminate释放名额且保留历史；其他对话不占当前对话名额。不另设running数量限制，已有实例可全部唤醒。创建检查与登记在共享注册表锁内完成；存量超限不自动终结，只阻止继续新建。
- 全部子实例后台运行，创建立即返回，不阻塞主输入区，不发送子任务 `sub_agent_waiting`。
- 自然无工具回复结束本轮，保存上下文和最终回复，进入 `idle`。不需要结束工具、输出文件或交付文件；idle 不算后台运行。
- 给 idle 实例发消息沿用原上下文开启下一轮。运行中消息安全入队，在完整工具批次结束后的模型请求前追加，不能插到 assistant/tool 配对中间。
- 单轮 `timeout_seconds` 可省略，省略即不限时；后续发送消息时省略预算，新轮不限时。运行中的引导不重置当前轮预算。
- **预算到期必须接完当前模型响应、执行该响应的整批工具并完整回填真实结果，再阻止下一次模型请求。** 不截断流、不跳工具、不伪造取消结果。此规则仅针对预算，显式stop即时请求取消当前执行。
- `stop_sub_agent`取消当前执行worker，实例监督循环保持存活；流式半截响应不提交，已提交批次的缺失工具结果补写“被主智能体取消”，已完成结果保留。界面批量停止写“被用户取消”。停止后idle、end_reason=stopped并通知主，后续可继续。重复stop不打断首次取消的资源清理。
- stop持久化work_paused，保留未消费pending_work但不自动重启；新消息解除暂停后消费原排队内容。恢复时同样遵守work_paused。取消协程不撤销已发生操作，MCP外置请求不保证远端已停止。
- `terminate_sub_agent` 是不可恢复的吸收态，优先于并发stop。真实进程中断后的未回填工具由恢复器标明结果未知，不能冒充正常预算超时或成功执行。
- 模型请求最多5次尝试，重试等待10秒；零接收或网络断流可重试。重试前仍检查单轮预算；耗尽转 idle 并报告错误。半截流式工具参数不执行。
- idle 使用 asyncio.Event 等待；跨线程只入待处理队列和唤醒事件，消息历史由子事件循环维护。子子提问需要恢复对方时，恢复操作放线程执行，避免阻塞同一事件循环。

### 11.3 唯一持久化与共享注册表

`modules/sub_agent/persistence.py` 的注册表按工作区 data_dir 共享：tasks、instances、running、states 共同属于同一 RuntimeRegistry。查询不能从磁盘重新覆盖运行实例所持的记录；恢复串行化，启动前完成实例／任务句柄和回调登记。

- 新状态：`<工作区 data_dir>/sub_agent_runtime.json`，version=1，原子替换。
- 子上下文：`<工作区 data_dir>/sub_agent_tasks/<task_id>/conversation.json`，保存 messages、round_id、end_reason、working_dir、状态和统计。
- 默认工作目录：`<项目>/.astrion/sub_agent_work/agent_N[_suffix]/`；也可复用已有项目内目录。
- pending_master_messages、pending_work及work_paused持久化。恢复仅消费新格式，不读取旧 sub_agents.json；terminated 永不复活。
- 激活恢复走 `server/context/resources.py`，按明确对话合并，构造 manager 不自动发模型请求；恢复 idle 后等待新消息，已接收且未暂停的待处理消息可以继续消费。切换对话不清理其他对话的运行实例。

执行器职责拆分：task 管生命周期；cancellation 管可恢复即时取消、缺失结果补齐和来源；engine 管模型／工具批次循环；runtime 管预算、排队和 MCP；communication 管问答；model 管 API 流；history 管压缩、媒体、用量和快照；execution 管可信内置工具资源。

### 11.4 最终通知与主任务门闸

普通中间输出只写子活动记录；每轮只把最终回复作为 user 消息发送给主对话。问答是额外的明确通信，子子提问／答复也向主对话汇报。

生产者只入 `MultiAgentState.pending_master_messages`。接收端统一处理：

1. 主任务运行中，在完整工具批次后或下一次模型请求前消费，保持队列顺序。
2. 主任务空闲时，poll 预占主任务门闸，再经 RuntimeService 派发 notice；token 经 InternalDirectives 移交。
3. 主最后一轮无工具调用时，先消费已到达子消息，仍有工作则继续处理；未完成子任务由后续轮询接续。

**Pool 优先**：有子消息就处理，不等全部 running 子实例退出。ask_master 工具本身仍处 running，等待全部退出会互锁。目标模式暂停／阻塞仍遵守目标策略，不因自动子通知解除。

受理失败时恢复消息队首并释放门闸。前 N−1 条通知只能在任务成功受理、执行体认领门闸后持久化；最后一条由正常主输入链持久化。运行中逐条消费若中途写失败，只把尚未写入的尾段回队，不重发已成功前缀。不要从同一个 tool 位置反复插入多条消息而倒置顺序。

后台命令／工作流通知与子协作通知使用独立池。传输字段 `has_running_multi_agent` 现在表示命名子实例 running 或有待消费消息，不代表对话类型；idle 子实例不能延长工作计时。REST 任务事件流是唯一实时通道。

### 11.5 显示和工具结果

输入栏显示“X 个子智能体”，与容量限制同样只排除terminated；点击展开QuickDock并仅滚动其内部栈到子智能体卡片，移动端打开对应悬浮层，不弹批量停止确认框。异步定位受对话session／epoch与请求代数约束。形象的后台子智能体计数只接受running，idle或failed不触发工作状态；后台命令继续沿用自己的状态规则。

协作消息继续使用 `auto_message_type=multi_agent_*`、`metadata.visibility="chat"`、`starts_work=false`，这些是消息协议，不能因删除对话类型而一起删掉。持久化消息与事件使用同一 message_id；显示恢复仍走单一快照／游标链路。

- 主 formatter：`utils/tool_result_formatter/agent_context.py`，在 dispatch.py 注册。
- 子通信 formatter：`modules/sub_agent/toolkit.py::_format_tool_result`。
- 前端主／备用路径共同委派 `toolRenderers.ts` → `subAgentResults.ts`。
- meta 只放名称、参数、权限、目录等；最终正文、问答和失败内容放 content。
- 新增工具还须同步 `core/tool_config.py` 类别、子工具系列／只读清单、动态加载注册及中英文文案。

### 11.6 MCP 与内置权限

四档固定内置权限和原生文件／命令隔离见 `sub_agent_access.md`。MCP 外置插件不套这些目录写沙箱。

每子实例独立 MCPClientManager，复用父服务配置和可见 alias，不共享活动客户端。默认 Playwright profile 加入稳定 actor_id；未显式设置 cwd 的子服务以 working_dir 为 cwd，显式配置优先。详见 `mcp_runtime_scope.md`。

### 11.7 调试和回归

复用 `modules/multi_agent/debug_logger.py::ma_debug()`。关键轮次、pending消息、门闸预占／受理失败、恢复、跨循环回答和 runtime_injected 路径写入运行态日志，禁止终端刷屏。

统一回归：`test/2026-10-22_子智能体统一/README.md`。还需按改动运行固定权限、显示快照、工作区导航、动态加载和 MCP 作用域回归。离线通过不等于真实模型、浏览器和所有原生平台验收。

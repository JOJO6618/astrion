# Rust 子智能体生命周期交接

## 2026-11-27 续接验证

普通 Agent 用同一生产运行时注册和分派具名子智能体，主任务在完整工具批次边界接收子通知。空闲会话经 `running-status` 派发通知，任务终态和 Web bootstrap 汇报后台子状态。隔离端到端测试通过了主任务创建子实例、子实例调用本地 MCP、子轮结果进入同一主对话。子实例内置命令、终端、实际工作区目录权限矩阵与 GUI 尚待逐项验收。

## SQLite 存储接线

Rust 的运行注册表、子上下文、协作队列、统计、结果和活动存入 `<DATA_DIR>/sub_agents.sqlite3`。活动分页继续使用字节边界游标。现有 v1 注册表和任务目录由后台迁移作业读取，按主对话原子导入并保留原始文件。下面记录的是 SQLite 改造前的交接状态。

截至暂停指令，子智能体专属代码已写入当前迁移工作树。生产运行接线和本地模拟模型验收仍待完成。

## 已写入

- `src/sub_agents/registry.rs` 增加运行注册表和子上下文的持久化操作。`mkdir` 改为接收 `impl AsRef<Path>`，在函数内部取得 `&Path`，修正 `stage-check2.log` 所报的 `Sized` 编译错误。
- `src/sub_agents/execution.rs`、`worker.rs`、`communication.rs` 写入具名实例创建、运行、消息排队、恢复、协作问答、停止和终结的实现。工作状态保存到 `sub_agent_runtime.json`，上下文和活动保存在各 `sub_agent_tasks/<task_id>/` 下。模型预算只在下一次调用前检查。显式停止经取消信号处理，缺失工具结果按取消来源回填，终结或进程中断的未知结果有单独文案。
- `src/sub_agents/scope.rs` 写入创建时对父权限上限的校验、固定路径身份校验及原生文本读写能力绑定。执行器当前开放原生文本读写和协作工具。`sandbox_write` 和 `full_access` 档位的工作区外操作仍未经装配和验收，命令、MCP 和媒体也未接入子执行器。
- `src/sub_agents/routes.rs` 增加浏览器 `stop`、`stop_all`、`terminate` 处理器。现有列表合并真实 worker 状态，活动读取沿用已有分页接口。浏览器实际调用尚待验证。
- `src/runtime/sub_agent_execution.rs` 写入主工具分派、运行中通知消费和空闲通知派发。代码准备通过主任务已有的受理门闸串行交付通知，以消息 ID 防止队列确认失败后重复持久化。该模块尚未被共享 Runtime 声明，尚未经编译或运行验证。

## 已有证据

- 已执行 `cargo check --manifest-path backend-rust/Cargo.toml --offline --target-dir backend-rust/target-master --lib`，退出成功，耗时 3.61 秒。当时已纳入注册表、监督器、子 worker 和浏览器停止处理器。检查发生在新建 `src/runtime/sub_agent_execution.rs` 之前，结果仅覆盖当时已声明的库模块。
- 暂停前最后一次源码读取显示，`src/runtime/approval.rs` 已有 `TaskApprovalSettings::child_access()`。主侧报告可信通知的消息 ID 已在 `runtime.rs` 接入。两处尚未随子运行模块验证。

## 待接线与验收

- 共享接线待由主侧恢复后处理。`src/runtime.rs` 需声明 `mod sub_agent_execution`，为 `Runtime` 加入 `sub_agents` 字段及默认初始化。`src/runtime/agent.rs` 需在普通 Agent 入口注册和分派子工具，在安全点激活子实例并消费通知，处理工作流、引导和子通知共存时的 assistant 唯一写入。`src/main.rs` 需创建共享 `SubAgents` 实例、注入 catalog 和固定模型，再将它挂到生产 Runtime。`src/web_status/` 需接入实际子任务运行标记与空闲通知轮询。
- 尚未运行临时目录和本地模拟模型的普通文档任务。待验证从正常入口创建和读写、窄权限拒绝越界、父权限变化后的固定档位、主子与子子问答、运行中入队和恢复、不中断整批工具的预算到期、各停止来源、终结吸收态、重启后的未知结果、活动 API 及快照和事件的消息 ID 一致性。
- 尚未验证 Rust 与 Python 来回接管的子上下文，也未验证生产 GUI。暂停指令后仅写入本交接文档。

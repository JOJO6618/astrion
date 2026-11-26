# Host 进程与终端资源交接

## 2026-11-27 续接验证

启动装配、Gateway 后台列表、浏览器终端和进程详情、监视快照以及退出回收已接入生产入口。命令按任务受理后的有效权限执行，沙箱写入审批复用工具调用指纹与一次性授权。完整工具批次后消费后台完结消息，空闲时由 `running-status` 使用主任务门闸派发。文件监视的开始与结束事件携带同一执行 ID。

隔离测试通过了任务权限与伪造参数拒绝、快照归属隔离，以及真实 macOS Seatbelt 的只读拒写、工作区内写入、敏感文件拒读和持久终端。生产入口本地模拟测试验证了 Bearer 后台列表、浏览器终端鉴权与文件监视快照。`test/2026-11-26_Rust运行资源/process_resources.rs` 另有原生审批及后台进程取消测试，普通沙箱环境默认跳过原生用例。

Web 与 headless 共用路径授权、命令拦截和沙箱状态接口。运行中的权限、执行环境和网络设置先保存为待生效值，在主 Agent 下一轮开始时生效。路径策略及有效模式发生变化时，回收旧权限下创建的终端。原生文件读写与平台进程共用路径策略。

macOS 正常生产入口的隔离测试覆盖了外部授权读写、命令规则阻止执行、direct 人工审批、单次完全访问与后续回到沙箱。同一父对话中的主、子终端可同名运行且输出互相隔离。窄权限子实例能读主工作区并写入自己的固定目录。真实 Seatbelt 拒绝其改写主工作区的测试文件。

Linux 使用已有的管理员安装服务和固定客户端。Windows 沙箱使用专用 WSL2 发行版与固定 `bwrap` 启动参数，direct 前后台命令和持久终端经本机命令解释器执行。Linux 和 Windows 平台模块已在对应 Rust 目标上独立通过类型检查，真实系统执行和安装仍待验证。旧独立终端页的 WebSocket、审批模式下只读试运行后的重试和异常退出后的跨进程回收仍待完成或验收。

以下为暂停前交接记录。

## 已有文件

- [`src/process_runtime/mod.rs`](../src/process_runtime/mod.rs) 保存前台进程、持久终端、后台指令和监视器快照。进程资源按 Host principal、工作区 ID、工作区路径和对话 ID 归属。实现后台列表、详情、取消、等待、通知认领和回滚、对话及工作区回收。`has_pending` 查找同一 owner 下的运行项或未消费终态。
- [`src/process_runtime/policy.rs`](../src/process_runtime/policy.rs) 从可信部署目录读取沙箱路径政策，从 Host 数据目录读取个人命令拦截和终端管理策略。macOS 调用固定的 `/usr/bin/sandbox-exec`，生成 deny-default Seatbelt profile，并区分只读、可写及三档网络。沙箱调用清理继承环境，校验 cwd，并限制对数据与部署目录的文件访问。
- [`src/process_runtime/process.rs`](../src/process_runtime/process.rs) 负责独立进程组、有限输出缓冲、终端输入和启动确认、回收及超时。正常、停止和超时路径均尝试回收分配给本实例的进程组。固定的宿主守护脚本持有后端存活管道，后端退出时尝试清理该进程组，用户命令始终作为参数交给 Seatbelt。
- [`src/process_runtime/tools.rs`](../src/process_runtime/tools.rs) 提供 `run_command`、`terminal_session`、`terminal_input`、`terminal_snapshot` 和 `sleep` 的参数解析及 schema。`run_command` 无 cwd 参数，内部授权字段与完全访问申请会被拒绝。
- [`src/runtime/process_resources.rs`](../src/runtime/process_resources.rs) 提供已认证运行任务的执行入口、资源查询和监视器桥接。快照使用现有 `ReadFile` 能力读取，缓存前重验任务，REST 取回时重验资源归属。浏览器路由使用 `private, no-store`。

## 共享装配和接线

主侧已经落盘 `lib.rs` 的 `pub mod process_runtime`、`runtime.rs` 的 `pub mod process_resources`，以及 `runtime/store.rs` 的 `pub(super) process_resources: crate::process_runtime::ProcessResources` 和默认初始化。`runtime/approval.rs` 已提供 `process_modes(&self) -> Option<(PermissionMode, ExecutionMode)>`。

- 启动时调用 `Runtime::configure_host_processes(&Path) -> Result<(), RuntimeError>`，传 `HostPaths::deploy`。`gateway_router(Runtime)` 放到现有 Gateway 认证和工作区中间件内，提供 Web 与 headless 的 `GET /api/background_commands`。`browser_router(Runtime)` 放到现有浏览器认证及工作区中间件内，提供 `GET /api/background_commands/{id}`、`POST /api/background_commands/{id}/cancel`、`GET /api/terminals` 和 `GET /api/terminals/{name}/output`。`monitor_router(Runtime)` 放到现有浏览器认证内，提供 `GET /api/gui/monitor_snapshot`。监视器由前端提供不透明执行 ID，现有前端请求没有工作区选择参数。
- 正常主 Agent 需把 `Runtime::process_tool_schemas(task_id)` 加入 schema，在同一轮只对已列出的进程工具调用 `Runtime::execute_process_tool(task_id, name, arguments)`。固定审核工具、子智能体和其他独立任务入口不能复用主 Agent 分派。**接线前必做**，将本模块 `live_scope` 的权限判定接到已经落盘的 `TaskApprovalSettings::process_modes()`。当前实现仍按保存的对话 metadata 判定权限，缺失档位时会使用本模块预设值，可能与任务准入有效档位不同。
- 通知用 `take_process_notices(task_id)` 认领先前完成的同对话后台结果，失败后以原 ID 调用 `restore_process_notices(task_id, ids)`。派发须经过现有主任务闸门。`Runtime::conversation_has_background(&Store, workspace, conversation)` 供现有状态与生命周期聚合。尚未实现空闲会话自动派发的接线。还需核对手动取消的通知语义，Python 手动取消会标记已通知，当前实现只请求并等待进程终止。
- 停对话时使用 `Runtime::stop_conversation_processes(workspace, conversation)` 停止后台项、全量标记终态。持有 Store 锁时，删除对话或解绑工作区使用 `Runtime::close_process_scope(&mut Store, workspace, Option<conversation>)`，回收该范围进程及快照。正常退出调用 `Runtime::shutdown_process_resources()`。共享层尚待接线，尤其要检查工作树切换、权限变更与旧 shell 的回收时点。
- 监视器工具前后可使用 `Runtime::process_monitor_id()` 生成独立展示 ID，再调用 `capture_process_monitor(task_id, id, MonitorStage, tool, &args)`。`tool_start` 与 `update_action` 需使用一致的展示 ID，并保留模型调用 ID 的关联，才能让前端读取对应快照。现有 `static/terminal.html` 还在用旧 WebSocket。此阶段的 REST 读接口对应 `TerminalPanel.vue`，单独终端页仍待迁移或确认是否保留。

## 已有证据与待验

1. `backend-rust/target-master/resume-check.log` 是修改前的共用检查日志，列出了 `tools_category` 未导出、`process.rs` 同一 match arm 的借用类型不一致以及 `ProcessResources::has_pending` 缺失。三处已按日志修正，修正之后未重跑共用构建。`runtime/process_resources.rs` 中 `#[cfg(test)]` 引用的 `test/2026-11-26_Rust运行资源/process_resources.rs` 尚未创建，执行测试构建前需补齐该文件或改用内联测试。
2. 当前受限执行环境中，固定 `/usr/bin/sandbox-exec` 运行无害 `printf` 探测返回退出码 `71`，stderr 为 `sandbox-exec: sandbox_apply: Operation not permitted`。这验证了此环境阻止嵌套 Seatbelt。没有使用 direct 命令代替平台正例，也没有在此环境验证真实进程的启动、输出、取消或恢复。
3. 待用授权的原生 macOS 及合成临时工作区验证同一命令的前后台输出、终端 open/input/snapshot/reset/close、进程组取消后 PID 消失、超时与正常关停、后端异常退出后的存活管道回收。用独立工作区与不同对话核对详情、取消、输出与通知无法串读。核对完成通知的认领、失败重投、快速完成、手动取消和全量清理。
4. 待用正常 Gateway 和浏览器入口验证有效 Host Bearer 只能读取后台列表，浏览器会话才可读详情、终端和监视器，取消需要现有 CSRF。通过写文件与读文件探测验证只读拒写、写权限限于工作区和现有批准路径、工作区外读拒绝、敏感路径拒绝、管理员关闭和个人命令拦截。重启后从空内存开始，后台记录和监视器缓存均为进程内状态，现阶段没有跨重启恢复的登记。后端异常退出后清理的实现需验证，另起会话或脱离进程组的后代不属于当前已验证的回收范围。
5. 尚未启用 Linux 已安装沙箱助手、Windows WSL2、direct、单次完全访问、审批提权重试、外部工作树额外授权和子智能体进程资源。配置中的严格技能检查目前只是保守守门，尚未验证 Python `skill_read::<skill-id>` 与启用技能清单的兼容。以上均不属于已经验收的生产行为。

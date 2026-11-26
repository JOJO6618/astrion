# 提问与计划审批交接

## 2026-11-27 续接验证

正式 Gateway 已挂载提问和计划审批四个路由，Agent 工具循环会批量发布提问并按原工具 ID 回填，计划批准后持久化执行模式并更新任务权限。Web bootstrap 从同一任务游标恢复未答问题和待批计划。隔离 Web 与 Gateway 端到端测试覆盖跨工作区筛选、同批两问、局部作答、重开页面、计划批准与恢复个性化默认权限。用例在 `test/2026-11-26_Rust提问计划审批/runtime_roundtrip.test.mjs`。

截至 2026-11-26 暂停时，本轮已写入交互存储、工具参数和 HTTP 路由，并小范围调整 `runtime/approval.rs`。交互模块仍待接入 Runtime 和 Gateway。本轮尚无测试文件、编译结果或运行验收结果。`test/2026-11-26_Rust提问计划审批/` 已建目录，尚未写入用例。

## 已写入工作树

- `backend-rust/src/interactions/mod.rs` 声明 `http`、`store`、`tools`，导出 `router`、`InteractionStore`、`ASK_USER`、`SUBMIT_PLAN`、`is_tool`，并预留独立存储测试路径。
- `backend-rust/src/interactions/store.rs` 将问题保存在批次中，将一份计划保存为单条交互。记录使用现有 `TaskScope`，索引按交互 ID 查询，列表和裁决按完整 `ApprovalPrincipal` 校验。包含首次合法裁决锁定、终态一小时保留、每用户名每类操作每分钟 120 次的限流方法、到期和按任务清理、原事件载荷构造。每批使用一个 `tokio::sync::Notify`，整批终结时唤醒等待方。
- `backend-rust/src/interactions/tools.rs` 提供两种工具定义、提问校验、至多八个选项和重复 ID 归一化、选择／补充／文本／跳过四种答案、模型侧 JSON 结果，以及计划摘要与正文的字符上限。
- `backend-rust/src/interactions/http.rs` 定义 `/api/user-questions/pending`、`/api/user-questions/{id}/answer`、`/api/plan-approvals/pending`、`/api/plan-approvals/{id}/answer`。从认证中间件注入的 `Extension<ApprovalPrincipal>` 取可信身份，调用三个待实现的 Runtime 方法。计划裁决放在阻塞线程，因为批准需要持久化。
- `backend-rust/src/runtime/approval.rs` 增加 `WorkMode::Ask`，原 `Interactive` 继续表示 `execute`，保留原有工具审批策略及测试使用的变体。将 `WorkMode`、`TaskApprovalSettings.work_mode`、`TaskApprovalSettings.valid_modes`、`task_scope` 和 `is_plan_file` 开放至 Runtime 内部。
- 本智能体尚未编辑 `lib.rs`、`runtime.rs`、`runtime/store.rs`、`runtime/agent.rs`、`gateway.rs`、`host_web.rs` 或 Web bootstrap。子智能体「代理运行生命周期」已通知其也负责修改 `runtime/agent.rs` 的 schema、验证和执行分派附近，恢复后需协调增量接线。

## 已核对的仓库契约

- Python `server/chat/approval.py` 提供四个现有 REST 路由，Web 与 headless 共用。Rust `backend-rust/src/main.rs` 将同一个 `gateway::router` 合入运行入口。`gateway.rs` 在当前工具审批路由层进行 Web 会话与 CSRF 或本机 Bearer 鉴权、解析工作区，并注入 `ApprovalPrincipal`。Rust 还需覆盖遗漏对话参数时的行为。当前 HTTP 草稿与 Rust 工具审批一样会列出该工作区内全部 pending，Python 则先尝试使用当前终端所选对话，找不到当前对话才列全部。现有 Web 提问和计划列表请求均明确提交 `conversation_id`。
- Python `server/chat_flow_tool_loop.py` 一次发布同一模型轮次的全部有效问题，整批回答、超时或停止后按原 `tool_call_id` 回填。事件名称为 `update_action`、`user_questions_required`、`user_questions_resolved`、`plan_approval_required`、`plan_approval_resolved`。`modules/user_question_manager.py` 与 `modules/plan_approval_manager.py` 定义序列化字段、终态及文本上限。Python 接口对部分非字符串或非布尔 JSON 做隐式转换，当前 HTTP 草稿要求正常客户端使用的字符串、布尔值或 null，适用范围仍需在对照测试中确认。
- `docs/agents_details/work_mode.md` 与 `core/main_terminal_parts/tools_policy.py::switch_work_mode` 表明，`ask` 模式禁用提问工具，`submit_plan` 可在各模式声明但非 `plan` 调用要返回引导。计划批准自动切换 `execute`，恢复 `pre_plan_permission_mode`（缺失时取个性化默认）及可恢复的 `pre_plan_execution_mode`，只读时保持沙箱。拒绝保持计划模式。提交需要任务执行根下 `.astrion/plan/` 的 Markdown 文件，批准结果返回用户看到的计划快照。
- `runtime/approval.rs::task_scope` 从已受理任务读取身份、工作区、对话、执行根和模式，拒绝已取消或非运行状态。`runtime.rs::cancel_task` 现有 oneshot 可停止工作协程，`store.rs::finish` 是任务终态的统一收尾位置。交互应持有原任务门闸，REST 仅更新原任务。
- Web `static/src/app/methods/conversation/bootstrap.ts` 从 `display.state` 恢复两个 pending 列表，Rust `web_status/bootstrap.rs` 当前硬编码为空且游标直接定位到最新事件。仅挂路由和发送新事件不足以恢复正在等待计划批准的重载页，需要把当前 pending 一起投影到同一游标的 bootstrap。现有路由初始化会补拉用户提问，源码未见计划审批的同类补拉。

## 待实现和接线签名

1. `lib.rs` 声明 `pub mod interactions;`，`runtime.rs` 声明 `mod interactions;`。`runtime/store.rs::Store` 加入 `pub interactions: crate::interactions::InteractionStore`，初始化 `InteractionStore::default()`。
2. 在 `runtime/interactions.rs` 实现 HTTP 草稿调用的三项方法。签名为 `pub(crate) fn pending_interactions(&self, principal: &ApprovalPrincipal, kind: crate::interactions::Kind, conversation_id: Option<&str>) -> Result<Vec<Value>, crate::interactions::Error>`、`pub(crate) fn answer_user_question(&self, principal: &ApprovalPrincipal, id: &str, answer: &crate::interactions::QuestionAnswer) -> Result<Value, crate::interactions::Error>` 和 `pub(crate) fn answer_plan_approval(&self, principal: &ApprovalPrincipal, id: &str, approved: bool, comment: &str) -> Result<Value, crate::interactions::Error>`。回答时持有 Store 锁，调用 `limit_answer`、`target`、`task_scope`，复验活动任务和已保存的 scope。重复合法裁决读取既有终态。批准计划须在同一锁内先成功持久化模式变更，再标记批准、发出 `plan_approval_resolved` 和唤醒任务。需同步更新此任务的 `TaskApprovalSettings`，避免工具循环继续用计划模式权限。路径校验复用 `is_plan_file`，读取复用 `tools::read_file::ReadFile` 的受限能力，实际读盘后重新验证任务 scope 再发布请求。
3. 在同文件实现 `pub(super) fn expire_task(store: &mut Store, task_id: &str)`，将 `store.interactions.expire_task(task_id)` 返回的终态事件写入原任务。`runtime.rs::cancel_task` 在现有锁内呼叫 `interactions::expire_task(&mut store, task_id)`，`runtime/store.rs::Store::finish` 在终态事件前呼叫 `super::interactions::expire_task(self, id)`。等待方法需先读取终态检查，再 `Notify::notified()`，并将超时与人工裁决在同一锁下线性化。如果等待协程被取消或中断，待办在任务取消和收尾处过期。
4. 在同文件为 agent 接线实现 `pub(super) fn interaction_tools(&self, task_id: &str) -> Result<Vec<Value>, String>`、`pub(super) async fn wait_for_questions<'a>(&self, task_id: &str, calls: impl IntoIterator<Item = (&'a str, &'a Value)>) -> Result<HashMap<String, Value>, String>`、`pub(super) async fn submit_plan(&self, task_id: &str, tool_call_id: &str, arguments: &Value) -> Result<Value, String>`。`wait_for_questions` 收集调用后只能带拥有所有权的数据跨 await。继续保留目标模式现有拒绝分支，避免未受理任务、Goal、询问模式或其它不允许交互的入口挂起。同一批次应在发布等待状态前发出问题的 `tool_start`，逐个回填时避免再次发送相同开始事件。下一轮重新计算交互 schema，令模式切换即时生效。
5. Gateway 在现有工具审批路由位置 `.merge(astrion_headless::interactions::router(app.runtime.clone()))`，受同一认证和工作区中间件保护。Web Bootstrap 要把带相同游标的交互快照提供给 `display.state.pending_user_questions` 和 `display.state.pending_plan_approvals`。

## 待验

- 独立存储用例的声明路径已预留 `test/2026-11-26_Rust提问计划审批/interactions_store.rs`，该文件还需创建。单元用例覆盖同批顺序与部分作答、选项和补充、跳过、非法选项、跨用户／工作区访问、重复和并发回答、超时和取消只过期待决记录，以及答复先于 await 时不丢唤醒。
- Runtime 与 HTTP 用例验证同一任务挂起和恢复、原 tool call ID 回填与历史顺序、跨工作区和对话过滤、两种入口的认证，以及恢复页面在当前游标显示 pending。计划应覆盖非计划调用、越界和链接路径、空文件和截断、拒绝维持计划模式、批准恢复原权限与执行环境、取消与批准竞态。
- 字符串、JSON 状态码和超时结果仍需逐项对照 Python，尤其是缺失 `approved` 的拒绝默认值、缺失对话参数、UTF-8 非法字节替换以及任务级超时配置的传递。Rust 目前的工具审批默认等待 3600 秒，Python 可从受理上下文覆盖。本轮没有运行测试或编译，也没有观察到生产入口运行这些路由。

# Responses 接线二轮交接

## 会话版本恢复

工作树 `migration/rust-backend` 已落盘开关、初始检查点、任务终态检查点、工作区文件浅备份、原位和复制还原，以及恢复事务。变更位于 `backend-rust/src/versioning/`、`backend-rust/src/runtime/versioning.rs` 和 `test/2026-11-26_Rust版本恢复/`。共享运行入口由主侧接线。当前状态为开发暂停，版本写路由尚待验收后挂载。

- [`runtime/versioning.rs`](../src/runtime/versioning.rs) 定义浏览器 POST 路由、运行时互斥、受权文件写前跟踪、终态记录和待恢复事务处理。读状态复用原来的 `Records`。
- [`mutations.rs`](../src/versioning/mutations.rs) 实现启用时生成 `seq=0`、禁用后保留历史、终态检查点去重、原位还原裁剪后续记录、复制还原保留源对话，并读取 Python 的 Windows／POSIX 快照路径。缺失快照时按旧 `message_index` 回退，恢复时保留当前冻结系统提示和执行根元数据。
- [`shallow_write.rs`](../src/versioning/shallow_write.rs) 读取旧浅备份日志和二进制备份，支持写回、重建、删除以及相对路径和符号链接检查。
- [`transaction.rs`](../src/versioning/transaction.rs) 先持久化恢复日志，再安装并校验记录和文件，以会话文档持久化为提交边界。恢复时根据会话指纹前滚或回滚。涉及工作区写入时登记工作区级待恢复标记，让同一工作区其他会话的任务入场也经过恢复。
- [`deep.rs`](../src/versioning/deep.rs) 从旧隐藏 Git 对象库读取树和 blob，使用独立索引准备全量检查点和恢复，并把工作区文件改动纳入同一个事务。真实项目的 Git 索引与 refs 由独立路径保留。当前实现对符号链接、特殊 Git 条目和外部对象库返回明确错误。全量恢复的边界仍待实测。

### 验证证据

- 已执行 Rust 格式化。已有测试编译最先遇到共享 `knowledge/migration/files.rs` 引用缺失夹具，后续重跑已越过该处。暂停后只读查看的最后日志停在 `runtime/process_resources.rs:406`，缺 `test/2026-11-26_Rust运行资源/process_resources.rs`，尚未执行版本测试。 [版本测试日志](../target-master/versioning-contract.log)
- 更早的离线构建卡在 `zopfli`。主侧随后报告已解析 `zopfli 0.8.4` 和 `zip 2.6.1`，最新日志已进入 `astrion-headless` 测试编译阶段。`deep.rs` 的 Git 错误转换由主侧协调在 `GitError` 定义处统一处理，仍需整体验证确认。
- 已写 7 个隔离测试，覆盖开关与终态检查点、活动任务互斥、旧浅备份二进制还原、旧全量会话复制、真实持久化写失败回滚、事务中断后前滚和回滚、路径与损坏备份拒绝，以及临时 Git 工作区的旧全量恢复。测试结果仍待取得。 [测试源码](../../test/2026-11-26_Rust版本恢复/versioning_contract.rs)

### 共享接线

- `runtime::versioning::mutation_router(runtime: Runtime) -> Router` 提供 `POST /api/conversations/{id}/versioning` 和 `POST /api/conversations/{id}/versioning/restore`。通过隔离验收后在 Host 浏览器路由里合并，复用既有浏览器会话、CSRF 和 `RegisteredWorkspace` 作用域。
- `runtime::versioning::load_status(&Runtime, &RegisteredWorkspace, id: &str) -> Result<Value, RuntimeError>` 返回现有 GET 的 `data`。会话加载调用前先释放已有 Store 锁。
- `runtime::versioning::recover_pending(store: &Store, workspace_id: &str, id: &str) -> Result<(), RuntimeError>` 在任务入场、持 Store 锁且读会话前调用。待恢复操作报错时拒绝入场。
- `runtime::versioning::track_edit(store: &Store, task_id: &str, raw_path: &str) -> Result<(), RuntimeError>` 在工具授权已消费、受权文件写入前调用。原始路径由已验证的写工具参数获得。备份失败时返回工具错误。
- `runtime::versioning::record_finished(store: &Store, task_id: &str) -> Result<(), RuntimeError>` 在 `Store::finish` 已成功持久化最终消息、任务已进入终态、仍持 Store 锁时调用。由调用方记录独立的检查点失败。
- `runtime::versioning::set_enabled_on_create(store: &Store, workspace_id: &str, id: &str) -> Result<(), RuntimeError>` 在启用默认备份的设置分支，完成新会话持久化后调用。当前函数启用会话范围。默认设置和新会话入口的衔接待主侧完成。

### 恢复开发后的待验项

1. 共享测试夹具齐备后，先跑 `cargo test --offline --target-dir backend-rust/target-master --manifest-path backend-rust/Cargo.toml --lib runtime::versioning`，根据结果核对版本模块和 Git 错误转换。
2. 在隔离工作区验证旧全量树的覆盖与复制、忽略文件保留、实际 Git 索引保留、失败后重试、工作区作用域重绑、同时启动任务，以及普通工作区 Git 中符号链接和文件／目录路径冲突的明确结果。
3. 确认旧浅备份保留历史的语义、恢复快照顺序、异常写入恢复与新启用配置一致，再验证 Host 登录、CSRF、Bearer 拒绝和实际任务工具钩子。现有 GET `supports_workspace_tracking` 仍为 `false`，需要结合全量验收确定何时公开该能力。

## 此前确认的 Codex OAuth 交付

Codex OAuth 请求构建复用普通 API Key Responses 的 SSE 解析。主侧已收取接线签名 `ProviderConfig::codex_transport(account_id)` 和 `model::codex::complete(&transport, model, messages, tools, &options, on_text)`。授权指纹约束加密推理回放范围。已完成的 `cargo test --lib model::` 共 **6 项通过**，含 Codex loopback 2 项和普通 API Key 4 项。Python OAuth 请求样本逐字段对照通过，另有 Python Fast 8 项和推理隔离 7 项通过。用过的凭证为合成数据，真实订阅接受 `priority` 的情况未冒烟。 [模型回归日志](../target-master/codex-model-http.log) · [Codex 协议测试](../../test/2026-11-25_RustCodexResponses/codex_http.rs)

# Chat→Agent 交接迁移交接记录

## SQLite 存储接线

Rust Chat 的会话树和列表摘要保存在账号 Chat 根下的 `conversations/conversations.sqlite3`。未导入的旧文件显示为不兼容，详情返回 `conversation_migration_required`。迁移标记使旧文件在数据库记录删除后保持隐藏。媒体索引和媒体内容仍使用已有路径，引用查询包含 SQLite 中的整棵树以及尚待迁移的旧资料。

真实 Host HTTP 验收覆盖旧树经明确迁移后的多进程续聊、回执重放、修订冲突、草稿和复制重启，以及 Chat→Agent 的倒计时、当前分支媒体、Agent 模型执行、重启恢复、上游错误重试和删除取消。

## SQLite 改造前的交接记录

### 暂停时状态

本轮实现已落在 migration/rust-backend 工作树，开发按要求暂停。交接实现涉及 `backend-rust/src/chat_mode/handoff.rs`、`backend-rust/src/chat_mode/handoff/service.rs`、`backend-rust/src/chat_mode/handoff/materials.rs`，以及主侧明确分配的 `backend-rust/src/runtime/chat_handoff.rs`。独立 HTTP 测试在 `test/2026-11-26_RustChat交接/handoff_http.py`。本轮未提交。共享入口由主侧接入。

## 已实现

- GET / POST `/api/chat/conversations/{id}/handoff`，支持按 handoff ID 或无歧义的旧 message ID 查找，支持 observe、pause、select、decline、accept。公开字段使用白名单。
- 树写入时同步 `<suggest_agent/>` 记录，保留旧 ID 和历史累计数。完成的当前分支可获得 30 秒期限，停止时保留完整建议供手动处理。展示身份变更、暂停和释放会取消计时。心跳在内存记录，只有期限后的有效心跳才可触发一次续聊。普通发送在同一树事务内处理原分支建议和旧分支失效。
- 接受时冻结当下分支，保存账号 Chat 根内的 `handoffs/<id>/snapshot.json`。后台调用原 Chat 模型生成摘要，再以原子目录发布方式写入默认工作区的 `.astrion/handoff/<id>/summary.md`、`conversation.md` 和当前分支的媒体。材料复用会核对来源会话、叶节点、清单和媒体摘要。
- Runtime 在同一 Store 锁内复核账号源 Chat、活动分支、交接状态、操作 ID 和目标工作区绑定，再走实际 Agent 受理与启动。目标 Agent 文档与首次 user 消息、来源标记同次持久化。重试复用确定性 Agent ID，已有正确关联及 user 消息时恢复原结果，避免重复启动。请求中的材料路径使用工作区相对路径。目标工作区来自可信 Host 配置，忽略客户端传入的路径和工作区标识。
- 删除入口使用交接取消信号。已向主侧提供 `let _handoff_gate = mode.cancel_handoffs(&id)?;` 接法，让删除在同一交接锁下完成。主侧答复会接入并统一处理共享声明。

## 已有证据

- 主侧报告 `backend-rust/target-master/debug/astrion-headless` 重建成功。该二进制可启动真实 Host 服务、完成登录并接收 Chat HTTP 请求。
- 本轮 HTTP 独立脚本已启动隔离进程和本地 SSE 服务。首个 Chat 请求在模型解析阶段返回 503，错误是 `未知模型 key: chat-local`，因而未触发倒计时或 Agent 启动。原因经代码核对为夹具写入 `DATA_DIR/config/custom_models.json`，但复用的 `fixture_environment()` 已显式指定另一个 `DEPLOY_CONFIG_DIR`，并在那里初始化了空模型目录。夹具需要向 `Path(env['DEPLOY_CONFIG_DIR']) / 'custom_models.json'` 写入两条本地模型配置。最近一次日志位于 `backend-rust/target-master/chat-handoff-http.log`。
- 上一轮非固定模型预算的 Gateway Node 契约另有 3/3 通过，覆盖两种模型协议、已保存会话、工具续轮和部署默认值。该结果不覆盖本轮交接。

## 恢复后待验

1. 修正模型夹具路径，确认隔离进程的默认交接工作区也落在夹具根内。`fixture_environment()` 已提供隔离的 Home，Host 默认路径解析由主侧负责。验证脚本当前预期 `ASTRION_HANDOFF_DEFAULT_WORKSPACE` 指向夹具目录，需与最终 Host 配置一致。
2. 使用主侧当前构建运行 `ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless python3.11 -B 'test/2026-11-26_RustChat交接/handoff_http.py'`。脚本用测试文件调整期限来验证期限后的心跳，并覆盖刷新与旧展示释放、重复操作、旧分支拒绝、仅复制当前分支媒体、实际 Agent 的 SSE 请求及其输出落盘、重启后收据恢复、上游 429 后重试、删除时取消摘要。上述断言尚未跑通。
3. 核对最终共享接线，包括删除期间保留 guard、树开始／完成／编辑／恢复钩子，以及 bootstrap 与任务轮询的公开投影。当前 HTTP 夹具还应验证目录注册后原工作区默认值未改变和跨工作区 Agent 路由访问边界。
4. 摘要目前用共享 `ModelConfig` 完成无工具请求，并传递上游错误。Python 交接摘要的独立用量账本、轮转诊断日志，以及源 Chat 的动态请求头设置，还需逐项核对。当前可确认的媒体迁移路径是导出分支媒体文件，摘要上游接收的是整理后的文本上下文。

# 会话传输迁移交接

Rust Host 已接通 ZIP 导入、导出和同机工作区迁移。归档验证、资源恢复、SQLite 发布、操作回执及标签重定位由正常浏览器 API 处理。真实隔离 HTTP 回归覆盖 Agent、Chat、Python 读回、源备份失败恢复和重启重试。文件的原生另存使用现有平台保存接口，实际系统对话框留待桌面验收。

## 前一阶段的交接记录

- `backend-rust/src/conversation_transfer/mod.rs` 定义共享限制、摘要和媒体字段读取规则。已声明 `restore` 子模块，文件尚未创建。
- `backend-rust/src/conversation_transfer/document.rs` 校验原始 JSON 和 Chat 树，构造迁移通知与目标元数据，保留原消息和未知字段，并校验目标模型及历史前缀。Chat 分支追加通知到原 `latest_leaf_id`，清理命令回执和交接运行资格并分配新交接 ID。
- `backend-rust/src/conversation_transfer/archive.rs` 实现 version=1 ZIP 读写、大小限制、SHA-256、媒体清单覆盖和原始中央目录检查。ZIP 库内部按名称去重，因此中央目录检查在 ZIP 库读取前执行。重复名称、加密和符号链接等拒绝分支尚无运行证据。
- `backend-rust/src/conversation_transfer/assets.rs` 从可信工作区或账号目录收集媒体与材料，借用目录句柄和 no-follow 文件读取。已写同媒体库迁移的媒体条目和 Blob 存在性检查。Chat 外部交接材料通过调用者提供的已注册工作区解析。尚需核对再次导出时完整覆盖别名，尤其是共用媒体库的迁移、媒体别名及目录别名。
- `test/2026-11-26_Rust会话传输/` 已创建，尚无夹具或回归代码。

## 源码依据与共享接线

- `docs/conversation_transfer.md` 是业务契约。原始数据和 HTTP 对照位于 `modules/conversation_transfer/{archive,assets,service}.py`、`server/conversation_transfer.py` 和 `server/conversation_transfer_support.py`。工作树已有共享 `Cargo.toml` 的 ZIP 依赖声明和 Cargo.lock 的 ZIP 条目，本子任务未修改共享清单或入口。
- `backend-rust/src/runtime/store.rs` 的 Store 是 Rust Agent 与 Chat 使用的写者门闸。`Store::persist` 允许索引写入失败时留下修复标记，传输需要严格事务发布。应复用 `index_row`，需要调整它的可见性或提供独立共享的严谨写入入口。Chat 同样需对齐现有索引行，验证原位读取、列表和搜索可继续消费导入文档。
- `backend-rust/src/navigation/store.rs` 的 `Navigation::relocated` 会保留现有标签 key。导航 snapshot/command 的锁顺序为导航锁到 Runtime Store，迁移不得在持有 Runtime Store 时调用独立加锁的 `relocated`。需要由共享导航层提供锁内重定位并协调源退役、失败回滚与旧提交权撤销。已向主侧报告，最后一次共享接线搜索被中断，当前应以实际文件核对为准。
- `backend-rust/src/gateway.rs` 的 `mutation_workspace` 可用于源工作区解析，当目标工作区与源不同，目标应单独经已注册目录解析并验证绑定。`SubAgents::has_running` 会自行获取 Runtime Store，不能在持有该 Store 锁时直接调用。后台进程和 Chat handoff 活动也需要相同门闸下的状态检查。源退休后还要清理普通对话选择和源缓存。
- `static/src/utils/downloadFile.ts` 明确要求桌面端对同一下载 URL 发 POST 并读取 `data.saved`。网页使用认证 GET。已向宿主交互子智能体询问原生保存接口，等待超时。待对接其公开接口，传入按 token 和授权 scope 打开的文件及校验过的默认文件名。

## 待实现与隔离验收

1. 创建 `restore.rs`、`runtime/transfer.rs` 和专属回归。实现独立资源命名空间、媒体与消息映射、严格索引发布、操作回执、源备份与退役。目标发布和回执先持久化，随后退役源。写失败时回滚事务拥有的目标状态。中断重试须比对源内容、目标历史前缀及 operation 绑定。
2. 实现并在 Host browser session 与 CSRF 保护下接入 `POST /api/conversations/{id}/export`、`GET|POST /api/conversation-transfer/download/{token}`、`POST /api/conversations/import`、`POST /api/conversations/{id}/migrate`。工作区列表路由已存在。下载严格限制随机 token 和请求 scope，GET `Cache-Control: no-store`，POST 保留 native 提示、保存及取消语义。导出清理仅处理超期且严格匹配的普通文件。
3. 仅使用临时目录验证 Python 包与 Rust 往返、Agent 跨工作区恢复、Chat 原树和交接状态、旧媒体与压缩/交接材料及再次导出、无授权工作区和忙会话拒绝、操作 ID 冲突、目标发布失败与源退役失败、进程中断重试及桌面稳定标签。增加 ZIP 拒绝用例，覆盖重复中央目录名称、资源缺失、大小越限、哈希不匹配、额外条目、路径穿越、加密和符号链接。主侧控制共享构建及实际原生对话框验收。

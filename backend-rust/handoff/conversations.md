# 普通 Agent 会话生命周期交接

本轮暂停在组装版 HTTP 验收前。三个入口、Runtime 选择状态、桌面删除快照和独立回归夹具均已落盘。主侧负责共享构建与路由装配。

## 已落盘

- `backend-rust/src/web_conversations.rs` 与 `backend-rust/src/web_conversations/lifecycle.rs` 注册 `GET /api/conversations/current`、`PUT /api/conversations/{id}/load`、`DELETE /api/conversations/{id}`。复用浏览器认证、CSRF 和可信工作区解析。同一路径上的原 GET 详情继续走原工作区中间件。
- `backend-rust/src/runtime/conversation_lifecycle.rs` 在 Runtime Store 锁内校验当前工作区绑定并读取普通 Agent 原始会话。在显式 `workspace_id` 查询参数且工作区空闲时更新工作区级选择。带 `conversation_id` 的 current 直接读取指定会话。运行中 load 仅返回目标会话资料，包括 `safe_navigation` 与 `reasoning_effort`。旧记录的 `deep` 映射为 `thinking`，无会话级模型时尝试从最近的助手消息恢复模型。版本状态借用 `runtime::versioning::load_status`。
- DELETE 在同一 Store 锁内校验并删除普通会话文件和索引记录，清理当前选择及搜索缓存。删除目标正在运行或正在停止时返回 409，避免任务写回后复活。仍可删除运行中工作区的其他会话。先写派生索引，再删除权威会话文件。正常 unlink 失败时尝试恢复索引项。确认文件删除后才向导航层发出删除命令。
- `backend-rust/src/navigation.rs` 增加 `deleted_snapshot`，复用原导航命令与桌面投影，移除目标工作区的停靠和独立窗口标签，并在 DELETE 响应的 `desktop_navigation` 中返回同一导航存储的快照。若导航写入失败，DELETE 仍确认文件已删，并返回 `desktop_navigation_error`，供既有轮询同步。
- 主侧已经在 `backend-rust/src/runtime.rs` 注册生命周期模块，并在 `backend-rust/src/host_web.rs` 用同一个 `Navigation` 实例挂载浏览器会话入口。版本读取由 Responses 接线模块提供。

## 已核对的依据

- Python `server/conversation.py` 的 load 用显式 `workspace_id` 查询参数切换到工作区级 terminal。没有该参数时，`server/context/decorators.py` 依据路径 ID 绑定对话级 terminal。`current` 无 `conversation_id` 时读取工作区级状态。`core/web_terminal.py` 明确说明工作区级实例启动不自动选择最近的会话。
- Python load 在工作区存在 `pending`、`running`、`cancel_requested` 主任务时调用 `_build_safe_load_result`，只读磁盘视图。`utils/conversation_manager/crud_mixin.py` 的读取逻辑为旧记录补齐运行模式，并在缺少模型时回看助手消息。Python DELETE 删除普通文件和索引后调用 `navigation.deleted`。
- 桌面与浏览器共用 `static/src/app/methods/conversation/action.ts` 的删除请求。桌面收到 `desktop_navigation` 时直接应用并转发快照。现行标签切换由 `static/src/app/methods/conversation/bootstrap.ts` 请求 `/bootstrap`，current 和 load 是保留的 HTTP 入口。
- 新 HTTP 夹具通过 `node --check`。已对照真代码修正两处夹具预期，bootstrap 的运行字段在 `data.running` 下，query 与 header 指向不同工作区时 Gateway 返回 400。第二处调整后未重新执行脚本。
- 第一次使用共享二进制试跑时，新的生命周期测试和原普通会话、版本测试都在浏览器服务器启动前失败，进程输出 `Host home directory is unavailable`。同批 headless 版本列表测试通过。主侧已调整 handoff 装配，普通 Web 启动不再要求 HOME。截至交接尚无用新二进制的生命周期结果。一次完整 Flask `test_client` 对照命令超时，当前证据来自 Python 源代码核对。

## 待验

用主侧最新的组装二进制，在工作树根执行：

```sh
ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless node --test \
  'test/2026-11-24_Rust后端/conversation_lifecycle_e2e.test.mjs' \
  'test/2026-11-24_Rust后端/web_conversations_e2e.test.mjs' \
  'test/2026-11-24_Rust后端/versioning_e2e.test.mjs'
ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless python3 -B \
  'test/2026-11-24_Rust后端/navigation_assembled.py'
```

新夹具覆盖 cookie 与 CSRF、空工作区、目标工作区和相同 ID 隔离、显式与隐式 load、旧模型和版本字段、运行中导航仍写入原会话、拒绝删除运行中的目标、索引读失败可重试、删除后列表搜索同步、停靠及独立窗口标签删除、重启后导航与删除结果。夹具中的预期仍待首轮组装验证，特别是旧记录启动准入、版本记录归属与已有导航快照字段。GUI 未启动，桌面消费契约核对来自前端源代码和待运行的 HTTP 快照断言。

若还需执行 Flask 对照，本机 `/opt/homebrew/bin/python3.11` 可导入 Flask 和 OpenAI。首轮采用完整 `server.app`、`initialize_host` 和浏览器 test client，工具调用超时。可参考 `test/2026-11-25_Rust对话消息/http_contract.py` 的生产视图＋受控依赖方式，避免完整应用启动依赖。

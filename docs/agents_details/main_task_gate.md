# 对话级主任务门闸与单写者不变量（完整）

> 本文由 `AGENTS.md` §12 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。
> **例外**：§12.2 / §12.3 原样搬运了压缩前的错误 API（幻觉函数名、错误存储模型、错误 token 移交通道），已于 2026-10-08 按源码校正（已标注）。除这两处外其余内容仍为逐字下沉。

---

## 12) 对话级主任务门闸与单写者不变量（2026-08-12）

> 事故背景：一个对话并发运行了两个主聊天任务（socketio 入口的用户任务 + 完成通知轮询器派发的通知任务），交叉写入共享 `conversation_history`，产生 `assistant→assistant→tool→tool` 乱序段，最终 API 400 `tool_call_id is not found`、通知永久丢失。本节机制即为修复该事故引入。

### 12.1 单写者不变量（核心约束）

**一个 WebTerminal（≈ 一个打开的对话）同一时刻只允许存在一个主聊天任务。** 主任务包括：用户消息任务、后台完成通知派发任务、多智能体 idle 派发任务等一切会向 `conversation_history` 追加消息并请求模型的执行体。宁可拒绝/推迟新任务，也绝不并发写入。

### 12.2 门闸本体（2026-10-08 按源码校正）

实现 `server/main_task_gate.py`。状态载体是**进程内全局锁 + terminal 实例属性**，不是以 `terminal_id` 为 key 的中央字典：模块级 `_LOCK = threading.Lock()` 保护终端实例属性 `_GATE_ATTR = "_main_task_gate_token"`（token 直接挂在 `WebTerminal` 实例上）。

四个真实 API（无类，均为模块顶层函数）：

| 函数 | 语义 |
|------|------|
| `try_acquire_main_task_gate(terminal) -> Optional[str]` | 非阻塞获取。空闲则生成 `uuid4().hex` 写入并返回；已被占用返回 `None`。terminal 为 None 返回 `None`。 |
| `acquire_adopted_main_task_gate(terminal, token) -> Optional[str]` | 认领派发方预占的 token。**传入 token 与实例当前 token 相等**则认领成功；被其他 token 占用返回 `None`；空闲则生成新 token（退化为竞争获取）。**判定依据是 token 相等，不是「同一任务」，也没有 owner 描述参数。** |
| `release_main_task_gate(terminal, token) -> None` | 仅 token 匹配时清空属性。terminal 为 None、token 为空、重复释放或 token 不匹配**均为无操作**（静默返回，不抛异常、无失败返回值）。 |
| `is_main_task_gate_busy(terminal) -> bool` | 只读探测当前是否有主任务持有门闸。 |

> ⚠️ 历史文档曾写 `acquire_or_claim_main_task_gate(terminal_id, owner_desc)`——该函数**在代码中不存在**（全仓 grep 零命中），不要再引用，也不要按它推断存储结构。

### 12.3 门闸入口与 token 移交（2026-10-08 按源码校正）

**现有全部门闸接入点**（新增主任务入口必须按同样模式接入）：

| 接入点 | 位置 | 模式 |
|------|------|------|
| 聊天入口 `process_message_task` | `server/chat_flow.py:139` 认领，`:259` finally 释放 | 认领/竞争 |
| 通知派发链预占 | `server/chat_flow_task_main.py:1023` 预占，`:1078` 传 token，`:1083` 派发失败释放，`:683` 外层释放 | 先预占再移交 |
| 手动压缩任务执行 | `server/tasks/compression.py:14` 认领，`:55` finally 释放 | 认领/竞争 |
| 深度压缩 REST 入口 | `server/conversation.py:1756` 预占 | 先预占再移交 |
| 工作流激活 | `server/workflow_runtime_api.py:125` 预占 | 先预占再移交 |
| 工作流通知派发 | `server/workflow_runtime_api.py:240` 预占 | 先预占再移交 |
| 外层兜底释放 | `server/tasks/models.py:1155` | 兜底 |

**token 移交通道（唯此一条）**：`RuntimeContext.directives.main_task_gate_token`（`server/runtime/context.py` 的 `InternalDirectives`）→ `TaskRecord.directives.main_task_gate_token` → `server/tasks/models.py:1056` 取出后传给 `run_chat_task_sync(..., main_task_gate_token=...)` → `process_message_task` 持 token 认领，不重复获取。

派发失败时（`server/chat_flow_task_main.py:1083`）释放门闸并回滚已打的通知标记（`_rollback_completion_notice_marks`）。

> ⚠️ 历史文档曾写 token 经 `session_data["main_task_gate_token"]` 移交——**代码中不存在该键**（全仓 grep 零命中），已按源码校正。

- **压缩运行语义（2026-10）**：手动压缩是 `task_type="compression"` 的主任务，自动压缩属于原主任务；两者均算智能体运行，输入保持可用，运行期间发送直接作为引导，停止精确取消当前任务。`server/compression_commit.py` 仅标记摘要快照中的历史前缀，历史标记/压缩记录/手动引导语/上下文统计一次原子提交；生成失败或提交前取消保留旧历史。`server/tasks/queue_state.py` 保留未消费引导及旧提前输入来源，停止/失败后连同附件恢复到所属对话草稿，不再自动续发；草稿保存成功后确认移除来源。压缩续接消费请求时在原任务锁内重新开放引导受理，最终空消费原子关闭受理；通知池不清空。
- **兜底释放**：`_run_chat_task`（`server/tasks/models.py`）外层 `finally` 兜底释放，防止异常路径门闸泄漏。

### 12.4 改代码注意事项（硬性）

1. **新增任何主任务入口必须走门闸**：聊天通过 `process_message_task`，手动压缩通过 `run_compression_task` 获取/认领相同的对话级门闸；chat/notice/compression 的受理检查也统一按对话互斥。
2. **不要在 return 分支手写 `_tool_loop_active` 恢复**：`execute_tool_calls`（`server/chat_flow_tool_loop.py`）已改为守护包装（try/finally 复位，内层 `_execute_tool_calls_impl`），新增提前返回路径无需也不应手动操作该标志——并发交错「存旧值→置True→恢复旧值」正是此前标志卡死的原因。
3. **不要依赖 build_messages 防御层掩盖并发问题**：`core/main_terminal_parts/context/messages.py` 的孤儿 tool 消息剥离只是「坏数据不再 400」的止血层，乱序段本身意味着历史已被污染；发现剥离 warning 日志应按事故排查，而不是视为正常。

### 12.5 Socket.IO 已整体移除（2026-09-11）

Web 端实时通道曾长期双轨（REST 任务轮询为主 + Socket.IO 辅助推送），本次清理后**轮询成为唯一通道**，Socket.IO 及其依赖（flask-socketio / socket.io-client / websockets）已全量移除。

**事件出口唯一权威 = 任务事件流**（`TaskRecord.events`，`_append_event`）：运行期一切事件（text/thinking/tool/token/todo/edited_files/edit_summary/context_warning/quota_exceeded/conversation_changed 标题更新等）都在其中，前端任务轮询与 CLI 共用。历史遗留的 socket 推送只是同一事件的镜像，删除不丢数据。

**各原 socket 功能的替代机制**（改代码前必查）：
- `status_update`（原 13 处推送）→ 前端 `socketMethods.fetchStatusSnapshot` 空闲 5s 轮询 `/api/status`（运行期跳过，由任务事件流驱动）；发起方操作后的即时状态走 REST 响应载荷。
- `quota_update/notice/exceeded` → `quota_exceeded` 本就在事件流；配额快照走 resource store 既有的 UsageQuotaPolling。
- `conversation_list_update / conversation_changed / conversation_loaded` → 发起方操作后主动刷新（既有主路径）；**多标签页被动同步退化为 status 轮询间接感知**（不再即时，属刻意取舍）。
- `system_ready` → `loadInitialData` 的 `/api/status` 一次性初始化已覆盖。
- 终端面板实时输出 → `GET /api/terminals/<name>/output` REST 轮询（TerminalPanel 面板打开时输出 1.5s、列表 5s），快照前缀匹配增量写入 xterm。
- 连接状态指示 → 既有 REST 心跳（`/api/health`，连续失败阈值置灰），与 socket 断开语义等价。

**硬性约束**：
1. 新增任何「实时通知」一律写任务事件流（`_append_event`）或扩 `/api/status` 载荷，**禁止**重新引入 socket 推送。
2. `server/extensions.py` 只剩 `run_background`（daemon 线程）；不要再往里加推送类函数。
3. `WebTerminal.broadcast()` / `terminal_manager.broadcast` 调用点保留但回调恒为 None（判空安全），新代码不要依赖它们产生用户可见效果。`make_terminal_callback(username)`（`server/context/resources.py`）同理：Socket.IO 移除后是恒返 None 的兼容空操作，保留仅为维持历史 import 路径（`workflow_runtime_api.py`、`resources.py` 自身构造 WebTerminal）不炸。

**Headless 形态（2026-09-11 新增）**：`server/headless_app.py` 提供无 web 站点的启动入口（CLI 自启动专用，详见 §2）；审批三类端点（tool/plan/question）已从 chat_bp 拆为独立蓝图 `server/chat/approval.py::approval_bp`（6 条 URL 一字不变），full 与 headless 双形态共用。

### 2026-10-23：运行中请求压缩

- 空闲点击仍创建独立 `task_type="compression"`，使用 `mode="manual"`，压缩后等待用户下一条消息。
- 运行中点击通过 `RuntimeService.request_compression` → `CompressionRequestMixin` 在原 TaskRecord 上登记，不创建第二个任务，不竞争或释放原任务门闸。`compression_request` 为任务内存态，停止／终态／重启后不跨任务继承。
- `VIEW_LOCK → TaskManager._lock` 下登记／消费请求。当前模型回复和整批工具调用的所有结果完整回填后执行 `mode="requested"` 深压缩；无工具的回复完成时也处理请求。最终收尾前原子消费或关闭受理窗口，防止受理成功但无人消费。
- `requested` 压缩成功后使用原 task_id 与 `work_anchor_message_id` 续接，沿用计时、编辑摘要和速度统计。重复请求及与已开始自动压缩重合的请求合并；用户主动停止仍精确取消当前任务。
- `compression_state.stage="waiting_for_tools"` 是排队状态；实际压缩使用既有 `generating_summary` 等阶段。状态进入 RuntimeProjection，与任务事件游标一同捕获并从 bootstrap 恢复。运行中请求的 ACK 不得将已有任务从事件0重新订阅，也不能覆盖已推进的阶段。
- 回归见 `test/2026-10-23_运行中压缩与状态边界/`。

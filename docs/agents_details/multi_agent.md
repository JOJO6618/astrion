# 多智能体对话类型（完整）

> 本文由 `AGENTS.md` §11 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 11) 多智能体对话类型（multi-agent conversation type）

> **2026-08 重构**：已废除「多智能体模式」全局状态，多智能体是**对话的不可变属性**（`metadata.multi_agent_mode = true`，创建时确定、不可变）。URL 统一为 `/<conv_id>`（旧 `/multiagent/*` 由前端 bootstrap 重定向到裸路径）。数据目录 `~/.astrion/astrion/host/mutiagents/`（保留原拼写）。重构方案见 `docs/conversation_type_unification_plan.md`（本地设计文档，未随仓库发布），本节只列 Agent 改代码时必须知道的硬约束。

### 11.0 前端对话类型模型（重构后）

- 三个语义单一的状态，**互不复用**：
  - `currentConversationType`（`static/src/app/state.ts`）：当前已打开对话的类型（`'normal' | 'multi_agent' | null`），由 `enterConversation` 从对话 metadata 恢复（`bootstrap.ts`），空对话为 `null`。
  - `newConversationType`（同上）：空对话时输入栏待创建类型（`'agent' | 'multi_agent'`），localStorage 持久化（`agents_new_conversation_type`），由输入栏底行 `+` 右侧的类型选择器修改（`InputComposer.vue` 的 `agent-type-switcher`）。
  - `sidebarConversationType`（`stores/conversation.ts`）：侧边栏列表过滤器（`'normal' | 'multi_agent'`），localStorage 持久化（`agents_sidebar_conversation_type`），驱动列表/搜索的 `multi_agent_mode=0|1` 请求参数与侧边栏新建按钮的对话类型。
- `stores/conversation.ts` 的 `multiAgentMode` 字段保留，但语义已变为「当前对话是否多智能体」，仅由 `enterConversation`（meta 落地）与空对话 watcher（复位 false）写入——`stores/subAgent.ts` 的读取点依赖它，不得删除。
- 侧边栏类型切换 UI：分段控件统一显示在「搜索对话」下方（平铺/分组模式位置一致，搜索时隐藏）；列表区域用 `<Transition>` 推挤式同步滑动动画（无 out-in，新面板绝对定位从侧边滑入把旧面板顶出，切多智能体向左，切回反向；切换时重置列表滚动位置）。
- 发送消息创建对话时按 `newConversationType` 选择 `/api/conversations` 或 `/api/multiagent/conversations`（`message/send.ts`）；侧边栏新建按 `sidebarConversationType` 选择（`conversation/action.ts`）。

### 11.1 角色与实例

- 主智能体显示名固定为 `Team Leader`，不需要专门的预置角色文件。
- 子智能体 = `role_id`（如 `ui-operator` / `full-stack-engineer` / `code-reviewer` / `researcher`）+ 角色内编号（同一 role_id 下从 1 递增）。显示名格式 `{Role Name}_{角色内编号}`，例如 `UI Operator_1`，后缀永远带数字。
- **编号暴露原则（2026-08-26 起）**：角色内编号显示名是**唯一**对模型和用户暴露的身份；全局 `agent_id` 是纯内部实现细节（任务字典 key / task_id 生成），由系统自动分配对话级最小空闲正整数，不接受模型指定、不出现在工具参数/结果文案/前端展示中。所有寻址类工具（send_message/stop/terminate/get_sub_agent_status/sleep 的 wait_sub_agent_output、子侧 ask_other_agent）一律用显示名。
- 主→子 / 子→主 / 子→子三种通信通过工具完成，工具签名见 `modules/multi_agent/tools.py`。
- **`send_message_to_sub_agent` 与 `ask_sub_agent` 语义不同，必须保留两者**：前者插入引导消息不阻塞，后者阻塞等待一轮回答。
- 子智能体间通信要求同时向主智能体输出汇报，不允许「偷偷沟通」。

### 11.2 子智能体执行机制

- 子智能体在主进程内 `asyncio.Task`，跑在独立后台事件循环线程里（历史原因是避开 Flask-SocketIO threading 冲突；Socket.IO 已移除，该线程模型仍保留）。工具调用复用主进程沙箱/容器链路，网络调用走 `utils.api_client.APIClient`。
- **模型请求重试（2026-08-26 起）**：`_run_loop` 对 `_call_model` 包重试循环，与主智能体 `run_streaming_attempts` 同构——最多 5 次尝试（`_SUB_AGENT_MAX_API_RETRIES=4`）、间隔 10s（`_SUB_AGENT_RETRY_DELAY_SECONDS`，用 `asyncio.sleep` 分段等待并响应软停止/取消）。重试条件：**仅当零接收**（未收到任何文本/思考/工具调用）才重试；已开始收到内容后断流（`SubAgentModelCallError.received_any=True`）直接失败。失败终态分模式：多智能体模式下 5 次全失败 → 转为 idle + `_forward_output_to_master` 报错（等 Team Leader 重新下达指令），输出期间断开 → 直接 failed 并同步向主智能体报错；传统模式一律 `_write_failure`。
- **工具「正在调用」进度事件（2026-08-26 起）**：`_call_model` 在工具名+id 首个流式 chunk 到达时即 emit `status="calling"` 进度事件（与后续 running/completed 共用同一 tool_call id），前端按 id 原地更新条目；`RunnerDetailPanel.vue` / `SubAgentActivityDialog.vue` 的 normalizeStatus 识别 `calling`（显示 spinner + 「调用中」），并支持同一 id 历史条目跨组原地更新（多工具 calling 事件交错场景）。
- 子智能体在多智能体模式下：
  - `create_sub_agent` 强制 `run_in_background=False`，不触发 `sub_agent_waiting` 事件，不阻塞前端输入区。
  - 子智能体自然的 assistant 输出结束（无 tool_calls）即本轮任务结束，进入 `idle`，上下文保留，不算 failed。
  - 不能把 `output.success == null` 直接判为 `failed`—`_check_task_status()` 对多智能体任务 `status=running/idle` 视为正常态（见 `modules/sub_agent/state.py`）。
  - idle 等待必须用 `asyncio.Event`，不能用 `threading.Event`，跨线程唤醒用 `call_soon_threadsafe`。
  - 任务记录里 `multi_agent_mode` 字段必须显式写入，缺失会导致传统后台通知池把多智能体任务当后台任务处理。`reconcile_task_states()` 已对旧任务补回。

### 11.3 消息池与派发链路（重点）

**输出端 ↔ 接收端分离**：
- 输出端（`SubAgentTask._forward_output_to_master`）：子智能体每次 assistant 文本输出都封为标准格式消息并 `push_master_message` 到会话的 `MultiAgentState.pending_master_messages`，不再做事。
- 接收端（`MultiAgentState` + dispatch）：根据主智能体当前状态选择插入方式：
  - **情况1（主运行中）**：在 `execute_tool_calls` 末尾 `process_multi_agent_master_messages(inline=True, after_tool_call_id=...)` 插入到下一轮模型 messages 列表里（详见 `server/chat_flow_tool_loop.py:1326`）。
  - **情况2（主空闲）**：`poll_multi_agent_notifications` spawn 出后台 poll，主对话空闲且 pool 有消息就 drain，调 `_dispatch_multi_agent_idle_messages` 创建 `task_type="notice"` 的后续 task，触发新一轮工作。
  - **情况3（主最后一轮无工具调用）**：主循环 `if not tool_calls:` 分支调 `process_multi_agent_master_messages(inline=False)` 后 `continue` 继续迭代，Team Leader 在同 task 续跑处理子智能体输出。（实现上与情况2同路径，即情况3通过后续 task 完成情况2）。

**情况2 硬约束（调试踩过坑）**：
  1. **Pool 优先，不能等所有 running 退出再 drain**：`ask_master` await 期间 status=running，但子智能体本身不会产生新输出，只有等主对话回答才能解套。pool 有消息就立即 drain，不管 running 状态。「对话处于运行状态」只决定前端显示态，不阻塞 pool 消费。
  2. **`_dispatch_multi_agent_idle_messages` 持久化不要重复**：前置 N-1 条调 `inject_multi_agent_master_message` 绑定持久化。最后一条只 emit 给在线客户端、不持久化、在后续 `task_manager.create_chat_task` 走的 `handle_task_with_sender` 里才 `add_conversation`。否则历史里会有两条相同 user 消息，前端刷新会被渲染两遍（一条多智能体渲染、一条通知渲染）。
  3. **Metadata `visibility` 必须显式为 `"chat"`**：`_user_message_ui_defaults("sub_agent")` 默认给 `{visibility: "compact"}`；在 `_dispatch_multi_agent_idle_messages` 构造 `auto_user_message_payload`时，如果有`**ui_defaults`，必须在它之**后**写 `"visibility": "chat"`（dict 字面量中后出现的key 胜出）。同样，在`handle_task_with_sender`处理多智能体消息时，`user_message_metadata.update(multi_agent_meta)`之后要显式`user_message_metadata["visibility"] = "chat"`。
  4. **必须传 `auto_message_type`**：前端 `isMultiAgentMessage()` 只看 `auto_message_type.startsWith('multi_agent_')`。`auto_user_message_payload` 和 `preceding_user_notices[i].payload` 都必须显式写 `auto_message_type`，字段不能用空值，否则前端 fallback 起通知渲染。

### 11.4 通知池轮询器完全独立

- 传统后台子智能体 / 后台 `run_command` 通知：`poll_completion_notifications`，在 `handle_task_with_sender` 结尾的 `needs_completion_poll` 。
- 多智能体通知：`poll_multi_agent_notifications`，在 `needs_ma_poll`。两者完全独立，避免 task_manager 单工作区互斥竞争。
- `task_complete` 事件中：
  - `has_running_sub_agents` 只算传统后台任务，不算多智能体。
  - `has_running_multi_agent` 多智能体专用字段，同时包含 _running_ 实例和 pending master 消息（详见 `server/chat_flow_task_main.py:2324`）。前端通过该字段走独立的 `startMultiAgentTaskProbe`（不启动 `sub_agent_waiting`）。

### 11.5 渲染与前端约定

- 多智能体消息渲染条件是**两个独立判断**：
  - `isMultiAgentMessage()`：看 `auto_message_type.startsWith('multi_agent_')`。
  - `getMessageVisibility()`：看 `metadata.visibility`。两者都必须正确，消息才能走多智能体渲染分支。任一错都会 fallback 到通知渲染。
- 多智能体消息不显示新的 assistant 回复头部（Astrion/工作时间），即 `metadata.starts_work=false`。但前端通过 `has_running_multi_agent` 在 `task_complete` 中单独处理恢复轮询。
- 子智能体进度弹窗：输出与工具按真实时间线混排，默认 3 行，过长用省略号或内部滚动；颜色走 `--text-primary` 等语义 token。
- 全局工具规范统一适用于多智能体权限：UI 不能引入 `compact` 以外的 fallback 样式习惯复制到多智能体渲染。

### 11.6 工具结果格式化与前端渲染位置

新增多智能体工具时，必须同步补齐「后端结果格式化」和「前端结构化渲染」，否则前端会 fallback 到原始 JSON 或空白。

**后端 formatter（主智能体工具）**
- 实现位置：`utils/tool_result_formatter/agent_context.py`
- 注册位置：`utils/tool_result_formatter/dispatch.py` 的 `TOOL_FORMATTERS`
- 处理入口：所有主智能体工具执行结果，最终由 `format_tool_result_for_context()` 转换为自然语言摘要，写入对话历史。

**后端 formatter（子智能体通信工具）**
- 实现位置：`modules/sub_agent/toolkit.py` 的 `_format_tool_result()`
- 覆盖工具：`ask_master` / `ask_other_agent` / `answer_other_agent` / `list_active_sub_agents`
- 处理入口：子智能体 tool call 结果回填到子对话上下文前。

**前端 renderer（主路径）**
- 实现位置：`static/src/components/chat/actions/toolRenderers.ts` 的 `renderEnhancedToolResult()`
- 调用方：
  - `static/src/components/chat/MinimalBlocks.vue`
  - `static/src/components/chat/StackedBlocks.vue`
- 注意：这两个视图已移除 `enhanced_tool_display` 开关判断，**所有工具块都强制走结构化渲染**，不再显示原始 JSON。

**前端 renderer（备用路径）**
- 实现位置：`static/src/components/chat/actions/ToolAction.vue` 的 `renderToolResult()`
- 调用方：`static/src/components/chat/ChatArea.vue`
- 同样已移除原始 JSON fallback，仅作为 ChatArea 独立工具块渲染的备用。

**新增工具 checklist**
1. `modules/multi_agent/tools.py` 定义工具签名。
2. `core/main_terminal_parts/tools_execution.py` 实现工具 handler 并返回标准 dict（`success` + 业务字段 + 可选 `error`）。
3. `utils/tool_result_formatter/agent_context.py` 新增 `_format_<tool_name>()`，`dispatch.py` 注册。
4. `static/src/components/chat/actions/toolRenderers.ts` 新增对应 `render<PascalCaseToolName>()` 并在 `renderEnhancedToolResult()` 中分发。
5. 如果是子智能体通信工具，同步在 `modules/sub_agent/toolkit.py` 的 `_format_tool_result()` 中补 formatter。

### 11.7 调试机制

- 调试日志统一走 `modules/multi_agent/debug_logger.py` 的 `ma_debug()` 函数，写入 `~/.astrion/astrion/host/logs/multi_agent_loop.log`。
- 关键状态转换点必须打 `ma_debug`，便于复现 bug：
  - `handle_task_with_sender_start`（含 `pending_master_messages_count`）
  - `state_push_master_message` （交出 `queue_len_before`）
  - `poll_ma_tick` （每 0.5s 一次，包含 `instance_count` / `statuses` / `pending_count` / `main_active` 的完整状态快照）
  - `dispatch_ma_idle_enter` / `dispatch_ma_idle_before_create_task` / `dispatch_ma_idle_task_created` / `dispatch_ma_idle_sender_user_message` / `dispatch_ma_idle_exit_ok`
  - `create_chat_task` 异常 / `provide_answer` 跨循环回写
  - `runtime_injected` 字段标记（metadata 里能区分多智能体 inline / idle / ask插入路径）

### 11.8 已知坑

- 多智能体模式下根本不 `emit` `sub_agent_waiting` 事件，避免前端进入「等待后台子智能体」的输入区阻塞态。
- `_announced_sub_agent_tasks` / `notified` 等标记仅适用于传统后台子智能体任务，多智能体任务不走这条通知路径。
- 多智能体任务的 `output.json` 中 `status` 在子智能体自然进入 idle 时会被写为 `"idle"`；在 `_check_task_status` 中由 `_check_task_status_keep_alive` 跳过防止错误判定为 `failed`。
- 传统后台通知池 `_collect_pending_completion_notices` 在 `task.get("multi_agent_mode")` 为真时跳过该 task；多智能体派出走独立的 `poll_multi_agent_notifications` 路径，不当混用。
- `_has_pending_completion_work` 主动排除 `multi_agent_mode=True` 的任务；两者永远独立。

### 11.9 对话切换与状态保留

- 切换会话不清理 `_running_tasks` 和 `_sub_agent_instances`。`SubAgentManager` 的全局 tasks 字典按 `task_id` 保持，多智能体状态由 `conversation_id` 在 `get_multi_agent_state` 中查。
- 子智能体对话存在 `~/.astrion/astrion/host/host/data/sub_agents/`。重启后走 `manager.restore_sub_agent` 恢复实例引用。
- **`MultiAgentState` 是进程级全局单例**（2026-07 重构）：存放在 `modules/multi_agent/state.py` 的 `GLOBAL_MULTI_AGENT_STATES`（key=`conversation_id`），所有 `SubAgentManager` 实例共享；`manager.multi_agent_states` 只是该全局 dict 的引用。此前它是 manager 实例属性，对话级 terminal 缓存重建会产生多个 manager，各自的 `_load_state` 都从磁盘快照 `from_snapshot` 出一份独立副本，导致 terminate 只标记其中一份、前端轮询落到其他副本显示陈旧 idle。`get_or_create` / `drop` / `_load_state` restore 均通过 `GLOBAL_MULTI_AGENT_STATES_LOCK`（RLock）互斥。
- **进程重启后的状态校准**（2026-07 新增）：`_load_state` 恢复 ma 快照后，会用任务记录（持久真相）校准实例终态——任务记录是 terminated/终态而快照里还是 idle 的，一律校准为终态；同处还有存量 `_None` 后缀显示名的自愈迁移（按「对话×角色×创建时间」重编号）。
- **显示名编号语义**：显示名后缀（如 `Full-Stack Engineer_1`）是**角色内编号**，创建路径 `tools_execution.py` 中通过 `peek_agent_id_for_role` + `commit_agent_id_for_role` 两步走——先 peek 构造显示名，创建成功后才提交计数器，**失败不消耗编号**（避免跳号）；全局 `agent_id` 由 `manager.next_free_agent_id()` 自动分配（对话级最小空闲正整数），两者是两套独立命名空间，不得混用。

---

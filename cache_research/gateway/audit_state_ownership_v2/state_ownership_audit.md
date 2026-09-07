# Astrion 状态责任表 S1-S10 落地核查报告（只读审计）

- 审计日期：2026-09-07
- 审计范围：`docs/runtime_contract.md` §2 状态责任表 S1-S10 + §3 正确性保障 + §6 已知缺口
- 方法：全程只读，逐项对照代码，给出「文件:行号」证据；凡无法静态定论处显式标注推断等级。
- 推断等级：✅ 已确认 / ⚠️ 存在偏差或疑点 / ❌ 不一致 / ❓ 未找到或无法静态验证

---

## 一、S1-S10 逐项核查表

### S1 对话历史与元数据 —— ✅ 一致

**声明**：权威=磁盘 conversation JSON；写入口=执行链统一 `ContextManager.add_conversation`（`utils/context_manager/message_mixin.py:127`）；保存保护=`save_conversation`（`crud_mixin.py:298`，merge-on-save 按 message_id 合并防缩减 + `_io_lock` RLock + `_atomic_write_json` 原子替换）；失效条件=删除对话（检查点恢复是唯一 allow_shrink 豁免）。

**代码证据**：
- `utils/context_manager/message_mixin.py:127` — `def add_conversation(` 行号精确匹配契约。
- `utils/conversation_manager/crud_mixin.py:298` — `def save_conversation(` 行号精确匹配。
- merge-on-save 与缩减拒绝：`utils/conversation_manager/crud_mixin.py:252-296`（`_merge_messages_by_id`，方向 C：同 id 取内存版、磁盘独有保留、内存独有追加、无 message_id 防御性跳过）；`:335-358`（调用 merge；`new_len < old_len and not allow_shrink` 时拒绝保存返回 False）。
- `_io_lock` RLock：`utils/conversation_manager/base.py:47`（`self._io_lock = threading.RLock()`）。
- 原子替换：`utils/conversation_manager/index_mixin.py:104`（`_atomic_write_json`：唯一临时文件 + json.dump + fsync + `replace_with_retry`）；保存路径 `crud_mixin.py:193-201`（`_save_conversation_file` 内 `with self._io_lock:` + `_atomic_write_json`）。
- 检查点恢复唯一豁免：`crud_mixin.py:341-358`（`allow_shrink` 参数仅豁免路径为 True）。

**结论**：契约声明与实现一致。外部写者（设置/压缩/CLI）复用同一保存链路的假设属契约 §2 标注的「已知现状」，本次未逐一枚举其是否全部走同一 `_io_lock`/原子写（见 Q(a)/总结）。

---

### S2 任务记录（TaskRecord）—— ✅ 一致

**声明**：权威=内存 `task_manager._tasks`；仅 `TaskManager` 可改；无持久化；`_lock` + 单对话 chat 互斥（:160-172，notice 豁免）；`cleanup_old_tasks`（终态超 3600s）。

**代码证据**：
- `self._tasks: Dict[str, TaskRecord] = {}` + `self._lock = threading.Lock()`：`server/tasks/models.py:98-100`。
- 单对话 chat 互斥 + notice 豁免：`server/tasks/models.py:158-173`（仅当 `normalized_task_type == "chat"` 时对同对话 `status in {pending,running}` 的 chat 任务排重并 raise `task_already_running`；notice 类型不参与排重）。
- 409 映射：`server/tasks/api.py:223-224`（`except RuntimeError ... return ... 409`）。
- `cleanup_old_tasks(3600)`：`server/tasks/models.py:105-130`（终态集合 `succeeded/failed/stopped/canceled/cancel_requested` 超 max_age 删除）；调度器 `start_task_cleanup_scheduler` `server/tasks/models.py:1100-1111`（每 600s 调 `cleanup_old_tasks(3600)`）。
- 无持久化：`TaskManager` 仅内存 dict，无落盘代码。

**结论**：一致。纯内存态（见 Q(a)）。

---

### S3 任务事件流 —— ✅ 一致

**声明**：`TaskRecord.events` 有界 deque maxlen=20000；执行链经 `_append_event`（:762）；`get_events_since` 按 offset；idx 单调分配；`_lock` 内分配。

**代码证据**：
- `self.events: deque[...] = deque(maxlen=20000)`：`server/tasks/models.py:74`。
- `_append_event`：`server/tasks/models.py:757-782`（`_lock` 内 `idx = rec.next_event_idx` → `rec.next_event_idx = idx+1` → `rec.events.append({...idx...})`；如 `next_event_idx` 缺失则回退 `rec.events[-1]['idx']+1`）。
- `get_events_since(rec, offset)`：`server/tasks/models.py:200-206`（锁内快照 `list(rec.events)` 后过滤 `e['idx'] >= offset`）。
- socket 房间推送 `user_{username}`：`server/tasks/models.py:824-826`（sender 内 `socketio.emit(event_type, data, room=f"user_{username}")`）。

**结论**：一致。idx 单调、有界、offset 续读全部命中。纯内存态（见 Q(a)）。

---

### S4 对话级主任务门闸 —— ⚠️ 基本一致，存在一处静态疑点

**声明**：权威=terminal 属性 `_main_task_gate_token`；`process_message_task` 唯一入口获取/finally 释放；通知链预占 + token 移交认领；token 匹配才能释放；任务结束/异常兜底释放。

**代码证据**：
- 门闸组件（零 Flask 依赖）：`server/main_task_gate.py`（整文件 73 行；`try_acquire_main_task_gate` / `acquire_adopted_main_task_gate` / `release_main_task_gate`（token 匹配才释放）/ `is_main_task_gate_busy`；存储终端属性 `_GATE_ATTR = "_main_task_gate_token"`）。
- 唯一入口获取/finally 释放：`server/chat_flow.py:142`（`gate_token = acquire_adopted_main_task_gate(terminal, main_task_gate_token)`，None 则拒绝并发并 return）；`:262`（`finally: release_main_task_gate(terminal, gate_token)`）。
- REST 路径：`server/tasks/models.py:895-900`（`_run_chat_task` 调 `run_chat_task_sync(...main_task_gate_token=...)` → `chat_flow.py:280-282` → `process_message_task`）。
- 通知链预占 + token 移交 + 失败回滚：`server/chat_flow_task_main.py:1014`（`gate_token = try_acquire_main_task_gate(web_terminal)`）；`:1069`（`main_task_gate_token=gate_token` 随 session_data 移交）；`:1029`（无通知释放）；`:1074-1075`（派发异常时 `release_main_task_gate` + `_rollback_completion_notice_marks`）；`_dispatch_completion_user_notice` 经 `RuntimeContext.from_terminal` + `InternalDirectives(main_task_gate_token=...)` 构造（`:590-614`）。
- `_run_chat_task` finally 兜底释放：`server/tasks/models.py:1088-1094`（按 session_data 的 `main_task_gate_token` 释放）。
- 异常回退路径（create 失败→直接执行）：`server/chat_flow_task_main.py:644-690`（`except Exception` → `report_*` → `asyncio.create_task(handle_task_with_sender(...))`，绕过 TaskRecord）。

**⚠️ 静态疑点（级别：有很大概率，未运行时验证）**：
- `handle_task_with_sender`（`server/chat_flow_task_main.py:1468+`）正文**不含任何**门闸获取/释放调用（grep 全文件仅 :1014/:1029/:1074 及 :71 导入命中）。
- 在「完成通知 create_task 失败 → 直接回退执行」路径上，门闸由轮询器在 :1014 预占持有；回退的 `handle_task_with_sender` 自身不释放；而轮询器在 `_dispatch_completion_user_notice` 正常返回后走到 :1080-1082 的 `return`（注释断言「门闸随任务移交（由任务线程 finally 释放）」），但该路径**没有任务线程**（未登记 TaskRecord）。
- 由此静态推断：回退路径执行完后门闸可能一直保持占用（`is_main_task_gate_busy` 恒 True），导致下一次通知轮询在 :1014 恒返回 None。这只是代码阅读推断，需运行时验证；若成立则与契约 §3 第 1/6 条强调的「回退路径必须保持门闸获取/释放语义」存在偏差。

**结论**：✅ 核心语义（唯一入口、finally 释放、token 匹配、通知链预占移交、失败回滚、任务线程 finally 兜底）全部一致；⚠️ 回退路径的门闸是否最终释放存在静态疑点。

---

### S5 停止标志（stop_flags）—— ✅ 一致（存放位置为模块级 dict，非对象属性）

**声明**：`state.stop_flags` dict；任务级键 = task_id（REST 即 client_sid）；`cancel_task`（REST 硬取消）、socket `stop_task`（软标志）；任务级键隔离；收尾 pop。

**代码证据**：
- 存放位置：`server/state.py:38`（`stop_flags: Dict[str, Dict[str, Any]] = {}`，模块级全局；引用方 `from server.state import stop_flags`，如 `models.py:22`）。—— 契约写作 `state.stop_flags`，实际是 `server.state` 模块级 dict，非某个 `state` 对象的属性。语义一致。
- 任务级键 = client_sid：`server/state.py:174-177`（`make_stop_keys`：`client_sid` 键 + `user:{username}` 索引键）；`:193-205`（`clear_stop_flag` 只 pop 本任务 key，`user:` 索引仅在仍指向本 entry 时才清，防误清其它并行任务）。
- REST 硬取消：`server/state.py` 键 + `server/tasks/models.py:250-264`（`loop.call_soon_threadsafe(task.cancel)` 投递硬取消 + `entry['stop']=True` + `rec.stop_requested=True`）；端点 `server/tasks/api.py:291-309`。
- socket `stop_task` 软标志：`server/socket_handlers.py:128-150`（仅 `task_info['stop'] = True`，注释明确「改为通过停止标志让任务内部处理」，直接取消已注释掉）。
- 执行链检查点轮询：`server/chat_flow_stream_loop.py:61`、`server/chat_flow_task_support.py:576`、`server/chat_flow_tool_loop.py:623`（`client_stop_info.get('stop')`）。
- 收尾 pop：`server/tasks/models.py:1083`（`finally: stop_flags.pop(rec.task_id, None)`）。

**结论**：一致。任务级键隔离、REST 硬取消 vs socket 软标志均成立。

---

### S6 审批/提问条目 —— ✅ 一致（含契约已标注的缺口/疑点，均被代码证实）

**声明**：三个内存 manager（tool/plan/user_question）；键=approval_id/question_id；list 按 username+conversation_id；执行链 create；REST 置终态（锁内单次裁决）；无持久化、无 TTL；轮询 0.2~0.3s、默认超时 3600s；超时/取消不回写终态（已知缺口）；审批条目 task_id 疑恒 None（静态疑点）。

**代码证据**：
- 三个 manager 文件：`modules/tool_approval_manager.py`（92 行）、`modules/plan_approval_manager.py`（100 行）、`modules/user_question_manager.py`。均 `self._items: Dict = {}` + `threading.Lock()`，纯内存。
- 锁内单次裁决（已决定返回现状）：`tool_approval_manager.py:63-91`（`decide` 内 `if item.get("status") != "pending": return dict(item)`）；`plan_approval_manager.py:75-90`（`answer`）；`user_question_manager.py:143-164`（`answer`）。
- list 按 username(+conversation_id) 过滤：`tool_approval_manager.py:51-63`、`plan_approval_manager.py:50-63`、`user_question_manager.py:126-141`。
- 无 TTL：三个 manager 均无过期/清理逻辑；`_items` 只在 create/decide 改，无 TTL 字段。
- 轮询 0.2~0.3s 默认超时 3600：`server/chat_flow_tool_loop.py:248-263`（`_wait_for_tool_approval`，`asyncio.sleep(0.2)`）、`:281-307`（`_wait_for_user_questions`，`sleep(0.2)`）、`:310-324`（`_wait_for_plan_approval`，`sleep(0.3)`）；超时经 `_approval_timeout_for(web_terminal) or 3600.0`（`:232-246`、`:454/:597/:907/:1178`）。
- 超时/取消不回写终态（已知缺口被证实）：三个 `_wait_*` 在超时时**只本地返回** timeout 状态（`:258-260`、`:303-305`、`:322-324`），**不调用 manager 把条目置为终态**，条目保持 `pending` 常驻内存直至重启。
- task_id 疑恒 None（静态疑点被强烈支持）：
  - create 调用点均传 `task_id=getattr(web_terminal, "task_id", None)`：`server/chat_flow_tool_loop.py:427`（plan）、`:565`（user_question）、`:866`、`:1137`（tool）。
  - `WebTerminal.__init__`（`core/web_terminal.py:81-135`）**未定义/未设置任何 `self.task_id`**；全代码库未发现 `terminal.task_id = ...` 赋值（仅 `modules/multi_agent/state.py:254` 是 AgentInstance 的 task_id，无关）。
  - `handle_task_with_sender` 内事件 task_id 也回退到 `getattr(web_terminal,"task_id",None) or client_sid`（`server/chat_flow_task_main.py:1524`），反证 terminal 上无有效 task_id。
  - → 结论（有很大概率，静态）：审批/提问条目的 task_id 字段恒 None。需运行时打点最终确认。

**结论**：一致，且契约 §6 标注的缺口（不回写、无 TTL、task_id 恒 None）在代码层面均被证实；socket 软 stop 不打断审批等待见 Q(c)。

---

### S7 对话级 terminal 实例 —— ✅ 一致

**声明**：`state.user_terminals` key=`username::workspace_id::conversation_id`；`get_user_resources` 创建/重建；回收器 24h 无活动关闭；回收 pop 前校验实例身份。

**代码证据**：
- key 结构：`server/context/resources.py:52-54`（`_make_terminal_key`：`base = f"{username}::{workspace_id}"`，有 conversation_id 则 `f"{base}::{conversation_id}"`）。
- 存储：`server/context/resources.py:304`、`:458`（`state.user_terminals[term_key] = terminal`）。
- 创建/重建逻辑：`get_user_resources` `server/context/resources.py:118`（`conversation_id 非空时返回对话级 terminal`）；`:218`、`:411`（`_make_terminal_key` 构造并创建/复用）；重建（`_reaper_closing` 标记）见 reaper。
- 回收器 24h：`server/context/reaper.py:40`（`CONVERSATION_TERMINAL_TTL_SECONDS = 24*3600`）；`:92-130`（`reap_idle_conversation_terminals`：只回收三段 key、超 TTL 且 `_conversation_terminal_has_running_work` 为空，先打 `_reaper_closing` 标记，二次确认，最后 `if state.user_terminals.get(term_key) is terminal` 才 pop）。

**结论**：一致。

---

### S8 权限模式 / 执行环境 / 网络权限 —— ✅ 一致

**声明**：权威=对话 metadata + terminal 当前值；`server/chat/permission.py` 端点：空闲立即生效、运行中修改入队由工具循环消费；metadata 持久化；排队保证单写者。

**代码证据**：
- 端点：`server/chat/permission.py:138-233`（`/api/permission-mode` GET/POST）、`:236-321`（`/api/execution-mode`）、`:323-395`（`/api/network-permission`）。
- 空闲立即生效 / 运行中入队：`:172-185`（运行中 `terminal.queue_permission_mode_change(target_mode)` + `_sync_workspace_terminal_mode`）、`:207-215`（空闲 `set_permission_mode` 立即生效）；execution 同结构 `:271-295`/`:303`；network `:357-369`。
- 队列消费端：`core/main_terminal.py:325-376`（`queue_permission_mode_change`/`queue_execution_mode_change`/`queue_network_permission_change` 写入 pending；`apply_pending_runtime_mode_changes` 统一应用）；被工具循环在检查点消费：`server/chat_flow_tool_loop.py:1544-1549`。
- metadata 持久化：`core/main_terminal_parts/tools_policy.py:369-414`（`set_permission_mode` 内 `update_conversation_metadata(conv_id, {"permission_mode": normalized})`）；恢复：`core/web_terminal.py:509`（从 meta 恢复 pending_permission_mode）。

**结论**：一致。

---

### S9 多智能体实例状态 —— ✅ 一致

**声明**：`GLOBAL_MULTI_AGENT_STATES` 进程级单例 + 磁盘快照；`SubAgentManager`/dispatch 链路；`_load_state` 恢复校准；`GLOBAL_MULTI_AGENT_STATES_LOCK`；失效=terminate/对话删除。

**代码证据**：
- 进程级单例 + 锁：`modules/multi_agent/state.py:668`（`GLOBAL_MULTI_AGENT_STATES: Dict[str, "MultiAgentState"] = {}`）、`:671`（`GLOBAL_MULTI_AGENT_STATES_LOCK = threading.RLock()`）；`SubAgentManager.multi_agent_states = GLOBAL_MULTI_AGENT_STATES`（`modules/sub_agent/manager.py:76`）。
- 磁盘快照：`modules/sub_agent/state.py:168-196`（`_save_state`/`_save_state_unsafe`：payload 含 `tasks`/`conversation_agents` + `multi_agent_states` 快照，写 `self.state_file`= `data_dir/sub_agents.json`，`manager.py:67`）。多智能体专用 data_dir → `mutiagents/`：`server/multi_agent.py:77`。
- `_load_state` 恢复校准：`modules/sub_agent/state.py:32-168`：恢复 state_file → 用 `GLOBAL_MULTI_AGENT_STATES_LOCK` 加锁 `from_snapshot` 还原 `multi_agent_states`（:75-101，跳过已在内存的 conv）；**终态校准**（`:104-133`：`load_state_calibrate_agent_status`，按任务记录 `TERMINAL_STATUSES ∪ {terminated}` 纠正实例 status，防已终结实例以 idle 复活）；`_None` 显示名自愈（:138-167）。
- terminate/失效路径：`MultiAgentState.shutdown`（`modules/multi_agent/state.py:632-658`，清 output_waits/agents/queues）。

**结论**：一致。

---

### S10 用户偏好（模型/模式/个性化）—— ⚠️ 基本一致，原子写声明与代码不完全吻合

**声明**：settings.json / personalization.json（运行态路径）；设置类端点；执行链只读快照；**配置文件原子写**；会话快照（session_data）受理时固化。

**代码证据**：
- 运行态路径：`modules/personalization_manager.py:36`（`PERSONALIZATION_FILENAME = "personalization.json"`）；实际落盘 `.runtime/host/users/<user>/projects/<ws>/data/personalization.json`（运行态目录）。**未发现**运行态 `settings.json`（host 沙箱策略的 settings.json 在 `modules/host_sandbox_policy.py:70-121`，是另一文件，非用户偏好）。
- 设置端点：`server/chat/settings.py:73-129`（thinking/run mode）、`:194-232`（model）、`:327-365`（`/api/personalization` POST → `save_personalization_config`）。
- 执行链只读快照 + 受理时固化：`server/tasks/models.py:180-190`（`create_chat_task` 要求显式 `session_data`，缺失即 ValueError `tasks.missing_session_data`）；`:881-914`（`_run_chat_task` 从 `rec.session_data` 还原 `RuntimeIdentity`，不读 Flask session）；`server/context/personalization.py:23-106`（`_apply_workspace_personalization_preferences` 用 `session_model` 快照优先）。
- ⚠️ **"原子写"偏差**：`save_personalization_config` 使用**直接写** `with open(path,"w") as f: json.dump(...)`（`modules/personalization_manager.py:851-856`）——**非** temp-file+replace 原子写（对比 S1 的 `_atomic_write_json`）。settings.py:338/352 调用同一函数。因此「配置文件原子写」对 personalization.json 这条链**不成立**（直接覆写，存在写中断损坏风险），与对话 JSON 的原子写形成不对称。个人认为这是偏差，非致命——写入频率低、单进程单写者。

**结论**：⚠️ 部分一致——路径、只读快照、会话固化均正确；「原子写」声明对 personalization.json 不符合（直接覆写，非原子替换）。

---

## 二、三个整体问题

### (a) S2/S3/S6 是否确为纯内存态（进程重启即失）？有无持久化？

**结论：是，确为纯内存态，均无任何持久化。** 证据：
- **S2 TaskRecord**：`TaskManager.__init__` 只建内存 dict `self._tasks`（`server/tasks/models.py:98-100`），无落盘代码；`cleanup_old_tasks` 只是删除终态（:105-130），不是持久化。重启即失。
- **S3 TaskRecord.events**：`deque(maxlen=20000)`（`models.py:74`），只存在于 TaskRecord 内存对象，随 S2 一起消失。
- **S6 三个 manager**：`self._items` 均为内存 dict（`tool_approval_manager.py:14`、`plan_approval_manager.py:14`、`user_question_manager.py:14` 附近），无磁盘写。重启即失。
- 契约 §2「S2/S3/S6 为内存态：不能承诺重启续跑或无限期回放」与实现一致；§2 结尾「清理有 3600s 窗口」= `cleanup_old_tasks(3600)` 每 600s 跑一次（`models.py:1100-1111`）成立。
- 补充：同一进程内，S2/S3 在 3600s 窗口内不立即丢（cleanup 定时清）；S6 条目则**永不自动清理**（无 TTL）直到重启——两者内存生命周期行为不同，值得注意。

### (b) 契约 §3 列出的 6 项「已有正确性保障」在代码中是否都能找到对应实现？

**6 项全部可找到对应实现：**
1. **单写者不变量**（门闸）：`server/main_task_gate.py` 整文件 + `chat_flow.py:142/262` + 通知链 `chat_flow_task_main.py:1014/1069/1074-1075` + `models.py:1088-1094` 兜底。✅
2. **受理互斥**（create_chat_task chat 互斥 409 + notice 豁免）：`models.py:155-173` + `api.py:223-224`（409）。✅ 受理去重(S2)与执行互斥(S4)确实分属两层。
3. **保存保护**（merge-on-save + I/O 锁 + 原子替换 + 缩减拒绝）：`crud_mixin.py:252-296/335-358/193-201`、`base.py:47`、`index_mixin.py:104`。✅
4. **客户端恢复**（idx+offset 轮询 + running-status 对账 + task_id/idx 去重 + 过期响应过滤）：`models.py:757-782/200-206`（idx+offset）；`static/src/app/methods/taskPolling/probe.ts`（对账）；`static/src/stores/task.ts:197`（`task-poll-stale-response-ignored` 过期过滤）、`:267`（`stale-event-loop-abort`）、`:527`（去重集合）、`:313`（按 idx 去重）。✅
5. **审批单次裁决**（锁内裁决 pending，已决定返回现状）：`tool_approval_manager.py:63-91/83-86`、`plan_approval_manager.py:79-82`、`user_question_manager.py:151-154`。✅
6. **异常回退路径**（create 失败→直接 `handle_task_with_sender`，门闸保护下运行、绕过 TaskRecord）：`chat_flow_task_main.py:644-690`。⚠️ 但存在与 S4 一致的静态疑点（回退路径门闸释放问题，见 S4）。

### (c) 契约 §6 已知缺口的实际代码状态

| 契约 §6 缺口 | 代码实际状态 | 证据 |
|---|---|---|
| 超时/取消不回写 pending 终态 | **存在**。`_wait_for_tool_approval/_wait_for_user_questions/_wait_for_plan_approval` 超时仅本地返回，不把 manager 条目置终态。 | `chat_flow_tool_loop.py:248-263/281-307/310-324` |
| 条目无 TTL | **存在**。三个 manager 的 `_items` 无过期/清理逻辑。 | 三个 manager 全文 |
| 审批条目 task_id 疑恒 None | **静态强烈支持**：create 调用点传 `getattr(web_terminal,"task_id",None)`；WebTerminal 从不设置 task_id。需运行时打点确认。 | `chat_flow_tool_loop.py:427/565/866/1137`；`core/web_terminal.py:81-135` |
| socket 软 stop 不打断审批等待 | **大概率成立**：socket `stop_task` 只置软标志不取消 asyncio task（`socket_handlers.py:128-150`）；审批等待在工具循环内 `asyncio.sleep` 轮询，软标志不会让它立刻退出（等待循环只检查 manager 状态与 timeout，不检查 stop）。REST 硬取消通过 `call_soon_threadsafe(task.cancel)`（`models.py:255`）可打断。 | `socket_handlers.py:128-150`、`models.py:250-264`、`chat_flow_tool_loop.py:248-324` |

补充说明：socket 软 stop「不打断」结论为静态推断（有很大概率）。执行链检查点会轮询 stop 标志，但审批等待循环本身不检查它，因此软 stop 只能等审批超时/回答后下一检查点才生效。

---

## 三、总结：状态唯一 Owner 达成度

**已有唯一 Owner（实现与契约一致）：**
- **S1 对话历史与元数据**：单保存保护（merge + 锁 + 原子写）→ owner 明确（ContextManager / crud_mixin 保存链）。✅
- **S2 任务记录**：唯一 `TaskManager`（`server/tasks/models.py`）持有 `_tasks`。✅
- **S3 任务事件流**：`TaskRecord.events` 由 `_append_event` 唯一追加、`TaskManager` 读写。✅
- **S4 对话级主任务门闸**：进程级门闸组件唯一裁决，`process_message_task` 唯一获取入口。✅（回退路径释放存疑）
- **S6 审批条目**：三个 manager 各自唯一持有条目（`threading.Lock` 内单写裁决）。✅（内存态）
- **S7 对话级 terminal**：`get_user_resources` 唯一创建 + reaper 唯一回收。✅
- **S9 多智能体状态**：进程级 `GLOBAL_MULTI_AGENT_STATES` 单例 + `_load_state` 恢复校准 + 快照。✅

**仍属分散 / 内存态（影响「Gateway 状态唯一 Owner」达成）：**
- **S2/S3/S6 彻底的内存态**：进程重启即失，无持久化、无跨进程一致性（`models.py`、三个 approval manager）。这是「重启后续跑/回放」无法承诺的主因。
- **S5 stop_flags**：模块级全局 dict（`server/state.py:38`），读写点分散（REST models.py、socket handlers、执行链多个检查点），本质是进程内共享可变全局，无锁（依赖单线程 asyncio 事件循环 + 任务线程隔离），跨进程不成立。
- **S10 用户偏好**：写路径虽收敛到 `save_personalization_config`，但该写**非原子**（直接覆写 `personalization_manager.py:851-856`），与 S1 对话原子写不对称；且「执行链只读快照」依赖受理时 `session_data` 固化，若未来新增写入口不复制该快照机制则可能偏离。
- **S4 回退路径门闸释放疑点**：若成立，会使一个对话的门闸在回退场景下长期占用（动态疑点，需运行时确认）。
- **S6 task_id 恒 None**：审批条目无法关联到具体 Run（task_id 字段形同虚设），影响「审计/对账/阶段三超时语义」定位到任务。

**对「Gateway 状态唯一 Owner」判断的关键输入**：当前每一类状态在**单一进程内**基本都有唯一 owner 与互斥；但 S2/S3/S6 是进程内内存态（无跨进程 owner），S5/S10 存在分散/非原子写点。若 Gateway 化目标要求「跨进程/重启后状态唯一」，则内存态三大块（S2/S3/S6）与 S5 全局仍是障碍；若仅要求「进程内单写者」已达度较高。S4 回退路径与 S6 task_id 归属属需先闭环的两个具体疑点。

---

## 附：审计方法与人眼注意

- 全程只读，未修改任何项目文件。
- 静态结论均来自代码阅读；「有很大概率」的推断（S4 回退释放、S6 task_id 恒 None、socket 软 stop 不打断）明确标注，未以运行验证冒充确定结论。
- 交付目录：`cache_research/gateway/audit_state_ownership_v2/`。

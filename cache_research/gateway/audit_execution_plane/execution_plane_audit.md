# Execution Plane 盘点与耦合点报告（Gateway 化第 4 步前置调研）

> 版本：v1（2026-09-07）
> 定位：为「Runtime 与执行环境分层（Execution Contract）」盘点工具执行链路现状，纯只读调研，未修改任何项目代码。
> 范围：`core/main_terminal_parts/`、`modules/terminal_ops/`、`modules/persistent_terminal/`、`modules/file_manager/`、`modules/host_sandbox_runner.py`、`modules/docker_readonly_exec.py`、`modules/landlock_launcher.py`、`core/web_terminal.py`、`core/main_terminal.py`、`server/chat_flow_tool_loop.py` 及必要的关联文件（sender/事件链）。
> 行号以 2026-09-07 工作区代码为准；若与代码冲突，以代码为准。

---

## 0. 执行链路全景（一次理解）

```
模型循环 (Runtime 层)
  chat_flow_tool_loop.py:522 _execute_tool_calls_impl
    ├─ web_terminal.evaluate_tool_permission()          # 权限裁决（S8）
    ├─ capture_monitor_snapshot(file_manager)           # 执行前快照读（tool_start 附带）
    └─ web_terminal.handle_tool_call(function_name, args)  # tools_execution.py:1057 单一入口
         ↓ 按 tool_name 分派（约 30 个分支）
         命令执行   → self.terminal_ops.run_command(...) / bg_manager.create_background_command(...)
         文件读写   → self.file_manager.*（host 本地 or ContainerFileProxy→docker exec）
         终端会话   → self.terminal_manager.open_terminal/send_to_terminal/get_terminal_snapshot
         子智能体   → self.sub_agent_manager.create_sub_agent/execute_tool_for_sub_agent
         其他       → memory/search/ocr/todo/workflow/personalization 等 manager
         ↓ 最终执行环境
         Host:  asyncio.subprocess[(sandbox-exec|bwrap|WSL bwrap) 或 裸 shell]
         Docker: docker exec [-u readonly-uid] [/bin/bash -lc cmd]（只读走 docker_readonly_exec + landlock）
         文件:   进程内 直接读写（host）或 docker exec python helper（container_file_proxy.py）
```

**现状核心观察**：工具 handler（Runtime 侧）直接持有三个"执行环境句柄"属性——`self.terminal_ops`、`self.file_manager`、`self.terminal_manager`（外加 `self.sub_agent_manager`、`self.background_command_manager`）。执行环境选择逻辑（host 沙箱 vs docker vs direct）**内嵌在** terminal_ops/file_manager 各自的方法内部，由 `self.container_session`（`ContainerHandle`，`mode ∈ {docker, host}`）与 `self.host_execution_mode`（`sandbox/direct`）两把开关驱动。Runtime 与 Execution Plane 之间目前**没有独立的接口层**。

---

## 1. 执行面清单

### 约定
- ① = 工具入口（handler 分支）；② = 中间层；③ = 最终执行后端；「触达执行环境」= 会产生真实子进程 / 真实文件写 / 真实容器会话。
- 所有行号 = 该分支/函数定义行（handler 分支为 `elif tool_name ==` 行）。

### A. 命令执行类

| 子类 | ① 入口（handler） | ② 中间层 | ③ 最终执行后端 | 执行环境来源 |
|---|---|---|---|---|
| run_command（前台） | `tools_execution.py:1879` + `:1936` `await self.terminal_ops.run_command(...)` | `terminal_ops/run.py:434 run_command` → `:160 _run_command_subprocess` | 见下方 A1/A2/A3 三分支 | `self.terminal_ops`（`container_session` + `host_execution_mode`） |
| run_command（后台） | `tools_execution.py:1909-1924` `bg_manager.create_background_command(terminal_ops=...)` | `background_command_manager.py:39 create_background_command` → `:185 _run_command_thread`（**自带完整 docker/host 分支，不调 terminal_ops.run_command**） | 同 A1/A2/A3，逻辑为复制实现 | terminal_ops 的属性快照（session/python_env/host_execution_mode/网络权限/写权限） |
| custom_tool（自定义工具） | `tools_execution.py:1218` `await self.custom_tool_executor.run(...)` | `custom_tool_executor.py:40 run` → `:69 await self.terminal_ops.run_command(...)` | 同 run_command | `self.custom_tool_executor` 构造时注入的 `self.terminal_ops`（main_terminal.py:167） |
| install_package（若暴露） | `terminal_ops/run.py:410 install_package` | → `_run_command_subprocess` | 同 run_command | 同上 |

**A1 后端：Docker 容器内执行**（当 `container_session.mode == "docker"`）
- `terminal_ops/run.py:177-207`：`docker exec -e PATH=... -w <mount_path>/<rel> <container_name> /bin/bash -lc <command>`；
- 只读（`sandbox_write_access=False`）时追加 `docker_readonly_exec_args()`（`docker_readonly_exec.py:66`，`-u 10001:10001` 非特权 uid 强制 DAC 只读），并 `docker_readonly_wrap_inner`（`docker_readonly_exec.py:172`）加 Landlock 进程级只读域（`landlock_launcher.py:91 install_readonly_domain`，`_landlock_enabled` 开关，失败自动降级纯 DAC）。
- 后台同构：`background_command_manager.py:232-260`。

**A2 后端：宿主机 sandbox 内执行**（`host_execution_mode != "direct"` 且 `host_sandbox_enabled()`）
- `terminal_ops/run.py:218-242`：先 `build_host_sandbox_plan`（可写，`host_sandbox_runner.py:197`）或 `build_host_sandbox_readonly_plan`（只读，`:213`）生成 `SandboxPlan`，再 `asyncio.create_subprocess_exec`；
- 按平台展开：macOS `sandbox-exec`（`_build_macos_plan` :251）、Linux `bwrap`（`_build_linux_plan` :409）、Windows WSL bwrap（`_build_windows_plan` → `_build_windows_wsl_plan` :626）；网络权限由 plan 注入（`_build_macos_network_policy` :186，`restricted/full/none`）。
- 后台同构：`background_command_manager.py:262-290`。

**A3 后端：裸宿主机执行（direct，无沙箱）**
- `terminal_ops/run.py:255-262` `asyncio.create_subprocess_shell`；后台同构 `background_command_manager.py:290+`。
- `host_execution_mode == "sandbox"` 但 `host_sandbox_enabled()` 为假时直接返回错误（`terminal_ops/run.py:249-253`）。

### B. 文件读写类（文件工具 = 进程内操作，不经 OS 沙箱子进程；沙箱语义在进程内复刻）

| 子类 | ① 入口（handler） | ② 中间层 | ③ 最终执行后端 |
|---|---|---|---|
| read_file/read_skill | `tools_execution.py:1219/1223` → `tools_read.py:419 _handle_read_tool`（`read/serach/extract` 3 模式）→ `file_manager.read_file/read_text_segment/search_text/extract_segments`（`read_mixin.py:95/130/221/295`） | `file_manager`（`base.py:50`） | host：进程内路径读写（先过 `_validate_path`：`path_mixin.py:83` + `_ensure_host_access`：`path_mixin.py:215` 复刻 macOS 禁读清单）；docker：`_container_call` → `ContainerFileProxy.run`（`container_file_proxy.py:464`）→ `docker exec -i <container> python <helper>` |
| write_file | `tools_execution.py:1625-1650`（写前 `_track_shallow_versioning` 浅备份）→ `file_manager.write_file`（`crud_mixin.py:242`） | 同左 | host：直接 `Path.write_text`（受 `_ensure_host_access("write")` 授权范围限制）；docker：container proxy `_write_file`（`container_file_proxy.py:246`） |
| edit_file | `tools_execution.py:1652-1675` → `file_manager.replace_many_in_file`（`replace_mixin.py:125`） | 同左 | host：进程内替换；docker：proxy |
| create_file / delete_file / rename_file / create_folder | `tools_execution.py:1569/1589/1604/1677` | `crud_mixin.py:53/93/133/193` | host：进程内；docker：proxy |
| save_webpage（写文件） | `tools_execution.py:1847` `file_manager.write_file(target_path, ...)` | 同 write_file | 同 write_file |
| conversation_review（写文件） | `tools_execution.py:2094` `save_review_file()` → `target.write_text(...)`（**绕过 file_manager**，直写工作区 `WORKSPACE_REVIEW_DIRNAME`） | ― | host 进程内直写 |
| view_image / view_video | `tools_execution.py:1243/1281`（**只 stat/read 元数据**，设置 `pending_image_view/pending_video_view`，不读内容） | ― | 进程内 `Path.stat` |
| update_project_memory / manage_personalization | `tools_execution.py:681/2519` | `_handle_update_project_memory` / `_execute_manage_personalization` | 写运行态目录文件（.astrion/memory、personalization.json，S10） |

> 注：`save_webpage` 与 `conversation_review` 是文件工具里**绕开 FileManager 抽象**的两个直写点（前者最终仍走 file_manager.write_file，后者完全绕过）。

### C. 终端会话类（持久终端）

| 子类 | ① 入口（handler） | ② 中间层 | ③ 最终执行后端 |
|---|---|---|---|
| terminal_session open | `tools_execution.py:1315-1337` → `terminal_manager.open_terminal`（`terminal_manager.py:212`） | `PersistentTerminal.start`（`persistent_terminal/start.py:72`） | docker：`_start_docker_terminal`（:241）→ `_start_existing_container_terminal`（:258）/ `_start_new_container_terminal`（:315）；host：`_start_host_terminal`（:141，`build_host_sandbox_shell_plan`）或 direct `_start_plain_host_terminal`（:227） |
| terminal_input | `tools_execution.py:1349-1357` → `terminal_manager.send_to_terminal`（`terminal_manager.py:521`） | `PersistentTerminal.send_command`（`persistent_terminal/command.py:70`） | 向已启动的子进程 stdin 写 + 读 stdout（`io.py:91 _read_output`） |
| terminal_snapshot | `tools_execution.py:1361-1366` → `terminal_manager.get_terminal_snapshot`（`terminal_manager.py:697`） | `PersistentTerminal.get_snapshot`（`lifecycle.py:58`） | 纯缓冲读，不触执行 |
| terminal_session close/reset/list | `tools_execution.py:1328-1345` | `terminal_manager.close_terminal/reset_terminal/list_terminals`（:325/375/500） | 进程终止/信号 |

> 终端会话的 sandbox 后端选择：`terminal_manager.py:100-107 / 132-178`（`sandbox_mode` 默认 host，`container_session.mode=="docker"` 时转 docker；`_build_sandbox_options` 注入 `docker_readonly_exec` 与 `container_name/mount_path`）。终端的 broadcast 回调 = 上级 terminal 注入的 `message_callback`（`web_terminal.py:135/147`，Web 侧经 `attach_user_broadcast` 指向 emit_event 安全包装）。

### D. 子智能体类（执行派生，工具执行复用主进程链路）

| 子类 | ① 入口（handler） | ② 中间层 | ③ 最终执行后端 |
|---|---|---|---|
| create_sub_agent（阻塞/后台） | `tools_execution.py:2147` 分支，调用点 `:2213`（多智能体）/ `:2248`（传统） | `sub_agent/manager.py:225 create_sub_agent` → `execute_tool_for_sub_agent`（`manager.py:1003`）→ `self.terminal.handle_tool_call(...)`（`:1019`）→ **回到主进程执行链 A/B/C** | 复用主 terminal 环境（container_session/host_execution_mode 同源，`manager.py:167 set_container_session` / `:171 set_host_execution_mode`） |
| terminate_sub_agent / get_sub_agent_status / send_message_to_sub_agent / stop_sub_agent / answer_sub_agent_question / wait_sub_agent_output（sleep） | `tools_execution.py:2299/2325/2356/2407/2427/1369` | `sub_agent/manager.py:629/809/1307/570/…` | 控制面（状态/消息），不直接触执行环境；wait 阻塞等输出 |
| read_mediafile（子智能体专用） | `sub_agent/manager.py:1015`（`handle_read_mediafile`） | ― | 进程内读媒体文件（返回 base64 给模型） |

### E. 其他（触达但与执行环境弱相关）

| 子类 | ① 入口（handler） | 最终后端 |
|---|---|---|
| sleep（含 wait_sub_agent_*） | `tools_execution.py:1369` | asyncio 等待，不触执行环境 |
| ocr_image / vlm_analyze | `tools_execution.py:1237` | `self.ocr_client.vlm_analyze`（读工作区图片文件 + 模型推理） |
| web_search / extract_webpage / save_webpage | `tools_execution.py:1679/1740/1784` | 外部网络 API（Tavily）；save 落盘见 B |
| todo / memory / conversation_search / manage_personalization | `tools_execution.py:2132/1955/2026/2503` | 内存/运行态文件（TODO 存储在 context_manager，记忆在 memory_manager，personalization 覆写文件） |
| trigger_easter_egg | `tools_execution.py:2500` | 前端效果，不触执行 |

**一句话执行面清单**：真正触碰"执行环境"（能产生真实副作用）的工具 = **命令执行**（run_command 前台/后台、custom_tool、install_package）、**文件写**（write_file/edit_file/create_file/delete_file/rename_file/create_folder/save_webpage/conversation_review/update_project_memory/manage_personalization）、**文件读**（read_file 族、read_mediafile、ocr）、**终端会话**（terminal_session/terminal_input）、**子智能体**（其内部工具执行复用主链路）。其余为控制面/查询面。

---

## 2. 耦合点清单：执行链路中的 Web 概念依赖

### 2.1 依赖链总览（sender / 广播怎么流进执行链路）

```
执行链路事件出口（工具循环内 sender('tool_start'/'tool_approval_required'/...)）：
  chat_flow_tool_loop.py:522 _execute_tool_calls_impl  ← sender 参数透传
    └─ 上游 sender 定义点：
       (1) tasks/models.py:901 任务级 sender（_run_chat_task 内定义，run_chat_task_sync 传入）
            ├─ _append_event(rec, ...)          # TaskRecord 事件流（S3，非 Web）
            └─ socketio.emit(..., room=f"user_{username}")   # ←【仍耦合 · 直接 import server.extensions.socketio】
       (2) chat_flow_task_main.py:1511 raw_sender 包装（补 conversation_id/task_id/client_sid）
       (3) REST/回调适配层 sender：tasks/api.py / 通知链（chat_flow_task_main.py:618 dispatch_completion_create_chat_task 完成通知派发）
  terminal.broadcast / context_manager._web_terminal_callback（shell 输出、token_update、todo_updated）：
    └─ web_terminal.py:135/147/161  注入 message_callback
         └─ server/context/broadcast.py:16 make_terminal_callback → emit_event(..., room=f"user_{username}")
              └─ server/extensions.py:10 emit_event    # ←【已解耦安全包装，socketio 未绑定即静默】
```

### 2.2 分档清单

#### ✅ 已在第 1/2 步解耦（经安全包装，Gateway 可独立初始化）

| 位置 | 内容 | 说明 |
|---|---|---|
| `server/extensions.py:10-23` | `emit_event(event, data, room, ...)` | `socketio.server is None`（未绑定 app）时静默跳过 → 非 Web 进程零副作用 |
| `server/extensions.py:25-29` | `run_background(fn, ...)` | 未绑定时降级 daemon 线程 → 任务线程可脱离 socketio 启动 |
| `server/context/broadcast.py:16-25` | `make_terminal_callback(username)` | 只调 `emit_event`（安全包装），不直接 import socketio.emit |
| `server/context/broadcast.py:28-38` | `attach_user_broadcast(terminal, username)` | 把 terminal.message_callback / terminal_manager.broadcast 指到安全包装 |
| `terminal_manager.broadcast`、`PersistentTerminal.broadcast` | 终端 IO 事件出口 | 值为 terminal 注入的 message_callback（Web 侧指向 emit_event 包装；CLI 侧为 None，`terminal.py` 构造时 `broadcast_callback=None`，main_terminal.py:134） |
| `context_manager._web_terminal_callback`（token_update/todo_updated/编辑摘要） | `utils/context_manager/token_mixin.py:236`、`todo_annotation_mixin.py:138` | 值 = terminal.message_callback（web_terminal.py:161）；task 运行时被 models.py:930 切到任务 sender |
| `chat_flow_tool_loop.py:478-479` | `emit_event('status_update', ...)` | 经 extensions 安全包装 |
| `chat_flow_task_main.py:1850/2640/2692` | `run_background(...)` | 经 extensions 安全包装 |
| 执行链路对 Flask session 的读取 | `server/chat_flow_task_main.py / chat_flow_tool_loop.py / tasks/models.py / tools_execution.py / terminal_ops / file_manager / persistent_terminal` | **grep `session[` = 0 命中**（T12 已成立）；session 读取只剩适配层 `resources.py:149`（带 `has_request_context()` 兜底） |

#### ⚠️ / ❌ 仍耦合（执行链路直接依赖 Web 广播对象）

| 位置 | 内容 | 耦合性质 |
|---|---|---|
| **`server/tasks/models.py:919-920`**（任务级 sender） | `from server.extensions import socketio; socketio.emit(event_type, data, room=f"user_{rec.username}")` | ❌ **直接 import socketio 实例并调用 .emit**——绕过了 emit_event 包装，未绑定时**会抛异常**（socketio.server None 时 emit 行为未定义/异常）。这是主 Run 全部事件的实时推送口（含工具事件），Gateway 独立进程下会炸 |
| **`server/tasks/models.py:1045-1054`** | `socketio.emit('task_stopped', stopped_payload, room=f"user_{rec.username}")` | ❌ 同上（终态推送口） |
| **`server/chat_flow_task_main.py:975-989`**（完成通知链预写回显） | `from .extensions import socketio; socketio.emit(...)` | ❌ 直接 socketio.emit（完成通知派发链路，`poll_completion_notifications`） |
| **`server/chat_flow_task_main.py:1118-1125`**（多智能体 idle 派发回显） | 同上 `socketio.emit(...)` | ❌ 直接 socketio.emit（多智能体 idle 消息回显） |
| `server/chat_flow_task_main.py:1488` | `handle_task_with_sender` 内 `from .extensions import socketio` | ⚠️ 导入但未见直接使用（仅靠 sender 参数），属残留导入 |
| `sender` 函数签名贯穿工具循环 | `chat_flow_tool_loop.py:522` `_execute_tool_calls_impl(*, web_terminal, tool_calls, sender, ...)`，约 30+ 处 `sender('...')` | ⚠️ 工具执行循环把「事件发送」以 **sender 回调参数**贯穿（设计上已与具体传输解耦——sender 是可注入的），但**调用方**（models.py:901）绑死了 socketio，等于参数化了接口、没参数化实现 |
| `web_terminal`（WebTerminal 特有属性） | 工具循环参数 `web_terminal`（`_execute_tool_calls_impl:522`），handler 内大量 `getattr(self, ...)` 读取 WebTerminal 特有属性：`task_id`（chat_flow_task_main.py:1531 `getattr(web_terminal, "task_id", None)`）、`multi_agent_mode`、`sub_agent_manager`、`mcp_client_manager`、`background_command_manager`、`custom_tool_executor`、`ocr_client`、`easter_egg_manager`、`host_network_permission`、`current_permission_mode`、`data_dir` 中 `/web/users/` 路径判断（tools_execution.py:2163、:2466、:2482 `_is_web = '/web/users/' in _data_dir`） | ❌ **Runtime/工具实现把「身份是 Web 用户」编进了执行逻辑**（`_is_web` 判定影响 custom-role 目录解析）；`getattr(web_terminal, "task_id", None)` 恒 None（WebTerminal 全仓无 task_id 赋值点，记忆 N2） |
| `web_mode` / `api_client.web_mode` | `web_terminal.py:127-128` | ⚠️ 输出静默是 Web 概念，但包在 `WEB_API_SILENT` 环境变量开关内，非硬耦合 |

### 2.3 结论

- **第 1/2 步已把「传输出口」包装成了 emit_event/run_background + sender 回调注入**，工具循环内部已不出现 `socketio.*` 直接调用（唯一例外 `chat_flow_tool_loop.py:478` 用的也是安全包装 emit_event）。
- **残余硬耦合集中在 4 处直接 `socketio.emit`**：`tasks/models.py:920 / :1054`、`chat_flow_task_main.py:989 / :1125`。它们是「Gateway 独立启动验收」的最后障碍（T12 只查了 `test_request_context` 与 `session[`，未查 `socketio.emit` 裸调用）。
- **执行环境的 Web 语义残留**：`tools_execution.py` 中 `_is_web`（`'/web/users/' in data_dir`）影响 role 目录解析——这是身份/路径耦合，不在纯执行环境范围内，但同属「Runtime 依赖 Web 概念」，第 4 步应一并划界（建议归入 Execution Contract 的 workspace 解析，或至少记录）。

---

## 3. 抽象接口建议（只归纳现状，不发明新能力）

### 3.1 Execution Plane 接口面应覆盖的操作集（全部来自现状调用点）

| # | 操作 | 现状调用点（handler → 后端） | 参数（现状签名） | 关键语义（不许丢） |
|---|---|---|---|---|
| E1 | `run_command(cmd, workdir, timeout, write_access, network)` | tools_execution.py:1936 → terminal_ops/run.py:434 | `command, working_dir, timeout, sandbox_write_access, network_permission` | 返回 `{success,status,output,return_code,truncated,elapsed_ms}`；`status ∈ {completed, timeout, error, cancelled}`；字符上限 MAX_RUN_COMMAND_CHARS |
| E2 | `run_command_background(cmd, timeout, network, write_access)` | tools_execution.py:1916 → background_command_manager.py:39 | `terminal_ops, command, timeout, conversation_id, wait_seconds, network_permission, sandbox_write_access` | 返回 `command_id/status=running_background`；轮询/等待需按 command_id 寻址 |
| E3 | `write_file(path, content, mode)` | tools_execution.py:1638 → crud_mixin.py:242 | `path, content, mode` | 返回 `{path, original_file, new_file}`（编辑摘要依赖 original/new 全文） |
| E4 | `edit_file(path, replacements)` | tools_execution.py:1665 → replace_mixin.py:125 | `path, replacements` | 同上 |
| E5 | `read_file(path, type, start_line, end_line, max_chars, ...)` | tools_read.py:419 → read_mixin.py | `path/type(3 模式)/range/max_chars/size_limit` | 返回 `{path, content, truncated, char_count}`（不含原文全文；type=search/extract 各有参数） |
| E6 | `create_file / delete_file / rename_file / create_folder / delete_folder` | tools_execution.py:1569-1677 → crud_mixin.py | 同名 | 路径相对工作区 |
| E7 | `open_terminal / send_input / snapshot / close / reset / list` | tools_execution.py:1315-1368 → terminal_manager.py | 见 C 表 | 终端只读身份（`docker_readonly_exec` / readonly shell plan）；`broadcast`（IO 事件流出口） |
| E8 | `path_validate(path)`（执行前授权检查） | tools_execution.py:446/489/960 → path_mixin.py:83；chat_flow_tool_loop.py:61/396 | `path` → `(valid, error, full_path, rel_path?)` | 权限模式/沙箱范围/禁读清单（host_execution_mode 参与判定） |
| E9 | 执行环境快照（供子智能体提示词） | `get_execution_mode_state()`（main_terminal.py:448）、`execution_env_text.py` | ― | 注入到子智能体 system prompt 的 execution_mode 说明 |
| E10 | 命令校验/超时钳制 | terminal_ops/command.py:53 `_validate_command` / :76 `_clamp_timeout` | ― | FORBIDDEN_COMMANDS / TERMINAL_COMMAND_TIMEOUT 语义 |

（未列入的「查询/控制面」——memory、todo、approval、workflow、easter_egg、sender 事件——不属于 Execution Plane，它们应留在 Runtime 层。）

### 3.2 现有后端与实现文件对应

| 后端 | 最终执行实现文件 | 入口函数 | 适用工具 |
|---|---|---|---|
| **Docker 容器**（会话句柄 mode=docker） | `modules/terminal_ops/run.py:177-207`（前台）、`modules/background_command_manager.py:232-260`（后台）、`modules/persistent_terminal/start.py:241-413`（终端）、`modules/container_file_proxy.py:448-501`（文件） | `_run_command_subprocess` / `_run_command_thread` / `_start_docker_terminal` / `ContainerFileProxy.run` | 命令、终端、文件 |
| **Docker 只读加固** | `modules/docker_readonly_exec.py:55-190`（uid/gid、exec args、landlock wrap）、`modules/landlock_launcher.py:74-175`（probe_abi/install_readonly_domain/selftest） | `docker_readonly_exec_args` / `docker_readonly_wrap_inner` / `ensure_landlock_ready` | 受限档命令/终端 |
| **宿主机 OS 沙箱** | `modules/host_sandbox_runner.py:174-640`（`host_sandbox_enabled` + 三平台 plan 构建） | `build_host_sandbox_plan`（:197）`build_host_sandbox_readonly_plan`（:213）`build_host_sandbox_shell_plan`（:229） | 命令（前台/后台）、持久终端 shell |
| **宿主机 direct** | `modules/terminal_ops/run.py:255-262`、`persistent_terminal/start.py:227-240`、`file_manager` host 直读写 | `create_subprocess_shell` / `_start_plain_host_terminal` | 命令、终端（unrestricted+direct） |
| **进程内文件操作（host）** | `modules/file_manager/*`（read/crud/replace/patch/list mixin）、`modules/host_sandbox_policy.py:125-170`（禁读/可写清单） | `_validate_path` + `_ensure_host_access` + 各 mixin 方法 | 全部文件工具 |

> 结构性提示（仅归纳）：同一个「执行命令」语义在 **4 个文件里各有一份后端选择代码**（terminal_ops/run.py、background_command_manager.py 的 `_run_command_thread`、persistent_terminal/start.py、container_file_proxy.py），host/docker 分支判定条件互相复制。Execution Plane 接口一旦建立，这 4 处是天然的收敛点（本报告不要求本次重构，仅指出现状）。

---

## 4. 替身执行器接入点评估（内存替身，供 Runtime 测试）

### 前提约束（现状决定接入点形态）

1. 工具 handler 直接持有 `self.terminal_ops` / `self.file_manager` / `self.terminal_manager`（main_terminal.py:123/125/131 构造）；
2. `handle_tool_call` 是**单一工具入口**（tools_execution.py:1057），所有执行分支都在里面；
3. 后台命令**不走 terminal_ops.run_command**，而是 `BackgroundCommandManager._run_command_thread`（background_command_manager.py:185）自己的实现——**只替换 terminal_ops 会漏掉后台 run_command**；
4. `CustomToolExecutor` 只依赖 `terminal_ops.run_command`（构造注入，main_terminal.py:167），替换 terminal_ops 即覆盖；
5. 子智能体工具执行复用主 terminal 的 `handle_tool_call`（sub_agent/manager.py:1019）——**替换主执行链即覆盖子智能体**；
6. 文件工具 = 进程内操作 + `container_session` 代理，file_manager 与 terminal_ops 是**两个独立对象**，替身需分别处理。

### 候选方案

| 方案 | 做法 | 侵入面估计 | 优点 | 缺点 |
|---|---|---|---|---|
| **A. handler 分支注入「执行后端」接口**（最小侵入，推荐） | 在 `MainTerminal`（main_terminal.py 构造区 ~:121）加一个可选属性 `execution_backend`（默认 None）；在 `handle_tool_call` 的 run_command 分支（tools_execution.py:1879-1943，含后台分支 :1909）与文件写分支（:1625 write_file / :1652 edit_file，可选 :1569 create_file/:1589 delete_file/:1604 rename_file/:1677 create_folder）插入 `if self.execution_backend: ... else: 原逻辑` | **改 1 个文件（tools_execution.py）+ 1 个构造点（main_terminal.py）：~6-8 处小分支插入**；需要为替身定义最小接口（E1/E2/E3/E4，约 4 个方法） | 语义最清晰：Runtime 侧显式「执行环境依赖」，替身可同时挡命令+后台+文件写；不改 terminal_ops/background_command_manager 源码；后台命令也覆盖（在分支层拦截） | 需在 handler 内维护分支双轨（真实/替身），有长期漂移风险；文件读（E5）如果也要替身需再加分支 |
| **B. 构造期替换三件套**（对象级替换） | 给 MainTerminal 构造加参数（或测试子类覆写），用 FakeTerminalOps / FakeFileManager / FakeTerminalManager 替换 `self.terminal_ops/file_manager/terminal_manager`（main_terminal.py:123/125/131 三行）；后台命令因在 :1916 透传 `terminal_ops=self.terminal_ops`，FakeTerminalOps 需实现 `_resolve_active_container_session/_validate_command/_resolve_work_path` 等被 BackgroundCommandManager 读取的属性 | **改 1 个文件（main_terminal.py 构造区 3 行 + 可选工厂）+ 需实现 3 个替身类**（接口面较大：TerminalOperator 约 15+ 公开方法、FileManager 约 10+ 方法、TerminalManager 约 8 个）；后台命令**无法被完全拦截**（`_run_command_thread` 只取 terminal_ops 快照后自己执行，需另 patch） | 对 handler 零侵入；贴近真实装配（资源解析同路径） | 替身类面太大、后台命令漏网点；容易「替了个寂寞」 |
| **C. 实例方法级 monkeypatch**（测试夹具式，零源码改动） | pytest fixture / 测试装配中直接替换实例方法：`TerminalOperator.run_command`、`_run_command_subprocess`、`BackgroundCommandManager._run_command_thread`、`FileManager.write_file/replace_many_in_file/create_file/delete_file/rename_file/create_folder` | **0 个源码文件改动**，但需列出 ~8-10 个方法覆盖清单（易漏）；依赖内部方法名稳定性 | 对产品代码零侵入，快速验证 | 脆弱（内部改名即失效）；只覆盖了「当前测到的路径」，后台命令线程路径尤其容易漏 |

### 推荐

**第 4 步首版建议方案 A**（handler 分支注入 `execution_backend`），理由：
- 与「Runtime ↔ Execution Plane 分层」目标同构——替身执行器就是 Execution Plane 的第一个非 Web 实现；
- 覆盖齐全：前台/后台命令、custom_tool（其唯一出口就是 terminal_ops.run_command，被分支层拦截）、子智能体（复用 handle_tool_call）、文件写；
- 侵入面可数：1 个文件（tools_execution.py）+ 1 个构造点（main_terminal.py），约 6-8 个小分支；
- 后续接真实后端（Docker/host/远端）时，替身接口可直接演进为正式 Execution Plane 接口（E1-E4 起步）。

若只想先验证「Runtime 循环不触发真实副作用」而不关心文件工具，可先只拦 run_command 前台+后台 2 个分支（tools_execution.py:1909-1943），侵入面缩到最小（~2 处 + 1 属性）。

---

## 附：关键文件:行号索引

- 工具入口：`core/main_terminal_parts/tools_execution.py:1057`（handle_tool_call）、:391（evaluate_tool_permission）
- 执行器构造：`core/main_terminal.py:121-142`
- Web 广播注入：`core/web_terminal.py:100-152`
- 命令后端：`modules/terminal_ops/run.py:160/434`；后台：`modules/background_command_manager.py:39/185`；自定义：`modules/custom_tool_executor.py:40`
- 终端后端：`modules/terminal_manager.py:212/521`；`modules/persistent_terminal/start.py:72/141/241`；`modules/persistent_terminal/base.py:66`
- 文件后端：`modules/file_manager/base.py:50/77-100`；`modules/container_file_proxy.py:448-501`；`modules/host_sandbox_policy.py:125-170`
- OS 沙箱：`modules/host_sandbox_runner.py:174/197/213/229`；Docker 只读：`modules/docker_readonly_exec.py:55/66/172`；Landlock：`modules/landlock_launcher.py:74/91/138`
- 事件/sender：`server/tasks/models.py:901-920/1045-1054`；`server/chat_flow_task_main.py:989/1125/1511`；`server/chat_flow_tool_loop.py:505/522/828/969/986/1037`；`server/extensions.py:10/25`；`server/context/broadcast.py:16/28`
- 身份/执行环境装配：`server/context/resources.py:221/296/414/450`（host/docker 容器句柄来源）

---

*报告完毕。本报告为纯只读调研产物；所有结论均基于静态阅读与 grep 定位，未经运行时验证。*
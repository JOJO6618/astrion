# Agent 模型工具迁移清单

以 Python 的 [主 Agent 工具组装](../../core/main_terminal_parts/tools_definition/main.py) 和 [子 Agent 工具组装](../../modules/sub_agent/runtime.py) 为分母。Rust 主侧通过 [模型工具菜单](../src/runtime/agent/tool_menu.rs) 构造真实模型请求，由 [循环](../src/runtime/agent.rs) 执行和回填。子侧由 [独立循环](../src/sub_agents/worker.rs) 注册和执行。以下清单记录模型入口。原有 Python 名称例如 `ocr_image` 可作为配置中的保留入口，生产模型对视觉是按能力注册的。

## 主 Agent

| Python 模型工具 | Rust 入口或验收 |
| --- | --- |
| `ask_user`、`submit_plan` | 公共交互模块，生产任务轮询验证问答、审批和返回执行态。 |
| `sleep`、`run_command`、`terminal_session`、`terminal_input`、`terminal_snapshot` | Host 进程资源及对话权限；按平台沙箱能力注册。 |
| `read_file`、`write_file`、`edit_file` | 本机锚定文件工具；回环验证活跃工作树切换后读写根随之变化。 |
| `read_skill`、`search_skills`、`create_skill` | 工作区技能共享读取；归档走 Host 权限审批、私库及工作区镜像。回环验证归档、立即读回和嵌套资源。 |
| `web_search`、`extract_webpage`、`save_webpage` | 搜索与提取和 Chat 共用服务，保存走当前执行根的普通写入权限。回环使用本机 SearXNG 协议与白名单网页。 |
| `create_sub_agent`、`send_message_to_sub_agent`、`get_sub_agent_status`、`stop_sub_agent`、`terminate_sub_agent`、`answer_sub_agent_question` | 子实例监督器、固定权限和 SQLite。本轮生产回环验证创建、查询、复用和通知。 |
| `update_memory`、`recall_project_memory`、`search_project_memory`、`update_project_memory` | Host 账户记忆和按工作区绑定的专题。回环验证全局写、专题创建、同批读取后更新、子侧读取。专题已读版本同工具回执提交。 |
| `conversation_search`、`conversation_review` | 同工作区 SQLite 历史搜索及复用 Markdown 回顾渲染。回环验证搜索排除当前对话、读取上条实际对话。保存走标准工作区权限。 |
| `todo_create`、`todo_update_task` | 共享待办逻辑，主对话及子轮分别持久化。 |
| `manage_personalization` | 只投影 Python 现有个人偏好字段并复用设置锁。回环验证模型更新主题并从实际存储读回。 |
| `vlm_analyze` | 文本模型借助配置的 `OCR_*` OpenAI Chat Completions 兼容端点。回环验证真实 PNG 字节、调用参数与回填文本。 |
| `view_image`、`view_video` | 基于模型的图像、视频能力注册；复用附件 BlobStore，以工具消息保留引用并在工具批次后送入模型。图像有主、子端到端验收；视频按 API 适配器能力传送。 |
| `trigger_easter_egg` | 保持分类默认关闭。完成事件调用已有彩蛋组件并遵守可见性、减少动画和新鲜度；前端构建已通过。 |
| `activate_workflow`、`report_workflow_stage`、`choose_workflow_branch`、`get_workflow_status`、`deactivate_workflow`、`list_workflows`、`save_workflow` | 已注册工作流服务。 |
| `submit_schedule`、`query_schedules`、`manage_schedule` | 复用 Host 正式调度器及执行权限快照。生产回环通过模型提交真实 YAML、查询和手动运行，并等到调度器状态为 success。 |
| `enter_worktree`、`exit_worktree` | 显式启用才注册，单独批次切换。回环验证混合批拒绝、工作树真实读写、退出、切换回执及闲置子实例复用后跟随重绑。已授权区外工作树可用于主侧原生文件能力。进程与子实例保留当前受支持的工作区范围。 |
| `load_tools` | 按需工具受理、常驻 stub、完整定义回执和同批提交。回环验证先拦截、再加载、后真实执行和重复加载失败。 |
| `list_mcp_servers`、`mcp__*` | 各 actor MCP 工具快照，经独立运行时执行，主、子循环已有协议回环验收。 |
| 管理员自定义 ID | 复用 Rust 管理登记。管理员安装的 `execution.py` 通过带类型的参数、当前任务审批与平台沙箱运行。macOS 回环验证纯计算、文本参数与返回模板。 |
| `browser_tabs`、`browser_navigate`、`browser_snapshot`、`browser_find`、`browser_click`、`browser_type`、`browser_fill_form`、`browser_select_option`、`browser_hover`、`browser_drag`、`browser_press_key`、`browser_scroll`、`browser_wait_for`、`browser_take_screenshot`、`browser_handle_dialog`、`browser_file_upload`、`browser_drop`、`browser_evaluate`、`browser_fill_saved_login`、`browser_console_messages`、`browser_network_requests`、`browser_network_request`、`browser_downloads`、`browser_emulate_media` | 由独立内置浏览器迁移接线、验收和报告。 |

## 子 Agent

| Python 模型工具 | Rust 入口或验收 |
| --- | --- |
| `read_file`、`write_file`、`edit_file`、`run_command` | 子实例权限与独立执行域；保留工作目录、工作区和沙箱档位。 |
| `web_search`、`extract_webpage`、`save_webpage` | 与主侧共用服务，写网页仍受子实例固定写根约束。 |
| `read_mediafile` | 固定执行根读取，可持久化的媒体引用，子模型真实收到 PNG。 |
| `read_skill`、`search_skills`、`recall_project_memory`、`search_project_memory` | 工作区共享只读适配；子模型收到已归档技能或专题正文。 |
| `todo_create`、`todo_update_task` | 独立子上下文，回环验证可创建、勾选并读回完成状态。 |
| `ask_master`、`ask_other_agent`、`answer_other_agent`、`get_sub_agent_status` | 同主对话内通信、状态与真实结果回填。 |
| `list_mcp_servers`、`mcp__*` | 独立子 MCP 客户端及各子实例运行权限。 |

实际请求的重点回归在 [Agent 工具夹具](../../test/2026-11-27_RustAgent工具对照/) 和 [网页工具夹具](../../test/2026-11-27_Rust内置网页工具/web_tools_http.py)。各第三方搜索商、真实 OCR 服务、桌面界面和 Linux / Windows 自定义工具实机未纳入这些本机回环。

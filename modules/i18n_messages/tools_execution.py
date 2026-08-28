"""Message pack: tools_execution (core/main_terminal_parts/tools_execution.py).

工具 handler 返回 dict 的用户可见消息（error/message/summary 等，显示在前端工具块）。
纯数据模块：禁止 import modules.i18n；由 modules/i18n.py import 时自动聚合。
插值用 str.format 命名参数：tr("tools_exec.<key>", name=value)。
"""

MESSAGES = {
    # ── MCP / 权限 ──
    "tools_exec.mcp_disabled_docker_mode": {
        "zh-CN": "当前为docker模式，MCP仅支持宿主机模式",
        "en-US": "MCP is only supported in host mode; currently in docker mode",
    },
    "tools_exec.readonly_denied": {
        "zh-CN": "当前处于只读模式，已拒绝会修改工作区或执行高风险操作的工具调用。",
        "en-US": "Read-only mode: tool calls that modify the workspace or perform high-risk operations have been denied.",
    },

    # ── skill / 工作流 ──
    "tools_exec.source_dir_empty": {
        "zh-CN": "source_dir 不能为空",
        "en-US": "source_dir cannot be empty",
    },
    "tools_exec.skills_sync_failed": {
        "zh-CN": "同步当前工作区 skills 失败",
        "en-US": "Failed to sync workspace skills",
    },
    "tools_exec.skill_archived": {
        "zh-CN": "已归档 skill：{skill_name}",
        "en-US": "Skill archived: {skill_name}",
    },
    "tools_exec.workflow_no_conversation": {
        "zh-CN": "当前没有打开的对话，无法激活工作流。",
        "en-US": "No open conversation; cannot activate the workflow.",
    },
    "tools_exec.no_open_conversation": {
        "zh-CN": "当前没有打开的对话。",
        "en-US": "No open conversation.",
    },
    "tools_exec.workflow_none": {
        "zh-CN": "当前没有可用工作流。可阅读 workflow-authoring 技能后用 save_workflow 创建。",
        "en-US": "No workflows available. Read the workflow-authoring skill and use save_workflow to create one.",
    },
    "tools_exec.workflows_count": {
        "zh-CN": "可用工作流（共 {count} 个）：",
        "en-US": "Available workflows ({count} in total):",
    },
    "tools_exec.workflow_builtin": {
        "zh-CN": "内置",
        "en-US": "built-in",
    },
    "tools_exec.workflow_user": {
        "zh-CN": "用户",
        "en-US": "user",
    },
    "tools_exec.workflow_item_line": {
        "zh-CN": "- {name}：{description}（{src}，{node_count} 节点）",
        "en-US": "- {name}: {description} ({src}, {node_count} nodes)",
    },
    "tools_exec.workflow_no_description": {
        "zh-CN": "（无描述）",
        "en-US": "(no description)",
    },
    "tools_exec.workflow_overwritten_note": {
        "zh-CN": "（覆盖已有版本）",
        "en-US": "(overwrote an existing version)",
    },
    "tools_exec.workflow_shadows_builtin_note": {
        "zh-CN": "（用户副本，遮蔽同名内置工作流）",
        "en-US": "(user copy shadowing a built-in workflow of the same name)",
    },
    "tools_exec.workflow_archived_summary": {
        "zh-CN": "已归档工作流：{workflow_name}（{node_count} 节点）{note}。可使用 activate_workflow 激活。",
        "en-US": "Workflow archived: {workflow_name} ({node_count} nodes){note}. Use activate_workflow to activate.",
    },

    # ── 项目记忆 ──
    "tools_exec.memory_name_invalid": {
        "zh-CN": "记忆名称不合法: {name}",
        "en-US": "Invalid memory name: {name}",
    },
    "tools_exec.memory_dir_create_failed": {
        "zh-CN": "创建记忆目录失败: {error}",
        "en-US": "Failed to create memory directory: {error}",
    },
    "tools_exec.memory_file_write_failed": {
        "zh-CN": "写入记忆文件失败: {error}",
        "en-US": "Failed to write memory file: {error}",
    },
    "tools_exec.memory_updated": {
        "zh-CN": "已更新项目记忆: {safe_name}",
        "en-US": "Project memory updated: {safe_name}",
    },
    "tools_exec.memory_created": {
        "zh-CN": "已创建项目记忆: {safe_name}",
        "en-US": "Project memory created: {safe_name}",
    },

    # ── 先读后写 / skill 强约束 ──
    "tools_exec.read_before_edit_error": {
        "zh-CN": "调用 {tool_name} 前必须先使用 read_file 阅读目标文件",
        "en-US": "You must use read_file to read the target file before calling {tool_name}",
    },
    "tools_exec.read_before_edit_message": {
        "zh-CN": "文件 {target_path} 尚未在当前对话中通过 read_file 阅读。请先使用 read_file（read/search/extract 任一模式）读取后再编辑。",
        "en-US": "File {target_path} has not been read via read_file in this conversation. Please read it with read_file (any of read/search/extract modes) before editing.",
    },
    "tools_exec.skill_required_error": {
        "zh-CN": "调用 {tool_name} 前必须先阅读 {required_path}",
        "en-US": "You must read {required_path} before calling {tool_name}",
    },
    "tools_exec.skill_required_message": {
        "zh-CN": "请先使用 read_skill（或 read_file）阅读 {required_path}，随后再调用该工具。",
        "en-US": "Please read {required_path} with read_skill (or read_file) before calling this tool.",
    },

    # ── 参数预检查 / 取消 ──
    "tools_exec.action_cancelled": {
        "zh-CN": "用户取消操作",
        "en-US": "Action cancelled by user",
    },
    "tools_exec.params_too_large": {
        "zh-CN": "参数过大({chars}字符)，超过200KB限制",
        "en-US": "Arguments too large ({chars} chars), exceeding the 200KB limit",
    },
    "tools_exec.params_too_large_suggestion": {
        "zh-CN": "请分块处理或减少参数内容",
        "en-US": "Please process in chunks or reduce the argument size",
    },
    "tools_exec.content_too_long": {
        "zh-CN": "文件内容过长({chars}字符)，超过{limit}字符限制",
        "en-US": "File content too long ({chars} chars), exceeding the {limit} char limit",
    },
    "tools_exec.content_too_long_suggestion": {
        "zh-CN": "请分块写入，或设置 append=true 多次写入",
        "en-US": "Please write in chunks, or set append=true and write multiple times",
    },
    "tools_exec.precheck_failed": {
        "zh-CN": "参数预检查失败: {error}",
        "en-US": "Failed to pre-check arguments: {error}",
    },

    # ── MCP 工具 ──
    "tools_exec.mcp_module_unavailable": {
        "zh-CN": "MCP 模块不可用",
        "en-US": "MCP module unavailable",
    },
    "tools_exec.mcp_not_enabled": {
        "zh-CN": "MCP 功能未启用",
        "en-US": "MCP is not enabled",
    },
    "tools_exec.mcp_manager_unavailable": {
        "zh-CN": "MCP 管理器不可用",
        "en-US": "MCP manager unavailable",
    },

    # ── 图片 / 视频 ──
    "tools_exec.missing_path_param": {
        "zh-CN": "缺少 path 参数",
        "en-US": "Missing path argument",
    },
    "tools_exec.path_empty": {
        "zh-CN": "path 不能为空",
        "en-US": "path cannot be empty",
    },
    "tools_exec.invalid_path_no_workspace": {
        "zh-CN": "非法路径，超出项目根目录，请使用不带/workspace的相对路径",
        "en-US": "Invalid path outside the project root; please use a relative path without /workspace",
    },
    "tools_exec.invalid_path_relative": {
        "zh-CN": "非法路径，超出项目根目录，请使用相对路径",
        "en-US": "Invalid path outside the project root; please use a relative path",
    },
    "tools_exec.image_not_found": {
        "zh-CN": "图片不存在: {path}",
        "en-US": "Image not found: {path}",
    },
    "tools_exec.image_too_large": {
        "zh-CN": "图片过大，需 <= 10MB",
        "en-US": "Image too large; must be <= 10MB",
    },
    "tools_exec.image_unsupported_format": {
        "zh-CN": "不支持的图片格式: {suffix}",
        "en-US": "Unsupported image format: {suffix}",
    },
    "tools_exec.image_attached": {
        "zh-CN": "图片已附加到工具结果中，将随 tool 返回。",
        "en-US": "Image attached to the tool result; it will be returned with the tool.",
    },
    "tools_exec.video_not_found": {
        "zh-CN": "视频不存在: {path}",
        "en-US": "Video not found: {path}",
    },
    "tools_exec.video_unsupported_format": {
        "zh-CN": "不支持的视频格式: {suffix}",
        "en-US": "Unsupported video format: {suffix}",
    },
    "tools_exec.video_too_large": {
        "zh-CN": "视频过大，需 <= 50MB",
        "en-US": "Video too large; must be <= 50MB",
    },
    "tools_exec.video_attached": {
        "zh-CN": "视频已附加到工具结果中，将随 tool 返回。",
        "en-US": "Video attached to the tool result; it will be returned with the tool.",
    },

    # ── 终端会话 ──
    "tools_exec.unknown_action": {
        "zh-CN": "未知操作: {action}",
        "en-US": "Unknown action: {action}",
    },

    # ── sleep 工具 ──
    "tools_exec.sleep_reason_default": {
        "zh-CN": "等待操作完成",
        "en-US": "Waiting for operation to complete",
    },
    "tools_exec.sleep_no_params": {
        "zh-CN": "sleep 至少需要提供一个参数：seconds / wait_sub_agent_ids / wait_sub_agent_output / wait_runcommand_id",
        "en-US": "sleep requires at least one argument: seconds / wait_sub_agent_ids / wait_sub_agent_output / wait_runcommand_id",
    },
    "tools_exec.sleep_params_exclusive": {
        "zh-CN": "sleep 的等待参数互斥：seconds / wait_sub_agent_ids / wait_sub_agent_output / wait_runcommand_id 只能提供一个",
        "en-US": "sleep wait arguments are mutually exclusive: provide exactly one of seconds / wait_sub_agent_ids / wait_sub_agent_output / wait_runcommand_id",
    },
    "tools_exec.agent_output_multi_agent_only": {
        "zh-CN": "wait_sub_agent_output 仅在多智能体模式下可用",
        "en-US": "wait_sub_agent_output is only available in multi-agent mode",
    },
    "tools_exec.sub_agent_manager_unavailable": {
        "zh-CN": "子智能体管理器不可用",
        "en-US": "Sub-agent manager unavailable",
    },
    "tools_exec.no_multi_agent_state": {
        "zh-CN": "当前对话没有多智能体状态",
        "en-US": "The current conversation has no multi-agent state",
    },
    "tools_exec.list_sep": {
        "zh-CN": "、",
        "en-US": ", ",
    },
    "tools_exec.none_placeholder": {
        "zh-CN": "（无）",
        "en-US": "(none)",
    },
    "tools_exec.agent_not_found": {
        "zh-CN": "未找到子智能体「{display_name}」。当前已有: {available}",
        "en-US": "Sub-agent \"{display_name}\" not found. Current: {available}",
    },
    "tools_exec.agent_output_wait_timeout": {
        "zh-CN": "等待 {display_name} 输出超时（5分钟）。可用 send_message_to_sub_agent 重新激活。",
        "en-US": "Timed out waiting for {display_name} output (5 minutes). You can reactivate it with send_message_to_sub_agent.",
    },
    "tools_exec.agent_output_wait_cancelled": {
        "zh-CN": "等待 {display_name} 被取消。该子智能体现在不可用，可用 send_message_to_sub_agent 重新激活。",
        "en-US": "Waiting for {display_name} was cancelled. The sub-agent is now unavailable; reactivate it with send_message_to_sub_agent.",
    },
    "tools_exec.agent_reactivate_hint": {
        "zh-CN": " 可用 send_message_to_sub_agent 重新激活。",
        "en-US": " You can reactivate it with send_message_to_sub_agent.",
    },
    "tools_exec.agent_output_wait_failed": {
        "zh-CN": "等待 {display_name} 输出失败: {error}。可用 send_message_to_sub_agent 重新激活。",
        "en-US": "Failed to wait for {display_name} output: {error}. You can reactivate it with send_message_to_sub_agent.",
    },
    "tools_exec.sleep_wait_ids_not_supported": {
        "zh-CN": "多智能体模式下 sleep 工具不支持 wait_sub_agent_ids，请使用 wait_sub_agent_output。",
        "en-US": "In multi-agent mode the sleep tool does not support wait_sub_agent_ids; use wait_sub_agent_output instead.",
    },
    "tools_exec.wait_ids_nonempty": {
        "zh-CN": "wait_sub_agent_ids 必须是非空数组",
        "en-US": "wait_sub_agent_ids must be a non-empty array",
    },
    "tools_exec.wait_ids_invalid": {
        "zh-CN": "wait_sub_agent_ids 含非法值: {invalid}",
        "en-US": "wait_sub_agent_ids contains invalid values: {invalid}",
    },
    "tools_exec.agent_not_found_by_id": {
        "zh-CN": "未找到对应子智能体: {missing}",
        "en-US": "Matching sub-agent not found: {missing}",
    },
    "tools_exec.waited_agents_done": {
        "zh-CN": "已等待 {count} 个子智能体结束",
        "en-US": "Finished waiting for {count} sub-agents",
    },
    "tools_exec.background_manager_unavailable": {
        "zh-CN": "后台命令管理器不可用",
        "en-US": "Background command manager unavailable",
    },
    "tools_exec.bg_command_not_found": {
        "zh-CN": "未找到后台命令: {command_id}",
        "en-US": "Background command not found: {command_id}",
    },
    "tools_exec.bg_command_wrong_conversation": {
        "zh-CN": "该后台命令不属于当前对话",
        "en-US": "This background command does not belong to the current conversation",
    },
    "tools_exec.bg_runcommand_done": {
        "zh-CN": "后台 run_command 等待完成",
        "en-US": "Background run_command finished waiting",
    },
    "tools_exec.seconds_not_number": {
        "zh-CN": "seconds 必须是数字",
        "en-US": "seconds must be a number",
    },
    "tools_exec.sleep_too_long": {
        "zh-CN": "等待时间过长，最多允许 {max_sleep} 秒",
        "en-US": "Wait time too long; max allowed is {max_sleep} seconds",
    },
    "tools_exec.sleep_too_long_suggestion": {
        "zh-CN": "建议分多次等待或减少等待时间",
        "en-US": "Try waiting in multiple steps or reduce the wait time",
    },
    "tools_exec.sleep_must_be_positive": {
        "zh-CN": "等待时间必须大于0",
        "en-US": "Wait time must be greater than 0",
    },
    "tools_exec.waited_seconds": {
        "zh-CN": "已等待 {seconds} 秒",
        "en-US": "Waited {seconds} seconds",
    },

    # ── 文件工具 ──
    "tools_exec.file_created_empty": {
        "zh-CN": "已创建空文件: {path}。请使用 write_file 写入内容，或使用 edit_file 进行替换。",
        "en-US": "Empty file created: {path}. Use write_file to add content, or edit_file for replacements.",
    },
    "tools_exec.missing_file_path": {
        "zh-CN": "缺少必要参数: file_path",
        "en-US": "Missing required argument: file_path",
    },
    "tools_exec.missing_replacements": {
        "zh-CN": "缺少必要参数: replacements（必须是非空数组）",
        "en-US": "Missing required argument: replacements (must be a non-empty array)",
    },

    # ── web_search / extract_webpage / save_webpage ──
    "tools_exec.search_quota_exhausted": {
        "zh-CN": "搜索配额已用尽，将在 {reset_at} 重置。请向用户说明情况并提供替代方案。",
        "en-US": "Search quota exhausted; it will reset at {reset_at}. Explain this to the user and provide alternatives.",
    },
    "tools_exec.search_failed": {
        "zh-CN": "搜索失败",
        "en-US": "Search failed",
    },
    "tools_exec.webpage_extract_too_long": {
        "zh-CN": "网页提取返回了过长的{char_count}字符，请不要提取这个网页，可以使用网页保存功能，然后使用read工具查找或查看网页",
        "en-US": "Webpage extraction returned too many chars ({char_count}). Do not extract this page; use the webpage save feature, then read/search the saved file",
    },
    "tools_exec.webpage_extract_failed": {
        "zh-CN": "网页提取失败: {error}",
        "en-US": "Failed to extract webpage: {error}",
    },
    "tools_exec.tavily_key_missing": {
        "zh-CN": "Tavily API密钥未配置，无法保存网页",
        "en-US": "Tavily API key is not configured; cannot save the webpage",
    },
    "tools_exec.extract_failed_no_content": {
        "zh-CN": "提取失败，未返回任何内容",
        "en-US": "Extraction failed, no content returned",
    },
    "tools_exec.extract_failed": {
        "zh-CN": "提取失败",
        "en-US": "Extraction failed",
    },
    "tools_exec.extract_result_empty": {
        "zh-CN": "提取成功结果为空，无法保存",
        "en-US": "Extraction returned an empty result; nothing to save",
    },
    "tools_exec.webpage_content_empty": {
        "zh-CN": "网页内容为空，未写入文件",
        "en-US": "Webpage content is empty; file not written",
    },
    "tools_exec.write_file_failed": {
        "zh-CN": "写入文件失败",
        "en-US": "Failed to write file",
    },
    "tools_exec.webpage_saved": {
        "zh-CN": "网页内容已以纯文本保存到 {path}，可用 read_file 的 search/extract 查看，必要时再用终端命令。",
        "en-US": "Webpage content saved as plain text to {path}. Use read_file search/extract to view it, or terminal commands if needed.",
    },
    "tools_exec.webpage_save_failed": {
        "zh-CN": "网页保存失败: {error}",
        "en-US": "Failed to save webpage: {error}",
    },

    # ── run_command ──
    "tools_exec.bg_timeout_required": {
        "zh-CN": "后台模式下 timeout 参数必填且需大于0",
        "en-US": "In background mode the timeout argument is required and must be greater than 0",
    },
    "tools_exec.bg_timeout_max": {
        "zh-CN": "后台模式下 timeout 最大为 3600 秒",
        "en-US": "In background mode timeout is capped at 3600 seconds",
    },
    "tools_exec.timeout_required": {
        "zh-CN": "timeout 参数必填且需大于0",
        "en-US": "The timeout argument is required and must be greater than 0",
    },
    "tools_exec.fg_timeout_max": {
        "zh-CN": "前台模式下 timeout 最大为 120 秒",
        "en-US": "In foreground mode timeout is capped at 120 seconds",
    },
    "tools_exec.result_too_large": {
        "zh-CN": "结果内容过大，有{char_count}字符，请使用限制字符数的获取内容方式，根据程度选择10k以内的数",
        "en-US": "Result too large ({char_count} chars); use a content-fetching mode with a char limit, choosing a value within 10k as appropriate",
    },

    # ── update_memory / 项目记忆工具 ──
    "tools_exec.append_needs_content": {
        "zh-CN": "append 操作需要 content",
        "en-US": "The append operation requires content",
    },
    "tools_exec.replace_needs_valid_index_content": {
        "zh-CN": "replace 操作需要有效的 index 和 content",
        "en-US": "The replace operation requires valid index and content",
    },
    "tools_exec.delete_needs_valid_index": {
        "zh-CN": "delete 操作需要有效的 index",
        "en-US": "The delete operation requires a valid index",
    },
    "tools_exec.recall_needs_name": {
        "zh-CN": "recall_project_memory 需要 name 参数",
        "en-US": "recall_project_memory requires the name argument",
    },
    "tools_exec.search_memory_needs_keywords": {
        "zh-CN": "search_project_memory 需要 keywords 参数（至少 1 个关键词）",
        "en-US": "search_project_memory requires the keywords argument (at least 1 keyword)",
    },
    "tools_exec.update_memory_needs_name": {
        "zh-CN": "update_project_memory 需要 name 参数",
        "en-US": "update_project_memory requires the name argument",
    },
    "tools_exec.update_memory_needs_description": {
        "zh-CN": "update_project_memory 需要 description 参数",
        "en-US": "update_project_memory requires the description argument",
    },
    "tools_exec.update_memory_needs_content": {
        "zh-CN": "update_project_memory 需要 content 参数",
        "en-US": "update_project_memory requires the content argument",
    },

    # ── 对话回顾 ──
    "tools_exec.conversation_search_found": {
        "zh-CN": "找到 {count} 个当前工作区内的历史对话",
        "en-US": "Found {count} historical conversations in the current workspace",
    },
    "tools_exec.conversation_id_empty": {
        "zh-CN": "conversation_id 不能为空",
        "en-US": "conversation_id cannot be empty",
    },
    "tools_exec.review_mode_invalid": {
        "zh-CN": "mode 必须为 read 或 save",
        "en-US": "mode must be read or save",
    },
    "tools_exec.review_conversation_missing": {
        "zh-CN": "对话不存在或不属于当前工作区",
        "en-US": "Conversation does not exist or does not belong to the current workspace",
    },
    "tools_exec.review_returned": {
        "zh-CN": "已直接返回对话回顾内容（{char_count} 字符）",
        "en-US": "Conversation review content returned directly ({char_count} chars)",
    },
    "tools_exec.review_too_long_saved": {
        "zh-CN": "对话回顾内容太长（{char_count} 字符），已保存到文件: {rel_path}，请分段或查找阅读。",
        "en-US": "Conversation review content too long ({char_count} chars); saved to file: {rel_path}. Please read it in sections or search.",
    },
    "tools_exec.review_saved": {
        "zh-CN": "已生成对话回顾文件: {rel_path}",
        "en-US": "Conversation review file generated: {rel_path}",
    },

    # ── 子智能体 ──
    "tools_exec.create_sub_agent_need_role_id": {
        "zh-CN": "多智能体模式下 create_sub_agent 必须指定 role_id",
        "en-US": "In multi-agent mode create_sub_agent must specify role_id",
    },
    "tools_exec.create_sub_agent_no_deliverables": {
        "zh-CN": "多智能体模式下不支持交付目录参数",
        "en-US": "The deliverables directory argument is not supported in multi-agent mode",
    },
    "tools_exec.role_not_found": {
        "zh-CN": "角色不存在: {role_id}",
        "en-US": "Role not found: {role_id}",
    },
    "tools_exec.display_names_nonempty": {
        "zh-CN": "display_names 必须是非空数组（子智能体显示名，如 UI Operator_1）",
        "en-US": "display_names must be a non-empty array (sub-agent display names, e.g. UI Operator_1)",
    },
    "tools_exec.agent_not_exist": {
        "zh-CN": "子智能体不存在",
        "en-US": "Sub-agent does not exist",
    },
    "tools_exec.multi_agent_only": {
        "zh-CN": "该工具仅在多智能体模式下可用",
        "en-US": "This tool is only available in multi-agent mode",
    },
    "tools_exec.multi_agent_state_not_ready": {
        "zh-CN": "多智能体状态未就绪",
        "en-US": "Multi-agent state is not ready",
    },
    "tools_exec.agent_terminated_no_message": {
        "zh-CN": "{display_name} 已被手动终结，无法再接收消息。如需继续工作，请创建新的子智能体。",
        "en-US": "{display_name} has been manually terminated and cannot receive messages. Create a new sub-agent to continue.",
    },
    "tools_exec.agent_not_exist_or_ended": {
        "zh-CN": "{display_name} 不存在或已结束",
        "en-US": "{display_name} does not exist or has ended",
    },
    "tools_exec.custom_agent_fields_required": {
        "zh-CN": "role_id/name/body_prompt 必填",
        "en-US": "role_id/name/body_prompt are required",
    },

    # ── 兜底 / 异常 ──
    "tools_exec.unknown_tool": {
        "zh-CN": "未知工具: {tool_name}",
        "en-US": "Unknown tool: {tool_name}",
    },
    "tools_exec.tool_exec_exception": {
        "zh-CN": "工具执行异常: {error}",
        "en-US": "Tool execution error: {error}",
    },

    # ── 个性化管理 ──
    "tools_exec.pref_enabled_label": {
        "zh-CN": "个性化功能总开关",
        "en-US": "Personalization master switch",
    },
    "tools_exec.pref_self_identify": {
        "zh-CN": "AI自称: {value}",
        "en-US": "AI self-identity: {value}",
    },
    "tools_exec.pref_user_name": {
        "zh-CN": "AI如何称呼用户: {value}",
        "en-US": "How AI addresses the user: {value}",
    },
    "tools_exec.pref_profession": {
        "zh-CN": "用户职业: {value}",
        "en-US": "User profession: {value}",
    },
    "tools_exec.pref_tone": {
        "zh-CN": "交流语气: {value}",
        "en-US": "Communication tone: {value}",
    },
    "tools_exec.pref_considerations": {
        "zh-CN": "注意事项: {value}",
        "en-US": "Considerations: {value}",
    },
    "tools_exec.pref_set": {
        "zh-CN": "已设置",
        "en-US": "set",
    },
    "tools_exec.pref_unset": {
        "zh-CN": "未设置",
        "en-US": "not set",
    },
    "tools_exec.pref_unset_paren": {
        "zh-CN": "(未设置)",
        "en-US": "(not set)",
    },
    "tools_exec.pref_theme": {
        "zh-CN": "主题配色: {value}",
        "en-US": "Theme: {value}",
    },
    "tools_exec.pref_communication_style": {
        "zh-CN": "交流风格: {value}",
        "en-US": "Communication style: {value}",
    },
    "tools_exec.pref_conversation_continuity": {
        "zh-CN": "对话连续性: {value}",
        "en-US": "Conversation continuity: {value}",
    },
    "tools_exec.pref_read_success": {
        "zh-CN": "个性化配置读取成功:\n{details}",
        "en-US": "Personalization config read successfully:\n{details}",
    },
    "tools_exec.pref_read_failed": {
        "zh-CN": "读取配置失败: {error}",
        "en-US": "Failed to read config: {error}",
    },
    "tools_exec.pref_update_needs_field": {
        "zh-CN": "更新操作需要指定 field 参数",
        "en-US": "The update operation requires the field argument",
    },
    "tools_exec.pref_field_not_allowed": {
        "zh-CN": "字段 '{field}' 不允许修改，可修改字段: {allowed_fields}",
        "en-US": "Field '{field}' is not allowed; modifiable fields: {allowed_fields}",
    },
    "tools_exec.pref_must_be_string": {
        "zh-CN": "{field} 必须是字符串",
        "en-US": "{field} must be a string",
    },
    "tools_exec.pref_field_too_long": {
        "zh-CN": "{field} 不能超过 {max_length} 个字符",
        "en-US": "{field} cannot exceed {max_length} characters",
    },
    "tools_exec.pref_considerations_must_be_string": {
        "zh-CN": "considerations 必须是字符串",
        "en-US": "considerations must be a string",
    },
    "tools_exec.pref_considerations_too_long": {
        "zh-CN": "considerations 不能超过 {max_length} 个字符",
        "en-US": "considerations cannot exceed {max_length} characters",
    },
    "tools_exec.pref_theme_must_be_string": {
        "zh-CN": "theme 必须是字符串",
        "en-US": "theme must be a string",
    },
    "tools_exec.pref_theme_invalid": {
        "zh-CN": "theme 必须是以下之一: {themes}",
        "en-US": "theme must be one of: {themes}",
    },
    "tools_exec.pref_comm_style_must_be_string": {
        "zh-CN": "communication_style 必须是字符串",
        "en-US": "communication_style must be a string",
    },
    "tools_exec.pref_comm_style_invalid": {
        "zh-CN": "communication_style 必须是 'default'、'human_like' 或 'auto'",
        "en-US": "communication_style must be 'default', 'human_like', or 'auto'",
    },
    "tools_exec.pref_continuity_must_be_string": {
        "zh-CN": "conversation_continuity 必须是字符串",
        "en-US": "conversation_continuity must be a string",
    },
    "tools_exec.pref_continuity_invalid": {
        "zh-CN": "conversation_continuity 必须是 'high'、'medium' 或 'low'",
        "en-US": "conversation_continuity must be 'high', 'medium', or 'low'",
    },
    "tools_exec.pref_validation_failed": {
        "zh-CN": "验证失败",
        "en-US": "Validation failed",
    },
    "tools_exec.pref_field_updated": {
        "zh-CN": "字段 '{field}' 更新成功",
        "en-US": "Field '{field}' updated successfully",
    },
    "tools_exec.pref_save_failed": {
        "zh-CN": "保存配置失败: {error}",
        "en-US": "Failed to save config: {error}",
    },
    "tools_exec.pref_unknown_action": {
        "zh-CN": "未知的 action: {action}，可选: read, update",
        "en-US": "Unknown action: {action}; available: read, update",
    },
}
"""聊天工具循环/任务主流域的后端 i18n 消息包（server/chat_flow_tool_loop.py、
server/chat_flow_task_main.py、server/deep_compression.py 等聊天管线文件）。

由 modules/i18n.py 自动聚合。纯数据，禁止 import。
"""

MESSAGES = {
    # ── 工具审批预览（_build_tool_approval_preview） ──
    "tool_loop.preview_missing_file_path": {
        "zh-CN": "缺少 file_path",
        "en-US": "Missing file_path",
    },
    "tool_loop.preview_missing_replacements": {
        "zh-CN": "缺少 replacements（必须是非空数组）",
        "en-US": "Missing replacements (must be a non-empty array)",
    },
    "tool_loop.preview_path_invalid": {
        "zh-CN": "路径校验失败",
        "en-US": "Path validation failed",
    },
    "tool_loop.preview_file_not_found": {
        "zh-CN": "目标文件不存在，无法生成上下文预览",
        "en-US": "Target file does not exist; cannot generate a context preview",
    },
    "tool_loop.preview_replace_all_type": {
        "zh-CN": "第 1 组 replace_all 必须是 true 或 false",
        "en-US": "replace_all of group 1 must be true or false",
    },
    "tool_loop.preview_short_old_notice": {
        "zh-CN": "提示：第 1 组 old_string 少于3行，允许继续执行；需要批量替换的场景可以单行或不足一行",
        "en-US": "Note: group 1 old_string has fewer than 3 lines; continuing anyway. Single-line or shorter old_string is allowed for batch replacements.",
    },
    "tool_loop.preview_mode_all": {
        "zh-CN": "全部匹配",
        "en-US": "all matches",
    },
    "tool_loop.preview_mode_first": {
        "zh-CN": "首个匹配",
        "en-US": "first match",
    },
    "tool_loop.preview_summary": {
        "zh-CN": "编辑 {file_path}，共 {count} 组；预览第 1 组第 {start}-{end} 行（{mode}）",
        "en-US": "Edit {file_path}, {count} group(s); previewing group 1, lines {start}-{end} ({mode})",
    },
    "tool_loop.preview_old_not_found": {
        "zh-CN": "共 {count} 组替换；未在文件中定位到第 1 组 old_string，显示原始替换内容",
        "en-US": "{count} replacement group(s); could not locate group 1's old_string in the file, showing the raw replacement content",
    },
    "tool_loop.preview_short_old_suffix": {
        "zh-CN": "（第 1 组 old_string 少于3行，已告知并继续）",
        "en-US": " (group 1 old_string has fewer than 3 lines; notice given, continuing)",
    },
    "tool_loop.preview_failed": {
        "zh-CN": "生成编辑预览失败: {error}",
        "en-US": "Failed to generate edit preview: {error}",
    },
    "tool_loop.preview_run_command": {
        "zh-CN": "执行命令: {command}",
        "en-US": "Run command: {command}",
    },

    # ── 工具审批/拒绝 ──
    "tool_loop.reason_not_provided": {
        "zh-CN": "未提供",
        "en-US": "Not provided",
    },
    "tool_loop.tool_call_rejected": {
        "zh-CN": "工具调用被拒绝\n原因：{reason}",
        "en-US": "Tool call rejected\nReason: {reason}",
    },
    "tool_loop.approval_missing": {
        "zh-CN": "审批请求不存在",
        "en-US": "Approval request not found",
    },
    "tool_loop.approval_user_mismatch": {
        "zh-CN": "审批请求用户不匹配",
        "en-US": "Approval request user mismatch",
    },
    "tool_loop.approval_timeout": {
        "zh-CN": "审批超时",
        "en-US": "Approval timed out",
    },
    "tool_loop.awaiting_approval": {
        "zh-CN": "等待用户审批",
        "en-US": "Waiting for user approval",
    },
    "tool_loop.awaiting_approval_retry": {
        "zh-CN": "检测到权限受限，等待用户审批后重试（审批只授予工作区内写权限）",
        "en-US": "Permission is restricted; waiting for user approval before retrying (approval grants workspace-scoped write only)",
    },
    "tool_loop.awaiting_approval_retry_short": {
        "zh-CN": "检测到权限受限，等待用户审批",
        "en-US": "Permission restricted; waiting for user approval",
    },
    "tool_loop.rejected_by_user": {
        "zh-CN": "操作被用户拒绝",
        "en-US": "Operation rejected by the user",
    },
    "tool_loop.rejected_timeout": {
        "zh-CN": "审批超时，操作未执行",
        "en-US": "Approval timed out; the operation was not executed",
    },
    "tool_loop.retry_still_denied_read_scope": {
        "zh-CN": "审批已授予本次工作区内写权限，但命令仍被沙箱拒绝：很可能是读取了授权范围（工作区 / 路径授权 / 系统基础路径）之外的路径。读越界不随审批放行，请引导用户在「路径授权」中添加所需路径后重试。",
        "en-US": "Approval granted workspace-scoped write for this command only, but the sandbox still rejected it: it most likely tried to read paths outside the authorized scope (workspace / path authorization / base system paths). Read escalation is not granted by approval; ask the user to add the needed path via Path Authorization and retry.",
    },

    # ── ask_user ──
    "tool_loop.question_missing": {
        "zh-CN": "用户问题不存在。",
        "en-US": "The user question no longer exists.",
    },
    "tool_loop.question_user_mismatch": {
        "zh-CN": "用户问题所属用户不匹配。",
        "en-US": "The user question belongs to a different user.",
    },
    "tool_loop.question_timeout": {
        "zh-CN": "等待用户回答超时。",
        "en-US": "Timed out waiting for the user's answer.",
    },
    "tool_loop.user_no_answer": {
        "zh-CN": "用户未回答。",
        "en-US": "No answer from the user.",
    },
    "tool_loop.awaiting_user_answer": {
        "zh-CN": "等待用户回答",
        "en-US": "Waiting for the user's answer",
    },

    # ── submit_plan（计划批准流） ──
    "tool_loop.submit_plan_not_in_plan_mode": {
        "zh-CN": "当前不在计划模式，submit_plan 不可用。若需要用户确认方案，请直接在回复中输出讨论内容。",
        "en-US": "Not currently in plan mode; submit_plan is unavailable. If you need user confirmation, present the discussion directly in your reply.",
    },
    "tool_loop.submit_plan_missing_file": {
        "zh-CN": "缺少 plan_file 参数。请先把计划写入 .astrion/plan/ 下的 .md 文件再提交。",
        "en-US": "Missing plan_file parameter. Write the plan to a .md file under .astrion/plan/ first, then submit.",
    },
    "tool_loop.submit_plan_invalid_path": {
        "zh-CN": "计划文件路径无效：{error}",
        "en-US": "Invalid plan file path: {error}",
    },
    "tool_loop.submit_plan_wrong_dir": {
        "zh-CN": "计划文件必须位于工作区 .astrion/plan/ 目录下。",
        "en-US": "The plan file must be located in the workspace .astrion/plan/ directory.",
    },
    "tool_loop.submit_plan_not_md": {
        "zh-CN": "计划文件必须是 .md 文档。",
        "en-US": "The plan file must be a .md document.",
    },
    "tool_loop.submit_plan_file_missing": {
        "zh-CN": "计划文件不存在：{plan_file}。请先写入计划文档再提交。",
        "en-US": "Plan file not found: {plan_file}. Write the plan document first, then submit.",
    },
    "tool_loop.submit_plan_read_failed": {
        "zh-CN": "读取计划文件失败：{error}",
        "en-US": "Failed to read the plan file: {error}",
    },
    "tool_loop.submit_plan_empty": {
        "zh-CN": "计划文档还是空的，请先写入计划内容再提交。",
        "en-US": "The plan document is still empty. Write the plan content first, then submit.",
    },
    "tool_loop.awaiting_plan_approval": {
        "zh-CN": "等待用户批准计划",
        "en-US": "Waiting for the user to approve the plan",
    },
    "tool_loop.plan_switch_note_ok": {
        "zh-CN": "系统已自动切换到执行模式，权限已解除只读锁定。",
        "en-US": "The system has automatically switched to execute mode; the read-only permission lock has been lifted.",
    },
    "tool_loop.plan_switch_note_failed": {
        "zh-CN": "（切换到执行模式失败：{error}，可请用户手动切换运行模式）",
        "en-US": "(Failed to switch to execute mode: {error}; you may ask the user to switch the work mode manually)",
    },
    "tool_loop.plan_approved": {
        "zh-CN": "用户已批准你的计划。{switch_note}请立即按照计划开始实施。",
        "en-US": "The user has approved your plan. {switch_note}Start implementing it now.",
    },
    "tool_loop.plan_approved_comment": {
        "zh-CN": "\n用户批准时附带的意见：{comment}",
        "en-US": "\nComment from the user on approval: {comment}",
    },
    "tool_loop.plan_rejected": {
        "zh-CN": "用户拒绝了这份计划，你仍处于计划模式。请根据用户的意见修订计划文档后重新调用 submit_plan 提交。",
        "en-US": "The user rejected this plan; you are still in plan mode. Revise the plan document based on the user's feedback and submit again via submit_plan.",
    },
    "tool_loop.plan_rejected_comment": {
        "zh-CN": "\n用户的意见：{comment}",
        "en-US": "\nUser feedback: {comment}",
    },
    "tool_loop.plan_rejected_no_comment": {
        "zh-CN": "\n用户没有填写具体意见，可在回复中询问用户需要调整的方向。",
        "en-US": "\nThe user did not provide specific feedback; you may ask in your reply which direction to adjust.",
    },
    "tool_loop.plan_no_decision": {
        "zh-CN": "计划批准未得到用户决定（可能已超时或请求丢失），可重新调用 submit_plan 提交。",
        "en-US": "No user decision was received for the plan approval (possibly timed out or the request was lost); you may submit again via submit_plan.",
    },
    "tool_loop.workflow_tool_error": {
        "zh-CN": "工作流工具执行异常：{error}",
        "en-US": "Workflow tool execution error: {error}",
    },

    # ── 工具执行状态/取消 ──
    "tool_loop.cancelled_by_user": {
        "zh-CN": "命令执行被用户取消",
        "en-US": "Command execution cancelled by the user",
    },
    "tool_loop.tool_no_result": {
        "zh-CN": "工具未返回结果",
        "en-US": "The tool returned no result",
    },
    "tool_loop.tool_not_allowed": {
        "zh-CN": "工具 {tool} 不在当前模型可用工具列表中，已拒绝执行。",
        "en-US": "Tool {tool} is not in the list of tools available to the current model; execution refused.",
    },
    "tool_loop.permission_denied_default": {
        "zh-CN": "当前权限模式不允许执行该工具。",
        "en-US": "The current permission mode does not allow executing this tool.",
    },
    "tool_loop.image_path_recorded": {
        "zh-CN": "系统已记录图片路径（不再附带二进制数据）: {path}",
        "en-US": "Image path recorded by the system (binary data no longer attached): {path}",
    },
    "tool_loop.video_path_recorded": {
        "zh-CN": "系统已记录视频路径（不再附带二进制数据）: {path}",
        "en-US": "Video path recorded by the system (binary data no longer attached): {path}",
    },
    "tool_loop.deep_compression_failed": {
        "zh-CN": "自动深层压缩失败",
        "en-US": "Automatic deep compression failed",
    },

    # ── 任务主流（chat_flow_task_main） ──
    "task_main.sub_agent_done_line": {
        # i18n-match：前端 history.ts / ui/shared.ts 对子智能体完成通知有识别正则
        "zh-CN": "子智能体{agent_id} ({summary}) 已完成任务。",
        "en-US": "Sub-agent {agent_id} ({summary}) has completed its task.",
    },
    "task_main.deliverables_line": {
        "zh-CN": "交付目录：{dir}",
        "en-US": "Deliverables: {dir}",
    },
    "task_main.context_overflow": {
        "zh-CN": "当前对话上下文已达 {current} tokens，超过模型上限 {max}，请先使用压缩功能或清理对话后再试。",
        "en-US": "This conversation's context has reached {current} tokens, exceeding the model limit of {max}. Please use compression or clear the conversation before retrying.",
    },
    "task_main.context_overflow_title": {
        "zh-CN": "上下文过长",
        "en-US": "Context too long",
    },
    "task_main.context_usage_hint": {
        "zh-CN": "当前对话上下文约占 {percent}%（{current}/{max}），建议使用压缩功能。",
        "en-US": "This conversation is using about {percent}% of its context ({current}/{max}); compression is recommended.",
    },
    "task_main.task_stopped": {
        "zh-CN": "任务已停止",
        "en-US": "Task stopped",
    },
    "task_main.max_tool_calls_reached": {
        "zh-CN": "⚠️ 已达到最大工具调用次数限制 ({limit})，任务结束。",
        "en-US": "⚠️ Maximum tool call limit reached ({limit}); task ended.",
    },
    "task_main.quota_exceeded": {
        "zh-CN": "配额已达到上限，暂时无法继续调用模型。",
        "en-US": "Quota limit reached; model calls are temporarily unavailable.",
    },
    "task_main.auto_fix_instruction": {
        "zh-CN": "你使用了错误的格式输出工具调用。请使用正确的工具调用格式而不是直接输出JSON。根据当前进度继续执行任务。",
        "en-US": "You output a tool call in the wrong format. Use the proper tool call format instead of raw JSON, and continue the task from the current progress.",
    },
    "task_main.auto_fix_notice": {
        "zh-CN": "⚠️ 自动修复: {message}",
        "en-US": "⚠️ Auto-fix: {message}",
    },
    "task_main.auto_fix_failed": {
        "zh-CN": "⌘ 工具调用格式错误，自动修复失败。请手动检查并重试。",
        "en-US": "⌘ Tool call format error; auto-fix failed. Please check manually and retry.",
    },
    "task_main.repeat_tool_warning": {
        "zh-CN": "⚠️ 检测到重复调用 {tool} 工具 {limit} 次，可能存在循环。",
        "en-US": "⚠️ Detected {limit} consecutive calls to {tool}; a loop may be occurring.",
    },
    "task_main.repeat_tool_terminated": {
        "zh-CN": "⌘ 工具 {tool} 重复调用过多，任务终止。",
        "en-US": "⌘ Tool {tool} was called too many times; task terminated.",
    },

    # ── 深层压缩（deep_compression） ──
    "deep_compression.conversation_not_found": {
        "zh-CN": "对话不存在: {conversation_id}",
        "en-US": "Conversation not found: {conversation_id}",
    },
    "deep_compression.in_progress": {
        "zh-CN": "对话正在压缩中",
        "en-US": "The conversation is being compressed",
    },
    "deep_compression.load_failed": {
        "zh-CN": "加载目标对话失败: {error}",
        "en-US": "Failed to load the target conversation: {error}",
    },
    "deep_compression.empty_model_content": {
        "zh-CN": "模型返回空内容",
        "en-US": "The model returned empty content",
    },
    "deep_compression.unknown_reason": {
        "zh-CN": "未知原因",
        "en-US": "unknown reason",
    },
    "deep_compression.summary_failed": {
        "zh-CN": "生成总结失败（{reason}）",
        "en-US": "Failed to generate the summary ({reason})",
    },
    "deep_compression.summary_failed_notice": {
        "zh-CN": "自动压缩总结失败，将使用失败占位文本：{reason}",
        "en-US": "Automatic compression summary failed; fallback placeholder text will be used: {reason}",
    },
    "deep_compression.marks_save_failed": {
        "zh-CN": "压缩标记保存失败：{error}",
        "en-US": "Failed to save compression marks: {error}",
    },
    "deep_compression.stats_reset_failed": {
        "zh-CN": "压缩后重置上下文统计失败：{error}",
        "en-US": "Failed to reset context statistics after compression: {error}",
    },
}

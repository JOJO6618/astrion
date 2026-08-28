"""Sub-agent (子智能体) domain message pack for backend i18n.

Covers user-visible messages migrated from modules/sub_agent/:
- manager.py  -> key prefix `sub_agent_mgr.`
- task.py     -> key prefix `sub_agent_task.`
- tools.py    -> key prefix `sub_agent_tools.`
- state.py    -> key prefix `sub_agent_state.` (residual, after earlier migration)
"""

MESSAGES = {
    # ── modules/sub_agent/manager.py ──
    "sub_agent_mgr.missing_thinking_mode": {
        "zh-CN": "缺少 thinking_mode 参数，必须指定 fast 或 thinking",
        "en-US": "Missing thinking_mode parameter; must be fast or thinking",
    },
    "sub_agent_mgr.thinking_mode_invalid": {
        "zh-CN": "thinking_mode 仅支持 fast 或 thinking",
        "en-US": "thinking_mode only supports fast or thinking",
    },
    "sub_agent_mgr.missing_conversation_id": {
        "zh-CN": "缺少对话ID，无法创建子智能体",
        "en-US": "Missing conversation ID; cannot create sub-agent",
    },
    "sub_agent_mgr.instance_slot_conflict": {
        "zh-CN": "内部错误：实例编号分配冲突，请重试创建。",
        "en-US": "Internal error: instance ID allocation conflict, please retry.",
    },
    "sub_agent_mgr.agent_id_used": {
        "zh-CN": "该对话已使用过编号 {agent_id}，请更换新的子智能体代号。",
        "en-US": "ID {agent_id} is already used in this conversation; pick a new sub-agent ID.",
    },
    "sub_agent_mgr.max_active_reached": {
        "zh-CN": "该对话已存在 {count} 个运行中的子智能体，请稍后再试。",
        "en-US": "This conversation already has {count} running sub-agents; try again later.",
    },
    "sub_agent_mgr.instance_register_conflict": {
        "zh-CN": "内部错误：实例注册冲突，请重试创建。",
        "en-US": "Internal error: instance registration conflict, please retry.",
    },
    "sub_agent_mgr.schedule_failed": {
        "zh-CN": "子智能体调度失败（事件循环繁忙），请稍后重试: {error}",
        "en-US": "Failed to schedule the sub-agent (event loop busy), please retry later: {error}",
    },
    "sub_agent_mgr.created": {
        "zh-CN": "子智能体{agent_id} 已创建，任务ID: {task_id}",
        "en-US": "Sub-agent {agent_id} created, task ID: {task_id}",
    },
    "sub_agent_mgr.created_multi": {
        "zh-CN": "{display_name} 已创建。",
        "en-US": "{display_name} created.",
    },
    "sub_agent_mgr.task_not_found": {
        "zh-CN": "未找到对应的子智能体任务",
        "en-US": "No matching sub-agent task found",
    },
    "sub_agent_mgr.already_finished": {
        "zh-CN": "子智能体已结束。",
        "en-US": "The sub-agent has finished.",
    },
    "sub_agent_mgr.finished_no_result": {
        "zh-CN": "子智能体已结束，但未获取到结果。",
        "en-US": "The sub-agent has finished, but no result was obtained.",
    },
    "sub_agent_mgr.pause_only_multi_agent": {
        "zh-CN": "stop_sub_agent 仅在多智能体模式下可用",
        "en-US": "stop_sub_agent is only available in multi-agent mode",
    },
    "sub_agent_mgr.terminated_cannot_pause": {
        "zh-CN": "子智能体已被终结，无法暂停",
        "en-US": "The sub-agent has been terminated and cannot be paused",
    },
    "sub_agent_mgr.pause_failed": {
        "zh-CN": "暂停子智能体失败: {error}",
        "en-US": "Failed to pause the sub-agent: {error}",
    },
    "sub_agent_mgr.paused_message": {
        "zh-CN": "{display_name} 已暂停，可用 send_message_to_sub_agent 重新激活。",
        "en-US": "{display_name} paused; use send_message_to_sub_agent to reactivate.",
    },
    "sub_agent_mgr.force_closed": {
        "zh-CN": "子智能体已被强制关闭。",
        "en-US": "The sub-agent has been force-closed.",
    },
    "sub_agent_mgr.force_closed_sysmsg": {
        "zh-CN": "🛑 {display_name} 已被手动关闭。",
        "en-US": "🛑 {display_name} has been manually closed.",
    },
    "sub_agent_mgr.agent_generic": {
        "zh-CN": "子智能体",
        "en-US": "Sub-agent",
    },
    "sub_agent_mgr.terminated_snapshot_summary": {
        "zh-CN": "{name} 已被手动关闭。",
        "en-US": "{name} has been manually closed.",
    },
    "sub_agent_mgr.need_agent_id": {
        "zh-CN": "必须指定至少一个agent_id",
        "en-US": "At least one agent_id must be specified",
    },
    "sub_agent_mgr.agent_not_found": {
        "zh-CN": "子智能体不存在",
        "en-US": "Sub-agent does not exist",
    },
    "sub_agent_mgr.unknown_todo_tool": {
        "zh-CN": "未知待办工具: {tool_name}",
        "en-US": "Unknown todo tool: {tool_name}",
    },
    "sub_agent_mgr.no_terminal": {
        "zh-CN": "子智能体管理器未绑定终端，无法执行工具",
        "en-US": "Sub-agent manager is not bound to a terminal; cannot execute tools",
    },
    "sub_agent_mgr.tool_exec_exception": {
        "zh-CN": "工具执行异常: {error}",
        "en-US": "Tool execution error: {error}",
    },

    # ── modules/sub_agent/task.py ──
    "sub_agent_task.execution_error": {
        "zh-CN": "执行异常: {error}",
        "en-US": "Execution error: {error}",
    },
    "sub_agent_task.max_turns_exceeded": {
        "zh-CN": "任务执行超过最大轮次限制",
        "en-US": "The task exceeded the maximum turn limit",
    },
    "sub_agent_task.model_call_failed_idle": {
        "zh-CN": "⚠️ 模型请求连续 {count} 次失败（网络或 API 异常）：{error}。本轮任务无法继续，我已进入空闲状态，请检查网络/模型服务后重新给我下达指令。",
        "en-US": "⚠️ Model requests failed {count} consecutive times (network or API error): {error}. This turn cannot continue; I have entered idle state. Please check the network/model service and give me new instructions.",
    },
    "sub_agent_task.model_output_interrupted": {
        "zh-CN": "⚠️ 模型输出中断（收到部分内容后连接断开）：{error}。本轮任务失败。",
        "en-US": "⚠️ Model output interrupted (connection dropped after partial content): {error}. This turn failed.",
    },
    "sub_agent_task.report_failed_no_reason": {
        "zh-CN": "子智能体报告执行失败，未说明原因",
        "en-US": "The sub-agent reported failure without explaining why",
    },
    "sub_agent_task.timeout_incomplete": {
        "zh-CN": "任务超时未完成",
        "en-US": "The task timed out before completion",
    },
    "sub_agent_task.manual_terminated_summary": {
        "zh-CN": "子智能体已被手动终止",
        "en-US": "The sub-agent has been manually terminated",
    },

    # ── modules/sub_agent/tools.py ──
    "sub_agent_tools.path_required": {
        "zh-CN": "path 不能为空",
        "en-US": "path must not be empty",
    },
    "sub_agent_tools.invalid_path": {
        "zh-CN": "非法路径，超出项目根目录",
        "en-US": "Invalid path, outside the project root",
    },
    "sub_agent_tools.file_not_found": {
        "zh-CN": "文件不存在: {path}",
        "en-US": "File not found: {path}",
    },
    "sub_agent_tools.forbidden_file_type": {
        "zh-CN": "禁止的文件类型",
        "en-US": "File type is not allowed",
    },

    # ── modules/sub_agent/state.py（残留） ──
    "sub_agent_state.output_parse_failed": {
        "zh-CN": "输出文件解析失败: {error}",
        "en-US": "Failed to parse the output file: {error}",
    },
    "sub_agent_state.terminated": {
        "zh-CN": "子智能体已被终结",
        "en-US": "The sub-agent has been terminated",
    },
    "sub_agent_state.max_turns_exceeded": {
        "zh-CN": "任务执行超过最大轮次限制。{summary}",
        "en-US": "The task exceeded the maximum turn limit. {summary}",
    },
    "sub_agent_state.crashed_snapshot": {
        "zh-CN": "子智能体异常退出，未写入最终执行结果",
        "en-US": "The sub-agent exited abnormally without writing a final result",
    },
    "sub_agent_state.cancelled_snapshot": {
        "zh-CN": "子智能体任务被取消，未写入最终执行结果",
        "en-US": "The sub-agent task was cancelled without writing a final result",
    },
    "sub_agent_state.crashed_with_detail": {
        "zh-CN": "子智能体异常退出：{detail}",
        "en-US": "The sub-agent exited abnormally: {detail}",
    },
    "sub_agent_state.stale_snapshot": {
        "zh-CN": "子智能体输出停留在运行中快照且已 {seconds} 秒未更新，疑似进程中断或任务崩溃",
        "en-US": "The sub-agent output stayed at a running snapshot for {seconds} seconds without updates; the process may have been interrupted or the task crashed",
    },
    "sub_agent_state.zombie_cleanup_message": {
        "zh-CN": "子智能体疑似僵尸任务，已超时自动清理运行状态。",
        "en-US": "The sub-agent looks like a zombie task; its running state was auto-cleaned after timeout.",
    },
    "sub_agent_state.zombie_cleanup_sysmsg": {
        "zh-CN": "⚠️ 子智能体长时间未结束，系统已自动清理运行状态。",
        "en-US": "⚠️ The sub-agent did not finish for a long time; the system auto-cleaned its running state.",
    },
    "sub_agent_state.exited_cleanup_message": {
        "zh-CN": "检测到子智能体任务已退出，已自动清理运行状态。",
        "en-US": "The sub-agent task was detected as exited; its running state was auto-cleaned.",
    },
    "sub_agent_state.exited_cleanup_sysmsg": {
        "zh-CN": "⚠️ 子智能体任务异常退出，系统已自动清理运行状态。",
        "en-US": "⚠️ The sub-agent task exited abnormally; the system auto-cleaned its running state.",
    },
}
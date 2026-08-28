"""Backend i18n message pack: terminal infrastructure user-visible messages.

Covers modules/terminal_manager.py, modules/background_command_manager.py,
modules/terminal_ops/run.py, modules/terminal_ops/command.py and
modules/persistent_terminal/command.py. Pure data module — do not import
anything here. Auto-discovered and merged by modules/i18n.py at import time.
zh-CN copy is verbatim from source; en-US is concise product-level English
(sentence case).
"""

MESSAGES = {
    # ── modules/terminal_manager.py ──
    "terminal.session_exists": {
        "zh-CN": "终端会话 '{session_name}' 已存在",
        "en-US": "Terminal session '{session_name}' already exists",
    },
    "terminal.max_terminals_reached": {
        "zh-CN": "已达到最大终端数量限制 ({max_terminals})",
        "en-US": "Maximum terminal count reached ({max_terminals})",
    },
    "terminal.close_extra_session_hint": {
        "zh-CN": "请先关闭一个终端会话",
        "en-US": "Please close a terminal session first",
    },
    "terminal.start_failed": {
        "zh-CN": "终端启动失败",
        "en-US": "Failed to start terminal",
    },
    "terminal.session_not_found": {
        "zh-CN": "终端会话 '{session_name}' 不存在",
        "en-US": "Terminal session '{session_name}' does not exist",
    },
    "terminal.no_active_session": {
        "zh-CN": "没有活动终端会话",
        "en-US": "No active terminal session",
    },
    "terminal.open_session_hint": {
        "zh-CN": "请先使用 terminal_session 打开一个终端",
        "en-US": "Please open a terminal with terminal_session first",
    },
    "terminal.reset_failed_restart": {
        "zh-CN": "终端会话 '{session_name}' 重置失败：无法重新启动进程",
        "en-US": "Failed to reset terminal session '{session_name}': could not restart the process",
    },
    "terminal.reset_success": {
        "zh-CN": "终端会话已重置并重新启动",
        "en-US": "Terminal session reset and restarted",
    },
    "terminal.output_wait_not_number": {
        "zh-CN": "output_wait 参数必须是数字",
        "en-US": "output_wait must be a number",
    },
    "terminal.output_wait_invalid": {
        "zh-CN": "output_wait 参数无效",
        "en-US": "Invalid output_wait parameter",
    },
    "terminal.output_wait_required": {
        "zh-CN": "output_wait 参数必填且需大于0",
        "en-US": "output_wait is required and must be greater than 0",
    },
    "terminal.output_wait_missing": {
        "zh-CN": "output_wait 参数缺失",
        "en-US": "output_wait parameter is missing",
    },
    "terminal.output_truncated_note": {
        "zh-CN": "输出已截断，仅返回了末尾的 {char_limit} 个字符",
        "en-US": "Output truncated; only the last {char_limit} characters were returned",
    },

    # ── modules/background_command_manager.py ──
    "terminal.timeout_required": {
        "zh-CN": "timeout 参数必填且需大于0",
        "en-US": "timeout is required and must be greater than 0",
    },
    "terminal.timeout_missing": {
        "zh-CN": "timeout 参数缺失",
        "en-US": "timeout parameter is missing",
    },
    "terminal.work_dir_outside_project": {
        "zh-CN": "工作目录必须在项目文件夹内",
        "en-US": "Working directory must be inside the project folder",
    },
    "terminal.record_lost": {
        "zh-CN": "后台任务记录丢失",
        "en-US": "Background task record lost",
    },
    "terminal.finished_within_5s": {
        "zh-CN": "命令在5秒内完成，未创建后台任务",
        "en-US": "Command completed within 5 seconds; no background task created",
    },
    "terminal.background_created_with_output": {
        "zh-CN": "后台命令已创建；以下为当前已捕获输出。",
        "en-US": "Background command created; here is the output captured so far.",
    },
    "terminal.exec_failed_code": {
        "zh-CN": "命令执行失败 (返回码: {code})",
        "en-US": "Command failed (exit code: {code})",
    },
    "terminal.exec_timeout_seconds": {
        "zh-CN": "命令执行超时 ({timeout}秒)",
        "en-US": "Command timed out ({timeout}s)",
    },
    "terminal.exec_failed_generic": {
        "zh-CN": "执行失败: {error}",
        "en-US": "Execution failed: {error}",
    },
    "terminal.bg_stale_timeout_cleaned": {
        "zh-CN": "后台指令运行超时，已自动清理运行状态。",
        "en-US": "The background command timed out; its running state was cleaned up automatically.",
    },
    "terminal.bg_stale_exited_cleaned": {
        "zh-CN": "检测到后台指令进程已退出，已自动清理运行状态。",
        "en-US": "The background command process has exited; its running state was cleaned up automatically.",
    },
    "terminal.command_id_required": {
        "zh-CN": "command_id 不能为空",
        "en-US": "command_id cannot be empty",
    },
    "terminal.background_command_not_found": {
        "zh-CN": "未找到后台命令: {command_id}",
        "en-US": "Background command not found: {command_id}",
    },
    "terminal.background_command_finished": {
        "zh-CN": "后台命令已结束",
        "en-US": "Background command has ended",
    },
    "terminal.background_cancelled_manual": {
        "zh-CN": "后台命令已手动停止",
        "en-US": "Background command stopped manually",
    },
    "terminal.background_cancel_requested": {
        "zh-CN": "后台命令停止请求已发送",
        "en-US": "Background command stop request sent",
    },
    "terminal.wait_bg_timeout": {
        "zh-CN": "等待后台命令完成超时",
        "en-US": "Timed out waiting for the background command to finish",
    },

    # ── modules/terminal_ops/run.py / command.py ──
    "terminal.host_sandbox_disabled": {
        "zh-CN": "宿主机命令执行被拒绝：HOST_SANDBOX_ENABLED=0",
        "en-US": "Host command execution rejected: HOST_SANDBOX_ENABLED=0",
    },
    "terminal.host_sandbox_unavailable": {
        "zh-CN": "宿主机沙箱不可用，拒绝执行: {error}",
        "en-US": "Host sandbox unavailable; execution refused: {error}",
    },
    "terminal.output_too_large": {
        "zh-CN": "结果内容过大，有{char_count}字符，请使用限制字符数的获取内容方式，根据程度选择10k以内的数",
        "en-US": "Result too large ({char_count} characters). Please use a content-fetching method with a character limit and choose a number within 10k as appropriate",
    },
    "terminal.command_cancelled": {
        "zh-CN": "命令执行被用户取消",
        "en-US": "Command execution cancelled by user",
    },
    "terminal.forbidden_command": {
        "zh-CN": "用户不允许执行包含“{pattern}”的指令",
        "en-US": "You are not allowed to run commands containing \"{pattern}\"",
    },
    "terminal.command_failed": {
        "zh-CN": "命令执行失败",
        "en-US": "Command execution failed",
    },

    # ── modules/persistent_terminal/command.py ──
    "terminal.not_running": {
        "zh-CN": "终端未运行，请先打开终端会话。",
        "en-US": "Terminal is not running. Please open a terminal session first.",
    },
    "terminal.input_failed": {
        "zh-CN": "终端已不可用或输入失败，请重新打开终端会话。",
        "en-US": "Terminal is unavailable or input failed. Please reopen the terminal session.",
    },
    "terminal.cmd_completed": {
        "zh-CN": "命令执行完成",
        "en-US": "Command executed successfully",
    },
    "terminal.cmd_no_output": {
        "zh-CN": "未捕获输出，命令可能未产生结果",
        "en-US": "No output captured; the command may have produced no result",
    },
    "terminal.cmd_awaiting_input": {
        "zh-CN": "命令已发送，终端等待进一步输入或仍在运行",
        "en-US": "Command sent; the terminal is waiting for more input or still running",
    },
    "terminal.cmd_echo_loop": {
        "zh-CN": "检测到终端正在回显输入，命令可能未成功执行",
        "en-US": "Detected the terminal echoing input; the command may not have executed successfully",
    },
    "terminal.cmd_output_with_echo": {
        "zh-CN": "命令产生输出，但终端疑似重复回显",
        "en-US": "Command produced output, but the terminal appears to echo it repeatedly",
    },
    "terminal.cmd_wait_timeout": {
        "zh-CN": "输出等待达到上限（{timeout}秒）",
        "en-US": "Output wait reached the limit ({timeout}s)",
    },
    "terminal.collected_output_note": {
        "zh-CN": "[已收集约{timeout}秒内的输出]",
        "en-US": "[Collected about {timeout} seconds of output]",
    },
    "terminal.output_truncated_appendix": {
        "zh-CN": "（输出已截断，保留末尾{chars}字符）",
        "en-US": " (output truncated; kept the last {chars} characters)",
    },
    "terminal.send_failed": {
        "zh-CN": "发送命令失败: {error}",
        "en-US": "Failed to send command: {error}",
    },
    "terminal.display_truncated_prefix": {
        "zh-CN": "[输出已截断，显示最后{chars}字符]",
        "en-US": "[Output truncated; showing the last {chars} characters]",
    },
}
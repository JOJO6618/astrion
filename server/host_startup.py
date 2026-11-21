"""Startup shared by the full Host server and the CLI headless server."""
from __future__ import annotations

import threading
from datetime import datetime

from flask import Flask

from config import AUTO_FIX_TOOL_CALL, MAX_ITERATIONS_PER_TASK, MAX_TOTAL_TOOL_CALLS
from server.utils_common import DEBUG_LOG_FILE, debug_log

_background_lock = threading.Lock()
_background_started = False


def initialize_host(app: Flask, path: str, thinking_mode: bool = False) -> None:
    from modules.host_workspace_manager import ensure_default_host_workspace
    from server.gateway_auth import get_or_create_host_api_token

    DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DEBUG_LOG_FILE.open('w', encoding='utf-8') as log:
        log.write(f"调试日志开始 - {datetime.now()}\n")
        log.write(f"默认工作区路径: {path}\n")
        log.write(f"默认思考模式: {'思考' if thinking_mode else '快速'}\n")
        log.write(f"自动修复: {'开启' if AUTO_FIX_TOOL_CALL else '关闭'}\n")
        log.write(f"最大迭代: {MAX_ITERATIONS_PER_TASK}\n")
        log.write(f"最大工具调用: {MAX_TOTAL_TOOL_CALLS}\n")

    app.config['DEFAULT_THINKING_MODE'] = thinking_mode
    app.config['DEFAULT_RUN_MODE'] = 'thinking' if thinking_mode else 'fast'

    # The CLI reads this token before its first request.
    try:
        if not get_or_create_host_api_token():
            debug_log('[HostStartup] 无法准备 Host API token')
    except Exception as exc:
        debug_log(f'[HostStartup] Host API token 初始化失败: {exc}')
    try:
        ensure_default_host_workspace()
    except Exception as exc:
        debug_log(f'[HostStartup] 默认工作区初始化失败: {exc}')


def start_background_jobs() -> None:
    """Start the process-wide Host services once."""
    global _background_started
    with _background_lock:
        if _background_started:
            return
        _background_started = True

    try:
        from server.context import start_conversation_terminal_reaper
        start_conversation_terminal_reaper()
    except Exception as exc:
        debug_log(f'[ConvTerminalReaper] 启动失败: {exc}')
    try:
        from modules.scheduled_tasks import get_service
        get_service().start()
    except Exception as exc:
        debug_log(f'[ScheduledTasks] 启动失败: {exc}')
    try:
        from modules.weekly_quota import start_tracker
        start_tracker()
    except Exception as exc:
        debug_log(f'[WeeklyQuota] 启动失败: {exc}')

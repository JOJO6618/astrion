from __future__ import annotations
from server.chat import chat_bp
import json, time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from io import BytesIO
import zipfile
import os

from flask import Blueprint, jsonify, request, session, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import secrets

from config import MAX_UPLOAD_SIZE, OUTPUT_FORMATS
from modules.personalization_manager import (
    load_personalization_config,
    resolve_context_compression_settings,
    save_personalization_config,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
)
from modules.skills_manager import (
    get_skills_catalog,
    infer_private_skills_dir,
    merge_enabled_skills,
    sync_workspace_skills,
)
from modules.upload_security import UploadSecurityError
from modules.host_sandbox_policy import load_policy, save_policy
from modules.user_manager import UserWorkspace
from core.web_terminal import WebTerminal
from config.model_profiles import get_model_context_window

from server.auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from server.context import with_terminal, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, get_or_create_usage_tracker
from server.security import rate_limited
from server.utils_common import debug_log
from server.state import PROJECT_MAX_STORAGE_MB
from server.state import tool_approval_manager, user_question_manager
from server.monitor import get_cached_monitor_snapshot

from modules.i18n import tr

UPLOAD_FOLDER_NAME = ".astrion/user_upload"
@chat_bp.route('/api/terminals')
@api_login_required
@with_terminal
def get_terminals(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取终端会话列表"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_realtime_terminal"):
        return jsonify({"success": False, "error": tr("chat_terminal.realtime_terminal_admin_disabled")}), 403
    if terminal.terminal_manager:
        result = terminal.terminal_manager.list_terminals()
        return jsonify(result)
    else:
        return jsonify({"sessions": [], "active": None, "total": 0})

@chat_bp.route('/api/terminals/<session_name>/output')
@api_login_required
@with_terminal
def get_terminal_output_rest(terminal: WebTerminal, workspace: UserWorkspace, username: str, session_name: str):
    """获取终端输出历史（TerminalPanel 轮询端点，替代原 WebSocket get_terminal_output）。"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_realtime_terminal"):
        return jsonify({"success": False, "error": tr("chat_terminal.realtime_terminal_admin_disabled")}), 403
    if not terminal.terminal_manager:
        return jsonify({"success": False, "error": "Terminal system not initialized"}), 400
    try:
        lines = int(request.args.get("lines", 100))
    except (TypeError, ValueError):
        lines = 100
    lines = max(1, min(lines, 2000))
    result = terminal.terminal_manager.get_terminal_output(session_name, lines)
    if result.get("success"):
        return jsonify({
            "success": True,
            "session": session_name,
            "output": result.get("output", ""),
            "is_interactive": result.get("is_interactive", False),
            "last_command": result.get("last_command", ""),
            "last_event_time": result.get("last_event_time"),
        })
    return jsonify({"success": False, "error": result.get("error", "unknown error")}), 404

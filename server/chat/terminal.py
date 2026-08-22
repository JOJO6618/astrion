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
from server.security import rate_limited, prune_socket_tokens
from server.utils_common import debug_log
from server.state import PROJECT_MAX_STORAGE_MB, pending_socket_tokens, SOCKET_TOKEN_TTL_SECONDS
from server.state import tool_approval_manager, user_question_manager
from server.extensions import socketio
from server.monitor import get_cached_monitor_snapshot

UPLOAD_FOLDER_NAME = ".astrion/user_upload"
@chat_bp.route('/api/terminals')
@api_login_required
@with_terminal
def get_terminals(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取终端会话列表"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_realtime_terminal"):
        return jsonify({"success": False, "error": "实时终端已被管理员禁用"}), 403
    if terminal.terminal_manager:
        result = terminal.terminal_manager.list_terminals()
        return jsonify(result)
    else:
        return jsonify({"sessions": [], "active": None, "total": 0})

@chat_bp.route('/api/socket-token', methods=['GET'])
@api_login_required
def issue_socket_token():
    """生成一次性 WebSocket token，供握手阶段使用。"""
    username = get_current_username()
    prune_socket_tokens()
    now = time.time()
    for token_value, meta in list(pending_socket_tokens.items()):
        if meta.get("username") == username:
            pending_socket_tokens.pop(token_value, None)
    token_value = secrets.token_urlsafe(32)
    pending_socket_tokens[token_value] = {
        "username": username,
        "expires_at": now + SOCKET_TOKEN_TTL_SECONDS,
        "fingerprint": (request.headers.get('User-Agent') or '')[:128],
    }
    return jsonify({
        "success": True,
        "token": token_value,
        "expires_in": SOCKET_TOKEN_TTL_SECONDS
    })

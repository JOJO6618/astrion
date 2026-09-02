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

from modules.i18n import tr

UPLOAD_FOLDER_NAME = ".astrion/user_upload"
@chat_bp.route('/api/memory', methods=['GET'])
@api_login_required
@with_terminal
def api_memory_entries(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """返回主/任务记忆条目列表，供虚拟显示器加载"""
    memory_type = request.args.get('type', 'main')
    if memory_type not in ('main', 'task'):
        return jsonify({"success": False, "error": tr("chat_misc.invalid_memory_type")}), 400
    try:
        entries = terminal.memory_manager._read_entries(memory_type)  # type: ignore
        return jsonify({"success": True, "type": memory_type, "entries": entries})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

@chat_bp.route('/api/gui/monitor_snapshot', methods=['GET'])
@api_login_required
def get_monitor_snapshot_api():
    execution_id = request.args.get('executionId') or request.args.get('execution_id') or request.args.get('id')
    if not execution_id:
        return jsonify({
            'success': False,
            'error': tr('chat_misc.missing_execution_id')
        }), 400
    stage = (request.args.get('stage') or 'before').lower()
    if stage not in {'before', 'after'}:
        stage = 'before'
    snapshot = get_cached_monitor_snapshot(execution_id, stage, username=get_current_username())
    if not snapshot:
        return jsonify({
            'success': False,
            'error': tr('chat_misc.snapshot_not_found')
        }), 404
    return jsonify({
        'success': True,
        'snapshot': snapshot,
        'stage': stage
    })

@chat_bp.route('/api/focused')
@api_login_required
@with_terminal
def get_focused_files(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """聚焦功能已废弃，返回空列表保持接口兼容。"""
    return jsonify({})

@chat_bp.route('/api/todo-list')
@api_login_required
@with_terminal
def get_todo_list(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前待办列表"""
    todo_snapshot = terminal.context_manager.get_todo_snapshot()
    return jsonify({
        "success": True,
        "data": todo_snapshot
    })

@chat_bp.route('/api/tool-settings', methods=['GET', 'POST'])
@api_login_required
@with_terminal
def tool_settings(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取或更新工具启用状态"""
    if request.method == 'GET':
        snapshot = terminal.get_tool_settings_snapshot()
        return jsonify({
            "success": True,
            "categories": snapshot
        })

    data = request.get_json() or {}
    category = data.get('category')
    if category is None:
        return jsonify({
            "success": False,
            "error": tr("chat_misc.missing_category"),
            "message": tr("chat_misc.missing_category_field")
        }), 400

    if 'enabled' not in data:
        return jsonify({
            "success": False,
            "error": tr("chat_misc.missing_enabled"),
            "message": tr("chat_misc.missing_enabled_field")
        }), 400

    try:
        policy = resolve_admin_policy(get_current_user_record())
        if policy.get("ui_blocks", {}).get("block_tool_toggle"):
            return jsonify({
                "success": False,
                "error": tr("chat_misc.tool_toggle_admin_disabled"),
                "message": tr("chat_misc.admin_forced_disabled")
            }), 403
        enabled = bool(data['enabled'])
        forced = getattr(terminal, "admin_forced_category_states", {}) or {}
        if isinstance(forced.get(category), bool) and forced[category] != enabled:
            return jsonify({
                "success": False,
                "error": tr("chat_misc.category_forced"),
                "message": tr("chat_misc.admin_forced_state")
            }), 403
        terminal.set_tool_category_enabled(category, enabled)
        snapshot = terminal.get_tool_settings_snapshot()
        socketio.emit('tool_settings_updated', {
            'categories': snapshot
        }, room=f"user_{username}")
        return jsonify({
            "success": True,
            "categories": snapshot
        })
    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 400

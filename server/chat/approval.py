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
from server.state import tool_approval_manager, user_question_manager, plan_approval_manager
from server.extensions import socketio
from server.monitor import get_cached_monitor_snapshot

from modules.i18n import tr

UPLOAD_FOLDER_NAME = ".astrion/user_upload"
@chat_bp.route('/api/user-questions/pending', methods=['GET'])
@api_login_required
@with_terminal
def list_pending_user_questions(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前用户待回答的问题列表。"""
    requested_conv_id = (request.args.get("conversation_id") or "").strip() or None
    if requested_conv_id is None:
        requested_conv_id = getattr(terminal.context_manager, "current_conversation_id", None)
    items = user_question_manager.list_pending(username=username, conversation_id=requested_conv_id)
    return jsonify({
        "success": True,
        "items": items,
        "conversation_id": requested_conv_id,
    })

@chat_bp.route('/api/user-questions/<question_id>/answer', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("user_question_answer", 120, 60, scope="user")
def answer_user_question(terminal: WebTerminal, workspace: UserWorkspace, username: str, question_id: str):
    """提交 ask_user 工具问题的回答。"""
    data = request.get_json() or {}
    try:
        item = user_question_manager.answer(
            question_id=question_id,
            username=username,
            selected_option_id=data.get("selected_option_id"),
            text=data.get("text"),
            dismissed=bool(data.get("dismissed")),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except KeyError:
        return jsonify({"success": False, "error": tr("chat_approval.question_not_found")}), 404
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({
        "success": True,
        "item": item,
    })

@chat_bp.route('/api/plan-approvals/pending', methods=['GET'])
@api_login_required
@with_terminal
def list_pending_plan_approvals(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前用户待批准的计划列表（work_mode=plan 的 submit_plan 工具）。"""
    requested_conv_id = (request.args.get("conversation_id") or "").strip() or None
    if requested_conv_id is None:
        requested_conv_id = getattr(terminal.context_manager, "current_conversation_id", None)
    items = plan_approval_manager.list_pending(username=username, conversation_id=requested_conv_id)
    return jsonify({
        "success": True,
        "items": items,
        "conversation_id": requested_conv_id,
    })

@chat_bp.route('/api/plan-approvals/<approval_id>/answer', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("plan_approval_answer", 120, 60, scope="user")
def answer_plan_approval(terminal: WebTerminal, workspace: UserWorkspace, username: str, approval_id: str):
    """提交计划批准/拒绝决策。approved=true 时工具循环侧会自动切换到 execute 模式。"""
    data = request.get_json() or {}
    try:
        item = plan_approval_manager.answer(
            approval_id=approval_id,
            username=username,
            approved=bool(data.get("approved")),
            comment=data.get("comment"),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except KeyError:
        return jsonify({"success": False, "error": tr("chat_approval.plan_approval_not_found")}), 404
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({
        "success": True,
        "item": item,
    })

@chat_bp.route('/api/tool-approvals/pending', methods=['GET'])
@api_login_required
@with_terminal
def list_pending_tool_approvals(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前用户待审批工具列表。"""
    requested_conv_id = (request.args.get("conversation_id") or "").strip() or None
    if requested_conv_id is None:
        requested_conv_id = getattr(terminal.context_manager, "current_conversation_id", None)
    items = tool_approval_manager.list_pending(username=username, conversation_id=requested_conv_id)
    return jsonify({
        "success": True,
        "items": items,
        "conversation_id": requested_conv_id,
    })

@chat_bp.route('/api/tool-approvals/<approval_id>/decision', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("tool_approval_decision", 60, 60, scope="user")
def decide_tool_approval(terminal: WebTerminal, workspace: UserWorkspace, username: str, approval_id: str):
    """提交工具审批决策。"""
    data = request.get_json() or {}
    decision = str(data.get("decision") or "").strip().lower()
    try:
        item = tool_approval_manager.decide(approval_id=approval_id, username=username, decision=decision)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except KeyError:
        return jsonify({"success": False, "error": tr("chat_approval.approval_not_found")}), 404
    except PermissionError as exc:
        return jsonify({"success": False, "error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({
        "success": True,
        "item": item,
    })

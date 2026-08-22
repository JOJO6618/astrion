from __future__ import annotations
from server.chat import chat_bp
import json, time

PERMISSION_MODE_OPTIONS = ["readonly", "approval", "auto_approval", "unrestricted"]
EXECUTION_MODE_OPTIONS = ["sandbox", "direct"]
NETWORK_PERMISSION_OPTIONS = ["restricted", "full", "none"]
WORK_MODE_OPTIONS = ["plan", "ask", "execute"]
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
from modules.host_sandbox_policy import (
    load_policy,
    save_policy,
    get_macos_deny_read_paths,
    get_macos_deny_read_regexes,
    get_windows_deny_read_paths,
)
from modules.user_manager import UserWorkspace
from core.web_terminal import WebTerminal
from config.model_profiles import get_model_context_window

from server.auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from server.context import with_terminal, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, get_or_create_usage_tracker, get_user_resources
from server.security import rate_limited, prune_socket_tokens
from server.utils_common import debug_log
from server.state import PROJECT_MAX_STORAGE_MB, pending_socket_tokens, SOCKET_TOKEN_TTL_SECONDS
from server.state import tool_approval_manager, user_question_manager
from server.extensions import socketio
from server.monitor import get_cached_monitor_snapshot
import os
import re

UPLOAD_FOLDER_NAME = ".astrion/user_upload"


def _sync_workspace_terminal_mode(username: str, workspace, kind: str, mode: str) -> None:
    """把模式切换同步到工作区级服务 terminal（仅内存态，不持久化）。

    对话级隔离后，携带 conversation_id 的切换请求落在对话级 terminal 上，
    工作区级 terminal 仍保持旧模式；而新建对话会以工作区 terminal 的当前
    模式冻结进 metadata（create_new_conversation），导致新对话继承过期模式。
    这里同步内存态即可：persist=False，避免误写工作区 terminal 上的陈旧对话。
    """
    try:
        ws_terminal, _ws = get_user_resources(
            username,
            workspace_id=getattr(workspace, "workspace_id", None),
            conversation_id=None,
        )
        if not ws_terminal:
            return
        if kind == "permission_mode":
            ws_terminal.set_permission_mode(mode, persist=False)
        elif kind == "execution_mode":
            ws_terminal.set_execution_mode(mode)
        elif kind == "network_permission":
            ws_terminal.set_network_permission(mode)
        elif kind == "work_mode":
            # 必须走 switch_work_mode（persist=False 仅内存态）：裸 set 会破坏
            # 「plan ⇒ 权限只读」不变量（切 plan 不锁只读 / 离 plan 不恢复权限）。
            # persist=False 避免误写工作区 terminal 上可能残留的陈旧对话 metadata。
            ws_terminal.switch_work_mode(mode, persist=False)
    except Exception:
        pass


# Windows 特有的 deny 正则（POSIX 的 ^/.*\.env$ 无法匹配 C:\ 开头的路径）
_WINDOWS_DENY_READ_REGEXES = [
    r"^[A-Za-z]:[\\/].*\.env(\.[^\\/]*)?$",
]


def _windows_system_deny_paths() -> list:
    """Windows 系统敏感目录（从环境变量取值，适配非默认安装位置）。"""
    return [
        os.environ.get("SystemRoot", r"C:\Windows"),
        os.environ.get("ProgramData", r"C:\ProgramData"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ]


def _normalize_for_compare(p: str) -> str:
    """路径比较归一化：展开 ~、转绝对路径、统一大小写与分隔符。"""
    return os.path.normcase(os.path.abspath(os.path.expanduser(p))).lower().rstrip("\\/")


def _path_conflicts_with_deny_list(path: str) -> Optional[str]:
    """检查用户授权路径是否与内置 deny 列表冲突，返回错误信息或 None。"""
    if not path:
        return None
    expanded = os.path.abspath(os.path.expanduser(path))
    deny_paths = list(get_macos_deny_read_paths())
    deny_regexes = list(get_macos_deny_read_regexes())
    if os.name == "nt":
        # Windows：补充 Windows deny 列表与系统目录（此前完全不校验）
        deny_paths += list(get_windows_deny_read_paths()) + _windows_system_deny_paths()
        deny_regexes += _WINDOWS_DENY_READ_REGEXES
    expanded_lower = _normalize_for_compare(expanded)
    for deny_path in deny_paths:
        deny_lower = _normalize_for_compare(deny_path)
        if expanded_lower == deny_lower or expanded_lower.startswith(deny_lower + os.sep):
            return f"禁止授权敏感路径: {path}"
    for pattern in deny_regexes:
        try:
            if re.search(pattern, expanded) or re.search(pattern, path):
                return f"禁止授权敏感文件: {path}"
        except re.error:
            continue
    return None
@chat_bp.route('/api/permission-mode', methods=['GET'])
@api_login_required
@with_terminal
def get_permission_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前权限模式。"""
    current_conversation_id = getattr(terminal.context_manager, "current_conversation_id", None)
    return jsonify({
        "success": True,
        "mode": terminal.get_permission_mode() if hasattr(terminal, "get_permission_mode") else "unrestricted",
        "pending_mode": (terminal.get_pending_runtime_modes().get("permission_mode") if hasattr(terminal, "get_pending_runtime_modes") else None),
        "options": PERMISSION_MODE_OPTIONS,
        "conversation_id": current_conversation_id,
    })

@chat_bp.route('/api/permission-mode', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("permission_mode_switch", 30, 60, scope="user")
def update_permission_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """更新当前对话权限模式。"""
    data = request.get_json() or {}
    target_mode = str(data.get("mode") or "").strip().lower()
    if target_mode not in PERMISSION_MODE_OPTIONS:
        return jsonify({
            "success": False,
            "error": "无效权限模式，仅支持 readonly / approval / auto_approval / unrestricted"
        }), 400

    # 判断当前是否在对话运行期间
    is_running = False
    try:
        from server.tasks import task_manager
        current_conv = getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None)
        running_tasks = [
            r for r in task_manager.list_tasks(username) if r.status in {"pending", "running", "cancel_requested"}
        ]
        if current_conv:
            running_tasks.sort(key=lambda r: 0 if r.conversation_id == current_conv else 1)
        is_running = bool(running_tasks)
    except Exception:
        pass

    if is_running:
        # 运行期间：使用 pending 机制延迟到工具循环中统一处理，
        # 避免「A→B→A」来回切换时中间值被覆盖后仍插入多余消息。
        try:
            terminal.queue_permission_mode_change(target_mode)
            _sync_workspace_terminal_mode(username, workspace, "permission_mode", target_mode)
        except Exception as exc:
            return jsonify({
                "success": False,
                "error": str(exc),
                "message": "更新权限模式失败"
            }), 500
        status = terminal.get_status()
        socketio.emit('status_update', status, room=f"user_{username}")
        return jsonify({
            "success": True,
            "mode": target_mode,
            "pending_mode": target_mode,
            "options": PERMISSION_MODE_OPTIONS,
            "conversation_id": getattr(terminal.context_manager, "current_conversation_id", None),
            "state": (terminal.get_execution_mode_state() if hasattr(terminal, "get_execution_mode_state") else None),
            "message": "权限模式将在当前工具执行完成后生效",
        })

    # 空闲期间：直接生效。切换通知由 baseline 机制在下一条真实 user 消息时补发
    # （见 chat_flow_task_main 的 drift 注入点），此处不再 enqueue。
    try:
        applied_mode = terminal.set_permission_mode(target_mode)
        if hasattr(terminal, "pending_permission_mode"):
            terminal.pending_permission_mode = None
        if hasattr(terminal, "_persist_runtime_mode_metadata"):
            terminal._persist_runtime_mode_metadata({
                "permission_mode": applied_mode,
                "pending_permission_mode": None,
            })
        _sync_workspace_terminal_mode(username, workspace, "permission_mode", applied_mode)
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "message": "更新权限模式失败"
        }), 500

    session["permission_mode"] = applied_mode
    status = terminal.get_status()
    socketio.emit('status_update', status, room=f"user_{username}")
    return jsonify({
        "success": True,
        "mode": applied_mode,
        "pending_mode": None,
        "options": PERMISSION_MODE_OPTIONS,
        "conversation_id": getattr(terminal.context_manager, "current_conversation_id", None),
        "state": (terminal.get_execution_mode_state() if hasattr(terminal, "get_execution_mode_state") else None),
        "message": "权限模式已更新并立即生效",
    })

@chat_bp.route('/api/execution-mode', methods=['GET'])
@api_login_required
@with_terminal
def get_execution_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    is_host = bool(getattr(terminal, "_is_host_mode", lambda: False)())
    can_manage = is_host and getattr(terminal, "user_role", "user") == "admin"
    state = terminal.get_execution_mode_state() if hasattr(terminal, "get_execution_mode_state") else {"mode": "sandbox"}
    return jsonify({
        "success": True,
        "enabled": can_manage,
        "state": state,
        "pending_mode": (terminal.get_pending_runtime_modes().get("execution_mode") if hasattr(terminal, "get_pending_runtime_modes") else None),
        "options": EXECUTION_MODE_OPTIONS,
    })

@chat_bp.route('/api/execution-mode', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("execution_mode_switch", 20, 60, scope="user")
def update_execution_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    is_host = bool(getattr(terminal, "_is_host_mode", lambda: False)())
    can_manage = is_host and getattr(terminal, "user_role", "user") == "admin"
    if not can_manage:
        return jsonify({"success": False, "error": "仅宿主机管理员可切换执行环境"}), 403
    data = request.get_json() or {}
    target_mode = str(data.get("mode") or "").strip().lower()
    if target_mode not in EXECUTION_MODE_OPTIONS:
        return jsonify({"success": False, "error": "无效执行环境，仅支持 sandbox / direct"}), 400

    # 判断当前是否在对话运行期间
    is_running = False
    try:
        from server.tasks import task_manager
        current_conv = getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None)
        running_tasks = [
            r for r in task_manager.list_tasks(username) if r.status in {"pending", "running", "cancel_requested"}
        ]
        if current_conv:
            running_tasks.sort(key=lambda r: 0 if r.conversation_id == current_conv else 1)
        is_running = bool(running_tasks)
    except Exception:
        pass

    if is_running:
        # 运行期间：使用 pending 机制延迟到工具循环中统一处理，
        # 避免「A→B→A」来回切换时中间值被覆盖后仍插入多余消息。
        try:
            terminal.queue_execution_mode_change(target_mode)
            _sync_workspace_terminal_mode(username, workspace, "execution_mode", target_mode)
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc), "message": "更新执行环境失败"}), 500
        status = terminal.get_status()
        socketio.emit('status_update', status, room=f"user_{username}")
        return jsonify({
            "success": True,
            "state": {
                **(terminal.get_execution_mode_state() if hasattr(terminal, "get_execution_mode_state") else {"mode": target_mode}),
                "mode": target_mode,
            },
            "pending_mode": target_mode,
            "options": EXECUTION_MODE_OPTIONS,
            "message": "执行环境将在当前工具执行完成后生效",
        })

    # 空闲期间：直接生效。切换通知由 baseline 机制在下一条真实 user 消息时补发。
    try:
        state = terminal.set_execution_mode(target_mode)
        if hasattr(terminal, "pending_execution_mode"):
            terminal.pending_execution_mode = None
        if hasattr(terminal, "_persist_runtime_mode_metadata"):
            terminal._persist_runtime_mode_metadata({
                "execution_mode": state.get("mode", target_mode),
                "pending_execution_mode": None,
            })
        _sync_workspace_terminal_mode(username, workspace, "execution_mode", state.get("mode", target_mode))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc), "message": "更新执行环境失败"}), 500
    status = terminal.get_status()
    socketio.emit('status_update', status, room=f"user_{username}")
    return jsonify({
        "success": True,
        "state": state,
        "pending_mode": None,
        "options": EXECUTION_MODE_OPTIONS,
        "message": "执行环境已更新并立即生效",
    })

@chat_bp.route('/api/network-permission', methods=['GET'])
@api_login_required
@with_terminal
def get_network_permission(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    is_host = bool(getattr(terminal, "_is_host_mode", lambda: False)())
    can_manage = is_host and getattr(terminal, "user_role", "user") == "admin"
    current = terminal.get_network_permission() if hasattr(terminal, "get_network_permission") else "restricted"
    return jsonify({
        "success": True,
        "enabled": can_manage,
        "mode": current,
        "pending_mode": (terminal.get_pending_runtime_modes().get("network_permission") if hasattr(terminal, "get_pending_runtime_modes") else None),
        "options": NETWORK_PERMISSION_OPTIONS,
    })

@chat_bp.route('/api/network-permission', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("network_permission_switch", 20, 60, scope="user")
def update_network_permission(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    is_host = bool(getattr(terminal, "_is_host_mode", lambda: False)())
    can_manage = is_host and getattr(terminal, "user_role", "user") == "admin"
    if not can_manage:
        return jsonify({"success": False, "error": "仅宿主机管理员可切换网络权限"}), 403
    data = request.get_json() or {}
    target_mode = str(data.get("mode") or "").strip().lower()
    if target_mode not in NETWORK_PERMISSION_OPTIONS:
        return jsonify({"success": False, "error": "无效网络权限，仅支持 restricted / full"}), 400

    is_running = False
    try:
        from server.tasks import task_manager
        current_conv = getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None)
        running_tasks = [
            r for r in task_manager.list_tasks(username) if r.status in {"pending", "running", "cancel_requested"}
        ]
        if current_conv:
            running_tasks.sort(key=lambda r: 0 if r.conversation_id == current_conv else 1)
        is_running = bool(running_tasks)
    except Exception:
        pass

    if is_running:
        try:
            terminal.queue_network_permission_change(target_mode)
            _sync_workspace_terminal_mode(username, workspace, "network_permission", target_mode)
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc), "message": "更新网络权限失败"}), 500
        status = terminal.get_status()
        socketio.emit('status_update', status, room=f"user_{username}")
        return jsonify({
            "success": True,
            "mode": target_mode,
            "pending_mode": target_mode,
            "options": NETWORK_PERMISSION_OPTIONS,
            "message": "网络权限将在当前工具执行完成后生效",
        })

    # 空闲期间：直接生效。切换通知由 baseline 机制在下一条真实 user 消息时补发。
    try:
        applied = terminal.set_network_permission(target_mode)
        if hasattr(terminal, "pending_network_permission"):
            terminal.pending_network_permission = None
        if hasattr(terminal, "_persist_runtime_mode_metadata"):
            terminal._persist_runtime_mode_metadata({
                "network_permission": applied,
                "pending_network_permission": None,
            })
        _sync_workspace_terminal_mode(username, workspace, "network_permission", applied)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc), "message": "更新网络权限失败"}), 500
    status = terminal.get_status()
    socketio.emit('status_update', status, room=f"user_{username}")
    return jsonify({
        "success": True,
        "mode": applied,
        "pending_mode": None,
        "options": NETWORK_PERMISSION_OPTIONS,
        "message": "网络权限已更新并立即生效",
    })

@chat_bp.route('/api/work-mode', methods=['GET'])
@api_login_required
@with_terminal
def get_work_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前运行模式（plan/ask/execute）。"""
    current_conversation_id = getattr(terminal.context_manager, "current_conversation_id", None)
    return jsonify({
        "success": True,
        "mode": terminal.get_work_mode() if hasattr(terminal, "get_work_mode") else "plan",
        # plan 档下权限被联动锁定为只读、执行环境被联动锁定为沙箱，前端需要同步显示
        "permission_mode": terminal.get_permission_mode() if hasattr(terminal, "get_permission_mode") else None,
        "execution_mode": terminal.get_execution_mode() if hasattr(terminal, "get_execution_mode") else None,
        "options": WORK_MODE_OPTIONS,
        "conversation_id": current_conversation_id,
    })

@chat_bp.route('/api/work-mode', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("work_mode_switch", 30, 60, scope="user")
def update_work_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """切换运行模式。与权限/执行环境不同：仅允许对话空闲时切换，运行中直接拒绝
    （运行模式决定交互节奏，运行中切换会让正在执行的任务行为自相矛盾）。
    """
    data = request.get_json() or {}
    target_mode = str(data.get("mode") or "").strip().lower()
    if target_mode not in WORK_MODE_OPTIONS:
        return jsonify({
            "success": False,
            "error": "无效运行模式，仅支持 plan / ask / execute"
        }), 400

    # 运行中拒绝切换（无 pending 队列——运行模式不存在「工具结果后插入」的路径）。
    # 运行模式是对话级状态，只检测本对话是否有运行中任务；其他对话运行不影响。
    try:
        from server.tasks import task_manager
        current_conv = getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None)
        if current_conv:
            conv_running = [
                r for r in task_manager.list_tasks(username)
                if r.status in {"pending", "running", "cancel_requested"} and r.conversation_id == current_conv
            ]
            if conv_running:
                return jsonify({
                    "success": False,
                    "error": "对话运行中，运行模式只能在空闲时切换",
                    "message": "对话运行中，运行模式只能在空闲时切换",
                }), 409
    except Exception:
        pass

    previous_permission = terminal.get_permission_mode() if hasattr(terminal, "get_permission_mode") else None
    try:
        result = terminal.switch_work_mode(target_mode)
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
            "message": "切换运行模式失败"
        }), 500

    # plan 联动可能改了权限模式：同步工作区级 terminal 的两个模式
    try:
        _sync_workspace_terminal_mode(username, workspace, "work_mode", result.get("mode") or target_mode)
        new_permission = result.get("permission_mode")
        if new_permission and new_permission != previous_permission:
            _sync_workspace_terminal_mode(username, workspace, "permission_mode", new_permission)
    except Exception:
        pass

    status = terminal.get_status()
    socketio.emit('status_update', status, room=f"user_{username}")
    return jsonify({
        "success": True,
        "mode": result.get("mode") or target_mode,
        "permission_mode": result.get("permission_mode"),
        # plan 联动可能切了执行环境（direct⇒sandbox），一并返回供前端同步
        "execution_mode": terminal.get_execution_mode() if hasattr(terminal, "get_execution_mode") else None,
        "options": WORK_MODE_OPTIONS,
        "conversation_id": getattr(terminal.context_manager, "current_conversation_id", None),
        "message": "运行模式已更新并立即生效",
    })

@chat_bp.route('/api/path-authorization', methods=['GET'])
@api_login_required
@with_terminal
def get_path_authorization(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    is_host = bool(getattr(terminal, "_is_host_mode", lambda: False)())
    can_manage = is_host and getattr(terminal, "user_role", "user") == "admin"
    data = load_policy()
    return jsonify({
        "success": True,
        "enabled": can_manage,
        "writable_paths": data.get("macos_writable_paths", []),
        "readable_extra_paths": data.get("macos_readable_extra_paths", []),
        "deny_read_paths": data.get("macos_deny_read_paths", []),
        "deny_read_regexes": data.get("macos_deny_read_regexes", []),
        "windows_deny_read_paths": data.get("windows_deny_read_paths", []),
    })

@chat_bp.route('/api/path-authorization', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("path_authorization_update", 20, 60, scope="user")
def update_path_authorization(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    is_host = bool(getattr(terminal, "_is_host_mode", lambda: False)())
    can_manage = is_host and getattr(terminal, "user_role", "user") == "admin"
    if not can_manage:
        return jsonify({"success": False, "error": "仅宿主机管理员可管理路径授权"}), 403
    data = request.get_json() or {}
    writable_items = data.get("writable_paths")
    readable_items = data.get("readable_extra_paths")
    if not isinstance(writable_items, list) or not isinstance(readable_items, list):
        return jsonify({"success": False, "error": "writable_paths/readable_extra_paths 必须为数组"}), 400
    writable = [str(x).strip() for x in writable_items if str(x).strip()]
    readable_extra = [str(x).strip() for x in readable_items if str(x).strip()]
    if "/" in writable or "/" in readable_extra:
        return jsonify({"success": False, "error": "禁止授权根目录 /"}), 400
    # Windows：禁止授权驱动器根目录（如 C:\、D:/），此前仅检查 POSIX 根 "/"
    drive_root_pattern = re.compile(r"^[A-Za-z]:[\\/]?$")
    if any(drive_root_pattern.match(p) for p in writable + readable_extra):
        return jsonify({"success": False, "error": "禁止授权驱动器根目录（如 C:\\）"}), 400
    for p in writable + readable_extra:
        conflict = _path_conflicts_with_deny_list(p)
        if conflict:
            return jsonify({"success": False, "error": conflict}), 400
    payload = save_policy({
        "macos_writable_paths": writable,
        "macos_readable_extra_paths": readable_extra
    })
    return jsonify({
        "success": True,
        "writable_paths": payload.get("macos_writable_paths", []),
        "readable_extra_paths": payload.get("macos_readable_extra_paths", []),
        "deny_read_paths": payload.get("macos_deny_read_paths", []),
        "deny_read_regexes": payload.get("macos_deny_read_regexes", []),
    })

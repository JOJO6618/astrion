"""Conversation entry: committed prefix, live display state and an exact cursor."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from modules.i18n import tr
from modules.preview_targets import _normalize as normalize_preview, is_preview_enabled
from server.auth_helpers import api_login_required, get_current_username
from server.context import get_user_resources
from server.tasks import task_manager
from server.tasks.queue_state import queue_snapshot
from server.conversation_view.snapshots import ACTIVE
from server.utils_common import debug_log

conversation_bootstrap_bp = Blueprint("conversation_bootstrap", __name__)


def _normalize_conv_id(conversation_id: str) -> str:
    value = str(conversation_id or "").strip()
    return value if not value or value.startswith("conv_") else f"conv_{value}"


def _existing_file(file_manager, path: str) -> bool:
    if file_manager is None:
        return False
    try:
        valid, _error, full_path = file_manager._validate_path(path)
        return bool(valid and full_path is not None and full_path.is_file())
    except Exception:
        return False


@conversation_bootstrap_bp.route("/api/conversations/<conversation_id>/bootstrap", methods=["GET"])
@api_login_required
def bootstrap_conversation(conversation_id: str):
    username = get_current_username()
    normalized_id = _normalize_conv_id(conversation_id)
    if not normalized_id:
        return jsonify({"success": False, "error": tr("bootstrap.missing_conversation_id")}), 400
    workspace_id = str(request.args.get("workspace_id") or "").strip() or None
    try:
        terminal, workspace = get_user_resources(username, workspace_id=workspace_id)
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503
    context = getattr(terminal, "context_manager", None)
    if context is None:
        return jsonify({"success": False, "error": tr("bootstrap.terminal_unavailable")}), 503
    manager = context._get_conversation_manager_for_id(normalized_id)
    effective_workspace = str(getattr(workspace, "workspace_id", None) or workspace_id
                              or session.get("workspace_id") or "default")
    snapshot = task_manager.capture_display(
        username, normalized_id, manager, workspace_id=effective_workspace
    ) if manager else None
    if snapshot is None:
        return jsonify({"success": False, "error": tr("bootstrap.conversation_not_found", id=normalized_id)}), 404
    conversation = snapshot["data"]
    meta = conversation.get("metadata") or {}
    display = snapshot["display"]
    task = display["task"]
    is_main_running = bool(task and task["status"] in ACTIVE)
    background = {
        "has_running_sub_agents": False,
        "has_running_background_commands": False,
        "has_running_multi_agent": False,
    }
    try:
        conv_terminal, _ = get_user_resources(
            username, workspace_id=effective_workspace, conversation_id=normalized_id
        )
        if conv_terminal:
            background = task_manager.get_conversation_running_status(conv_terminal, normalized_id)
    except Exception as exc:
        debug_log(f"[Bootstrap] background status unavailable: {exc}")
    running = {
        "is_main_running": is_main_running,
        "main_task_id": task["task_id"] if is_main_running else None,
        "main_task_type": task["task_type"] if is_main_running else None,
        **background,
        "is_truly_active": is_main_running or any(background.values()),
    }
    file_manager = getattr(terminal, "file_manager", None)
    edited_files = [
        {"path": item["path"], "op": item.get("op") or "edit", "ts": item.get("ts") or ""}
        for item in meta.get("edited_files", [])
        if isinstance(item, dict) and item.get("path") and _existing_file(file_manager, item["path"])
    ]
    preview_targets = []
    if is_preview_enabled():
        preview_targets = [
            item for item in normalize_preview(meta.get("preview_targets"))
            if item.get("type") != "file" or _existing_file(file_manager, str(item.get("path") or ""))
        ]
    try:
        from server.chat.preview import build_preview_runtime
        preview_runtime = build_preview_runtime(terminal, username, workspace, normalized_id)
    except Exception:
        preview_runtime = {"preview_base": None, "preview_token": None}
    run_mode = meta.get("run_mode") or ("thinking" if meta.get("thinking_mode") else "fast")
    run_mode = "thinking" if run_mode == "deep" else run_mode
    return jsonify({"success": True, "data": {
        "conversation_id": normalized_id,
        "meta": {
            "title": conversation.get("title", tr("bootstrap.unknown_conversation")),
            "run_mode": run_mode,
            "thinking_mode": bool(meta.get("thinking_mode", run_mode != "fast")),
            "reasoning_effort": meta.get("reasoning_effort"),
            "model_key": meta.get("model_key"),
            "multi_agent_mode": bool(meta.get("multi_agent_mode")),
            "work_mode": meta.get("work_mode") or "",
            "permission_mode": meta.get("permission_mode") or "",
            "execution_mode": meta.get("execution_mode") or "",
            "network_permission": meta.get("network_permission") or "",
            "messages_count": len(conversation.get("messages") or []),
        },
        "messages": snapshot["messages"],
        "display": display,
        "running": running,
        "runtime_queue": queue_snapshot(manager, normalized_id, include_guidance=not is_main_running),
        "compression": {
            "in_progress": is_main_running and bool(meta.get("compression_in_progress")),
            "mode": meta.get("compression_mode"), "conversation_id": normalized_id,
        },
        "edited_files": edited_files,
        "preview_targets": preview_targets,
        **preview_runtime,
    }})

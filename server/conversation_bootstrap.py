"""Conversation entry: storage/display transaction, without execution preparation."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from modules.file_manager import FileManager
from server.context.modes import effective_modes, pending_modes
from modules.i18n import tr
from modules.preview_targets import _normalize as normalize_preview, is_preview_enabled
from server.auth_helpers import api_login_required

from server.context.background import background_status
from server.context.data_decorators import with_workspace_data
from server.tasks import task_manager

from server.tasks.queue_state import queue_snapshot
from server.conversation_view.snapshots import ACTIVE

conversation_bootstrap_bp = Blueprint("conversation_bootstrap", __name__)


def _normalize_conv_id(conversation_id: str) -> str:
    value = str(conversation_id or "").strip()
    return value if not value or value.startswith("conv_") else f"conv_{value}"


def _existing_file(file_manager, path: str) -> bool:
    try:
        valid, _error, full_path = file_manager._validate_path(path)
        return bool(valid and full_path is not None and full_path.is_file())
    except Exception:
        return False


@conversation_bootstrap_bp.route("/api/conversations/<conversation_id>/metadata", methods=["GET"])
@api_login_required
@with_workspace_data
def conversation_metadata(conversation_id: str, data_context, workspace, username):
    normalized_id = _normalize_conv_id(conversation_id)
    try:
        data = data_context.manager_for(normalized_id).read_conversation(normalized_id)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    if data is None:
        return jsonify(success=False, error=tr("bootstrap.conversation_not_found", id=normalized_id)), 404
    return jsonify(success=True, data={
        "id": normalized_id, "workspace_id": data_context.workspace_id,
        "title": data.get("title") or "",
        "title_locked": bool((data.get("metadata") or {}).get("title_locked")),
    })


@conversation_bootstrap_bp.route("/api/conversations/<conversation_id>/bootstrap", methods=["GET"])
@api_login_required
@with_workspace_data
def bootstrap_conversation(conversation_id: str, data_context, workspace, username):
    normalized_id = _normalize_conv_id(conversation_id)
    if not normalized_id:
        return jsonify(success=False, error=tr("bootstrap.missing_conversation_id")), 400
    try:
        manager = data_context.manager_for(normalized_id)
        snapshot = task_manager.capture_display(username, normalized_id, manager,
                                                workspace_id=data_context.workspace_id)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    if snapshot is None:
        return jsonify(success=False, error=tr("bootstrap.conversation_not_found", id=normalized_id)), 404
    conversation = snapshot["data"]
    meta = conversation.get("metadata") or {}
    display = snapshot["display"]
    modes = effective_modes(data_context, meta, normalized_id)
    task = display["task"]
    is_main_running = bool(task and task["status"] in ACTIVE)
    background = background_status(
                            data_context, normalized_id, activate=True)
    running = {
        "is_main_running": is_main_running,
        "main_task_id": task["task_id"] if is_main_running else None,
        "main_task_type": task["task_type"] if is_main_running else None,
        **background, "is_truly_active": is_main_running or any(background.values()),
    }
    files = FileManager(str(workspace.project_path), data_dir=str(workspace.data_dir))
    files.set_host_execution_mode(modes["execution_mode"])
    edited_files = [
        {"path": item["path"], "op": item.get("op") or "edit", "ts": item.get("ts") or ""}
        for item in meta.get("edited_files", [])
        if isinstance(item, dict) and item.get("path") and _existing_file(files, item["path"])
    ]
    preview_targets = []
    if is_preview_enabled():
        preview_targets = [item for item in normalize_preview(meta.get("preview_targets"))
                           if item.get("type") != "file" or _existing_file(files, str(item.get("path") or ""))]
    from server.chat.preview import build_preview_data_runtime
    preview_runtime = build_preview_data_runtime(manager, username, workspace, normalized_id, conversation)
    run_mode = meta.get("run_mode") or ("thinking" if meta.get("thinking_mode") else "fast")
    run_mode = "thinking" if run_mode == "deep" else run_mode
    from modules.goal_state_manager import GoalStateManager
    goal = GoalStateManager(workspace.data_dir, normalized_id).progress_snapshot()
    display.setdefault("state", {})["goal_progress"] = goal or None
    display["state"]["goal_running"] = goal.get("status") in {"running", "reviewing", "retrying"}
    return jsonify( {"success": True, "data": {
        "conversation_id": normalized_id,
        "workspace_id": data_context.workspace_id,
        "workspace": {"workspace_id": data_context.workspace_id,
                      "project_path": str(workspace.project_path) if workspace.username == "host" else ""},
        "meta": {
            "title": conversation.get("title", tr("bootstrap.unknown_conversation")),
            "run_mode": run_mode, "thinking_mode": bool(meta.get("thinking_mode", run_mode != "fast")),
            "reasoning_effort": meta.get("reasoning_effort"), "model_key": meta.get("model_key"),
            **modes,
            "pending_runtime_modes": pending_modes(data_context, meta, normalized_id),
            "execution_mode_enabled": data_context.identity.host_mode and data_context.identity.role == 'admin',
            "network_permission_enabled": data_context.identity.host_mode and data_context.identity.role == 'admin',
            "messages_count": len(conversation.get("messages") or []),
        },
        "messages": snapshot["messages"], "display": display, "running": running,
        "todo_list": conversation.get("todo_list"),
        "current_context_tokens": (conversation.get("token_statistics") or {}).get("current_context_tokens", 0),
        "runtime_queue": queue_snapshot(manager, normalized_id, include_guidance=not is_main_running,
                                        data=conversation),
        "compression": {"in_progress": is_main_running and bool(meta.get("compression_in_progress")),
                        "mode": meta.get("compression_mode"), "conversation_id": normalized_id},
        "edited_files": edited_files, "preview_targets": preview_targets, **preview_runtime,
    }})


@conversation_bootstrap_bp.route("/api/workspace-view", methods=["GET"])
@api_login_required
@with_workspace_data
def workspace_view(data_context, workspace, username):
    """Read workspace identity and activity without preparing /new settings."""
    from server.runtime import runtime_service

    active = runtime_service.list_runs(username, data_context.workspace_id, status='active')
    return jsonify(success=True, data={
        'host_mode': data_context.identity.host_mode,
        'active_conversation_id': active[0].get('conversation_id') if active else None,
        "workspace": {"workspace_id": data_context.workspace_id,
                      "project_path": str(workspace.project_path) if workspace.username == "host" else ""},

    })


@conversation_bootstrap_bp.route("/api/new-conversation/settings", methods=["POST"])
@api_login_required
@with_workspace_data
def prepare_new_conversation_settings(data_context, workspace, username):
    """Explicit /new preparation may acquire a workspace runtime, never a file."""
    from server.context.new_conversation import prepare_draft_settings
    from server.context.resources import get_user_resources, _resource_lock, _make_terminal_key
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        previous_id = payload.get("previous_conversation_id")

        if previous_id is not None and not isinstance(previous_id, str):
            raise ValueError("Invalid previous_conversation_id")
        # An explicit source is resolved only inside the target workspace storage.
        owner = "host" if workspace.username == "host" else username
        with _resource_lock(_make_terminal_key(owner, workspace.workspace_id)):
            terminal, workspace = get_user_resources(
                username, workspace_id=data_context.workspace_id, update_session=False,
            )
            settings = prepare_draft_settings(terminal, workspace, previous_id)
        manageable = data_context.identity.host_mode and data_context.identity.role == "admin"
        return jsonify(success=True, data={
            "workspace": {"workspace_id": data_context.workspace_id,
                          "project_path": str(workspace.project_path) if data_context.identity.host_mode else ""},
            "settings": settings,
            "execution_mode_enabled": manageable,
            "network_permission_enabled": manageable,
        })
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except RuntimeError as exc:
        return jsonify(success=False, error=str(exc)), 503

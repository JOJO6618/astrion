"""Adapt HTTP to the one RuntimeService/TaskManager and event cursor contract."""
from flask import jsonify, request
from server.auth_helpers import api_login_required
from server.runtime import RuntimeContext, TaskParams, runtime_service
from server.tasks.models import task_public_payload
from modules.chat_mode.media import resolve_refs
from . import chat_mode_bp
from .context import resources, body, operation, owned_task


@chat_mode_bp.post("/tasks")
@api_login_required
def send():
    identity, scope, storage = resources()
    payload = body()
    message = payload.get("message", "")
    if not isinstance(message, str) or len(message) > 1_000_000:
        raise ValueError("Invalid Chat message")
    if payload.get("files"):
        raise ValueError("Chat does not accept files")
    images = resolve_refs(scope.root, payload.get("images", []), "image")
    videos = resolve_refs(scope.root, payload.get("videos", []), "video")
    if not message.strip() and not images and not videos and not payload.get("regenerate_turn_id"):
        raise ValueError("Enter a message or select media")
    conversation_id = payload.get("conversation_id")
    if conversation_id:
        storage.read(conversation_id)
    thinking = payload.get("thinking_mode")
    if thinking is not None and not isinstance(thinking, bool):
        raise ValueError("Thinking mode must be a boolean")
    settings = payload.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - {"reasoning_effort", "network_enabled"}:
        raise ValueError("Invalid Chat initial settings")
    if "network_enabled" in settings and not isinstance(settings["network_enabled"], bool):
        raise ValueError("Network state must be a boolean")
    from modules.chat_mode.configuration import EFFORTS
    effort = settings.get("reasoning_effort")
    if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS):
        raise ValueError("Invalid reasoning effort")
    params = TaskParams(
        message=message, images=images, videos=videos, conversation_id=conversation_id,
        model_key=payload.get("model_key"), thinking_mode=thinking,
        chat_operation_id=operation(payload), chat_parent_id=payload.get("parent_id"),
        chat_regenerate_turn_id=payload.get("regenerate_turn_id"),
        chat_edit_message_id=payload.get("edit_message_id"), chat_settings=settings,
    )
    record = runtime_service.create_task(RuntimeContext(identity, params))
    return jsonify(success=True, data=task_public_payload(record))


@chat_mode_bp.get("/tasks/<task_id>")
@api_login_required
def events(task_id):
    identity, _, storage = resources()
    record = owned_task(identity, task_id)
    try:
        offset = max(0, int(request.args.get("from", 0)))
    except (ValueError, TypeError):
        raise ValueError("Invalid event cursor")
    from server.conversation_view.snapshots import VIEW_LOCK
    from server.tasks import task_manager
    with VIEW_LOCK:
        events, cursor, _, metadata = runtime_service.get_task_events(identity.username, task_id, offset)
        snapshot = task_manager.capture_display(identity.username, record.conversation_id,
                                                 storage.manager, workspace_id=None)
    return jsonify(success=True, data={
        "view": {"document": snapshot["data"], "display": snapshot["display"]} if snapshot else None,
        **task_public_payload(record, include_title=False),
        "events": events or [], "next_offset": cursor,
        "window_start": (metadata or {}).get("window_start", 0),
        "status": (metadata or {}).get("status", record.status),
    })


@chat_mode_bp.post("/tasks/<task_id>/cancel")
@api_login_required
def cancel(task_id):
    identity, _, _ = resources()
    owned_task(identity, task_id)
    runtime_service.cancel_task(identity.username, task_id)
    return jsonify(success=True)

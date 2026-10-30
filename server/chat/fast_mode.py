"""Conversation-local ChatGPT Fast setting, with idle-only atomic persistence."""
from flask import jsonify, request

from config.model_profiles import get_model_profile
from server.auth_helpers import api_login_required
from server.chat import chat_bp
from server.context import with_terminal
from server.conversation_view.snapshots import VIEW_LOCK
from server.main_task_gate import is_main_task_gate_busy
from server.tasks import task_manager
from utils.api_client.codex.service_tier import validate_fast_mode


@chat_bp.post("/api/chatgpt-fast-mode")
@api_login_required
@with_terminal
def update_chatgpt_fast_mode(terminal, workspace, username):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(success=False, error="Invalid settings"), 400
    try:
        enabled = validate_fast_mode(payload.get("enabled"))
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    context = terminal.context_manager
    conversation_id = context.current_conversation_id
    if not conversation_id or payload.get("conversation_id") != conversation_id:
        return jsonify(success=False, error="Conversation not found"), 404
    with VIEW_LOCK, task_manager._lock:
        if is_main_task_gate_busy(terminal) or any(
            record.username == username and record.conversation_id == conversation_id
            and record.workspace_id == workspace.workspace_id
            and record.status in {"pending", "running", "cancel_requested"}
            for record in task_manager._tasks.values()
        ):
            return jsonify(success=False, error="Change settings after the current task finishes"), 409
        manager = context.conversation_manager

        def update(document):
            metadata = document["metadata"]
            profile = get_model_profile(metadata.get("model_key")) or {}
            if enabled and not (
                profile.get("responses_auth") == "codex_oauth"
                and profile.get("supports_chatgpt_fast_mode")
            ):
                raise ValueError("Selected ChatGPT model does not support Fast mode")
            metadata["chatgpt_fast_mode"] = enabled

        try:
            manager.mutate_conversation(conversation_id, update)
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 400
        context.conversation_metadata["chatgpt_fast_mode"] = enabled
        terminal.api_client.chatgpt_fast_mode = enabled
    return jsonify(success=True, data={"chatgpt_fast_mode": enabled})

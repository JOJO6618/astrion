"""One-click, idempotent Chat -> Agent handoff to the shared workspace."""
import time
from flask import jsonify, request

from server.auth_helpers import api_login_required
from server.conversation_view.snapshots import VIEW_LOCK
from modules.chat_mode.handoff import read, update, select, decline, schedule_deadline
from .handoff_workspace import default_target
from .handoff_execution import freeze, start, is_running
from .handoff_view import public_handoff
from . import chat_mode_bp
from .context import resources, body, operation


def public_state(storage, conversation_id, message_id, principal):
    state = read(storage, conversation_id, message_id)
    if state["status"] == "pending":
        schedule_deadline(storage, conversation_id, principal, state)
    if state["status"] == "preparing" and not is_running(storage, conversation_id, message_id):
        state = update(storage, conversation_id, message_id, lambda current, _: current.update(
            status="failed", error="Handoff was interrupted; retry to continue in the default workspace"))
    return {**public_handoff(state, host_mode=principal.host_mode),
            "server_time": time.time(), "host": principal.host_mode}


@chat_mode_bp.route("/conversations/<conversation_id>/handoff", methods=["GET", "POST"])
@api_login_required
def handoff_api(conversation_id):
    principal, scope, storage = resources(read_only=False)
    payload = body() if request.method == "POST" else request.args
    message_id = payload.get("message_id")
    if not isinstance(message_id, str):
        raise ValueError("Missing suggestion message identity")
    with VIEW_LOCK:
        state = read(storage, conversation_id, message_id)
        if request.method == "POST":
            operation_id = operation(payload)
            decision = payload.get("decision")
            if decision == "select":
                select(storage, conversation_id, message_id)
            elif decision == "decline":
                decline(storage, conversation_id, principal, message_id)
            elif decision == "accept":
                if state["status"] not in {"preparing", "completed"}:
                    if state["status"] == "pending":
                        select(storage, conversation_id, message_id)
                    elif state["status"] not in {"selecting", "failed"}:
                        raise RuntimeError("Suggestion has already been decided")
                    snapshot = freeze(storage, conversation_id, message_id)
                    workspace, agent = default_target(principal)
                    update(storage, conversation_id, message_id, lambda current, _: current.update(
                        status="preparing", operation_id=operation_id, error="",
                        target_workspace_id=workspace.workspace_id, selection={}))
                    start(storage, conversation_id, message_id, agent, workspace, snapshot,
                          preferences_dir=scope.preferences_dir)
            else:
                raise ValueError("Invalid handoff decision")
        return jsonify(success=True, data=public_state(storage, conversation_id, message_id, principal))

"""Chat CRUD and branch edits use shared storage and display transactions."""
from flask import jsonify, request
from server.auth_helpers import api_login_required
from server.conversation_view.snapshots import VIEW_LOCK
from server.tasks import task_manager
from modules.chat_mode.configuration import configure_conversation
from . import chat_mode_bp
from .context import resources, body, operation
from .handoff_view import public_document

ACTIVE = {"pending", "running", "cancel_requested"}


def records(identity, conversation_id):
    with task_manager._lock:
        return [record for record in task_manager._tasks.values()
                if record.username == identity.username and record.workspace_id is None
                and record.principal.conversation_kind == "chat"
                and record.conversation_id == conversation_id]


@chat_mode_bp.get("/conversations")
@api_login_required
def list_conversations():
    _, _, storage = resources()
    query = request.args.get("q", "").strip()
    if query:
        return jsonify(success=True, data=storage.search(query, 100))
    items = []
    for path in storage.manager.conversations_dir.glob("conv_*.json"):
        document = storage.read(path.stem)
        items.append({key: document.get(key) for key in ("id", "title", "created_at", "updated_at")})
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return jsonify(success=True, data=items)


@chat_mode_bp.get("/conversations/<conversation_id>")
@chat_mode_bp.post("/conversations/<conversation_id>/presentation")
@api_login_required
def bootstrap(conversation_id):
    identity, _, storage = resources(read_only=request.method == "GET")
    with VIEW_LOCK:
        if request.method == "POST":
            from modules.chat_mode.handoff import presentation
            payload = body()
            presentation(storage, conversation_id, payload.get("presentation_id"),
                         release=payload.get("release") is True)
            if payload.get("release") is True:
                return jsonify(success=True)
        snapshot = task_manager.capture_display(identity.username, conversation_id, storage.manager,
                                                 workspace_id=None)
        if snapshot is None:
            raise FileNotFoundError("Chat conversation unavailable")
        # The document is the authoritative tree; display is the shared running
        # projection with its absolute cursor. Browsing a leaf never writes it.
        return jsonify(success=True, data={
            "document": public_document(snapshot["data"], host_mode=identity.host_mode),
            "display": snapshot["display"],
        })


@chat_mode_bp.post("/conversations/<conversation_id>/duplicate")
@api_login_required
def duplicate_conversation(conversation_id):
    from modules.chat_mode.duplicate import duplicate
    _, _, storage = resources(read_only=False)
    with VIEW_LOCK:
        new_id = duplicate(storage, conversation_id, operation(body()))
    return jsonify(success=True, data={"conversation_id": new_id})


@chat_mode_bp.post("/conversations/<conversation_id>/edit")
@api_login_required
def edit(conversation_id):
    _, _, storage = resources(read_only=False)
    payload = body()
    with VIEW_LOCK:
        result = storage.edit(conversation_id, operation(payload), payload.get("message_id"),
                              payload.get("content"), expected_revision=payload.get("revision"))
    return jsonify(success=True, data=result)


@chat_mode_bp.patch("/conversations/<conversation_id>/settings")
@api_login_required
def settings(conversation_id):
    identity, _, storage = resources(read_only=False)
    with VIEW_LOCK:
        if any(record.status in ACTIVE for record in records(identity, conversation_id)):
            raise RuntimeError("Model settings can change after the current generation")
        result = configure_conversation(storage, conversation_id, body())
    return jsonify(success=True, data=result)


@chat_mode_bp.post("/conversations/<conversation_id>/network")
@api_login_required
def network(conversation_id):
    _, _, storage = resources(read_only=False)
    payload = body()
    return jsonify(success=True, data=storage.set_network(
        conversation_id, operation(payload), payload.get("enabled")))


@chat_mode_bp.patch("/conversations/<conversation_id>/title")
@api_login_required
def title(conversation_id):
    _, _, storage = resources(read_only=False)
    value = body().get("title")
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Invalid Chat title")
    storage.read(conversation_id)
    storage.manager.update_conversation_title(conversation_id, value.strip())
    return jsonify(success=True)


@chat_mode_bp.delete("/conversations/<conversation_id>")
@api_login_required
def delete(conversation_id):
    identity, _, storage = resources(read_only=False)
    storage.read(conversation_id)
    from .handoff_execution import cancel_for_conversation, is_running
    from modules.chat_mode.handoff import cancel_deadlines
    from modules.chat_mode.media_gc import collect
    cancel_deadlines(storage, conversation_id)
    if not cancel_for_conversation(storage, conversation_id):
        raise RuntimeError("Handoff is stopping; retry deletion after it finishes")
    active = [record for record in records(identity, conversation_id) if record.status in ACTIVE]
    for record in active:
        task_manager.cancel_task(identity.username, record.task_id)
        if record.thread:
            record.thread.join(2)
    with VIEW_LOCK:
        if any(record.status in ACTIVE for record in records(identity, conversation_id)):
            raise RuntimeError("Chat is stopping; retry deletion after it finishes")
        states = storage.read(conversation_id)["metadata"]["chat"].get("handoffs", {})
        if any(is_running(storage, conversation_id, message_id) for message_id in states):
            raise RuntimeError("Handoff is stopping; retry deletion after it finishes")
        task_manager.invalidate_display(conversation_id, username=identity.username, workspace_id=None)
        if not storage.manager.delete_conversation(conversation_id):
            raise FileNotFoundError("Chat conversation unavailable")
        (storage.root / "drafts" / f"{conversation_id}.json").unlink(missing_ok=True)
        if identity.host_mode:
            from server.conversation_tabs import navigation
            navigation.deleted(identity.username, "", conversation_id)
        try:
            collect(storage)
        except (OSError, ValueError, KeyError) as exc:
            from server.utils_common import debug_log
            debug_log(f"[Chat] Media collection deferred: {exc}")
    return jsonify(success=True)

"""Conversation-scoped composer storage, without creating execution resources."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import jsonify, request

from modules.composer_drafts import read_draft, save_draft
from utils.conversation_manager.locking import directory_lock


def draft_owner(username: str) -> str:
    return hashlib.sha256(str(username).encode("utf-8")).hexdigest()[:24]


def resolve_draft(data_context, workspace, username):
    raw = request.args.get("conversation_id")
    if raw is None:
        raise ValueError("conversation_id is required (use new for an empty view)")
    cid = str(raw)
    if cid != "new":
        if not re.fullmatch(r"conv_[A-Za-z0-9_-]{1,160}", cid):
            raise ValueError("Invalid conversation_id")
        if data_context.manager_for(cid).read_conversation(cid) is None:
            raise LookupError("Conversation not found")
    scope = f"{draft_owner(username)}:{data_context.workspace_id}:{cid}"
    filename = hashlib.sha256(scope.encode("utf-8")).hexdigest() + ".json"
    return Path(workspace.data_dir) / "composer_drafts" / "v2" / filename, scope


def draft_payload(body):
    content = body.get("content", "")
    if not isinstance(content, str) or len(content) > 40000:
        raise ValueError("Draft content must be text of at most 40000 characters")
    raw_refs = body.get("skill_refs") or []
    if not isinstance(raw_refs, list):
        raise ValueError("Invalid skill references")
    refs = []
    for item in raw_refs[:100]:
        if isinstance(item, dict) and item.get("name") and item.get("path"):
            refs.append({key: str(item.get(key) or "")[:limit] for key, limit in
                         (("name", 200), ("description", 1000), ("path", 4000))})
    result = {
        "content": content,
        "skill_refs": refs,
        "editor_json": body.get("editor_json") if isinstance(body.get("editor_json"), dict) else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    for key, limit in (("files", 9), ("images", 9), ("videos", 1), ("recovered_ids", 1000)):
        values = body.get(key) or []
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"Invalid draft {key}")
        if len(values) > limit:
            raise ValueError(f"Too many draft {key}")
        result[key] = values
    return result


def acknowledge_recovered(data_context, username, ids):
    """Delete old pending items only after their recovered draft is durably saved."""
    if not ids or request.args.get("conversation_id") == "new":
        return
    from server.tasks import task_manager
    from server.tasks.queue_state import QUEUE_KEY, save_queue

    cid = request.args["conversation_id"]
    ids = set(ids)
    with task_manager._lock:
        records = [rec for rec in task_manager._tasks.values()
                   if rec.username == username and rec.workspace_id == data_context.workspace_id
                   and rec.conversation_id == cid]
        for rec in records:
            rec.runtime_pending_queue = [item for item in rec.runtime_pending_queue if item.get("id") not in ids]
        if records:
            save_queue(max(records, key=lambda rec: rec.created_at))
        else:
            manager = data_context.manager_for(cid)
            with manager._io_lock:
                data = manager.read_conversation(cid)
                if data is None:
                    return
                state = (data.get("metadata") or {}).get(QUEUE_KEY)
                if not state:
                    return
                state["pending"] = [item for item in state.get("pending", []) if item.get("id") not in ids]
                manager._atomic_write_json(manager._get_conversation_file_path(cid), data)


def get_draft(data_context, workspace, username):
    try:
        path, scope = resolve_draft(data_context, workspace, username)
        payload = read_draft(path)
        # Old workspace-wide input has no conversation ownership. Preserve it once in /new.
        if not payload and request.args.get("conversation_id") == "new":
            from server.conversation import _resolve_input_draft_path
            old_path, _ = _resolve_input_draft_path(workspace, username)
            old = read_draft(old_path)
            if old.get("content"):
                with directory_lock(path.parent):
                    if not read_draft(path):
                        save_draft(path, draft_payload(old))
                    payload = read_draft(path)
        public = {key: value for key, value in payload.items() if key != "writer_sequences"}
        return jsonify(success=True, data={**public, "scope": scope})
    except LookupError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400


def put_draft(data_context, workspace, username):
    try:
        path, scope = resolve_draft(data_context, workspace, username)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("Invalid draft payload")
        payload = {**draft_payload(body), "scope": scope}
        with directory_lock(path.parent):
            saved = save_draft(path, payload, client_id=str(body.get("client_id") or "")[:120],
                               sequence=max(0, int(body.get("sequence") or 0)),
                               base_revision=body.get("base_revision"))
            revision = read_draft(path).get("revision", 0)
        if saved:
            acknowledge_recovered(data_context, username, payload["recovered_ids"])
        return jsonify(success=True, data={"saved": saved, "revision": revision})
    except LookupError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 409

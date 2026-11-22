"""Read browser residency decisions without loading conversations or creating terminals."""
from __future__ import annotations

import json

from flask import jsonify, request

from modules.native_browser.retention import BrowserGroup, decide_retention


def navigation_scopes(state: dict) -> set[tuple[str, str, str]]:
    return {
        (tab.get("conversation_kind", "agent"), tab.get("workspace_id") or "",
         tab["conversation_id"] or tab["key"])
        for tab in state["tabs"] if tab["kind"] in {"conv", "new"}
    }


def _read_groups() -> dict[tuple[str, str, str], tuple[int | None, bool]]:
    payload = json.loads(request.headers.get("X-Astrion-Browser-State", "{}"))
    rows = payload["owners"]
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("invalid_browser_retention_state")
    owners = {}
    for row in rows:
        scope, visit, closed = row["scope"], row["visit"], row["closed"]
        if (not isinstance(scope, list) or len(scope) != 3 or scope[0] not in ("agent", "chat")
                or any(not isinstance(value, str) or len(value) > 256 for value in scope)
                or (visit is not None and (type(visit) is not int or visit < 0))
                or not isinstance(closed, bool)):
            raise ValueError("invalid_browser_scope")
        owners[tuple(scope)] = visit, closed
    return owners


def _conversation_exists(username, identity, scope, managers) -> bool | None:
    mode, workspace, conversation = scope
    if not conversation.startswith("conv_"):
        return True
    try:
        key = (mode, workspace)
        if key not in managers:
            if mode == "chat":
                from server.chat_mode.context import resources
                _, _, storage = resources()
                managers[key] = storage.manager
            else:
                from server.context.data import get_workspace_data
                managers[key] = get_workspace_data(username, workspace, identity=identity).manager()
        path = managers[key]._get_conversation_file_path(conversation)
        try:
            path.stat()
        except FileNotFoundError:
            return False
        return True
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return None


def retention_response():
    from server.auth_helpers import get_current_username
    from server.context.workspace import request_identity
    from server.conversation_tabs import _normalized_conversation_ids, navigation
    from server.tasks import task_manager

    try:
        owners = _read_groups()
    except (ValueError, KeyError, TypeError):
        return jsonify(success=False, error="invalid_browser_retention_state"), 400

    username = get_current_username() or ""
    open_scopes = navigation_scopes(navigation.snapshot(username))
    running = {
        (record.principal.conversation_kind, record.workspace_id or "", conversation)
        for record in task_manager.list_tasks(username)
        if record.status in {"pending", "running", "cancel_requested"}
        for conversation in _normalized_conversation_ids(record.conversation_id)
    }
    identity = request_identity()
    managers = {}
    groups = {
        json.dumps(scope, ensure_ascii=False, separators=(",", ":")): BrowserGroup(
            visit=visit, closed=closed and scope not in open_scopes, running=scope in running,
            exists=_conversation_exists(username, identity, scope, managers),
        )
        for scope, (visit, closed) in owners.items()
    }
    return jsonify(success=True, username=username, **decide_retention(groups))

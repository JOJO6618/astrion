"""HTTP adapters for explicit workspace data and existing runtime lookup."""
from __future__ import annotations

from functools import wraps

from flask import jsonify, request
from server.auth_helpers import get_current_username
from server.context.data import get_workspace_data
from server.context.identity import NoWorkspaceError


def requested_workspace_id():
    body = request.get_json(silent=True) if request.is_json else None
    value = request.args.get("workspace_id") or request.headers.get("X-Astrion-Workspace-Id")
    if not value and isinstance(body, dict):
        value = body.get("workspace_id")
    return str(value or "").strip() or None


def with_workspace_data(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        username = get_current_username()
        try:
            data = get_workspace_data(username, requested_workspace_id())
        except NoWorkspaceError as exc:
            return jsonify(success=False, error=str(exc), code="no_workspace"), 200
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 404
        except RuntimeError as exc:
            return jsonify(success=False, error=str(exc)), 503
        kwargs.update(data_context=data, workspace=data.workspace, username=username)
        from server.context.modes import ConversationModeNotFound
        try:
            return view(*args, **kwargs)
        except ConversationModeNotFound as exc:
            return jsonify(success=False, error=str(exc)), 404
    return wrapper

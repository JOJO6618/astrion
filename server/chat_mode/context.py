"""Only trusted server identity determines the account's Chat storage."""
from dataclasses import replace
from flask import request, session

from server.runtime import principal_from_session_snapshot
from server.chat_mode_scope import scope_for_principal
from modules.chat_mode.storage import ChatStorage


def principal():
    value = replace(principal_from_session_snapshot(dict(session), None),
                    workspace_id=None, host_workspace_id=None, conversation_kind="chat")
    value.validate()
    return value


def resources(*, read_only=True):
    identity = principal()
    scope = scope_for_principal(identity)
    return identity, scope, ChatStorage(scope.root, read_only=read_only)


def body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def operation(payload):
    value = payload.get("operation_id")
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError("A stable operation ID is required")
    return value


def owned_task(identity, task_id):
    from server.tasks import task_manager
    record = task_manager.get_task(identity.username, task_id)
    if record is None or record.principal.conversation_kind != "chat":
        raise FileNotFoundError("Chat task unavailable")
    if record.principal.is_api_user != identity.is_api_user or record.principal.host_mode != identity.host_mode:
        raise FileNotFoundError("Chat task unavailable")
    return record

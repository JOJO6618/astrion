"""The only handoff stage allowed to create/select an execution workspace."""
from dataclasses import replace
from pathlib import Path
from threading import RLock

from server.context.identity import RuntimeIdentity
from server.context.workspace import resolve_workspace


def identity_for(principal, workspace_id=None):
    return RuntimeIdentity(host_mode=principal.host_mode, host_workspace_id=workspace_id,
                           is_api_user=principal.is_api_user, role=principal.role)


def projects(principal):
    from server import state
    if principal.host_mode:
        from modules.host_workspace_manager import load_host_workspace_catalog
        return [{"id": item["workspace_id"], "label": item.get("label") or item["workspace_id"]}
                for item in load_host_workspace_catalog().get("workspaces", [])]
    manager = state.api_user_manager if principal.is_api_user else state.user_manager
    rows = (manager.list_workspaces(principal.username) if principal.is_api_user else
            manager.list_user_workspaces(principal.username, prepare=False))
    return [{"id": key, "label": value.get("label") or key} for key, value in rows.items()]


_DEFAULT_LOCK = RLock()
_DEFAULT_WORKSPACE_ID = "astrion-default"


def default_target(principal):
    """Create the shared handoff workspace once, without changing user selection."""
    with _DEFAULT_LOCK:
        if principal.host_mode:
            from modules.host_workspace_manager import create_host_workspace
            path = Path.home() / "Desktop" / "Astrion" / "Default"
            workspace_id = create_host_workspace(str(path), label="默认工作区")["workspace"]["workspace_id"]
        else:
            from server import state
            workspace_id = _DEFAULT_WORKSPACE_ID
            if principal.is_api_user:
                state.api_user_manager.ensure_workspace(principal.username, workspace_id)
            else:
                rows = state.user_manager.list_user_workspaces(principal.username, prepare=False)
                if workspace_id not in rows:
                    state.user_manager.create_user_workspace(principal.username,
                        workspace_id=workspace_id, label="默认工作区")
        return resolve_target(principal, {"workspace_id": workspace_id})


def resolve_target(principal, payload):
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        if workspace_id not in {item["id"] for item in projects(principal)}:
            raise ValueError("Workspace unavailable")
    else:
        raise ValueError("Workspace identity is required")
    workspace = resolve_workspace(principal.username, workspace_id,
                                  identity=identity_for(principal, workspace_id), prepare=True)
    agent = replace(principal, workspace_id=workspace_id, conversation_kind="agent",
                    host_workspace_id=workspace_id if principal.host_mode else None)
    return workspace, agent

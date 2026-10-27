"""The only handoff stage allowed to create/select an execution workspace."""
from dataclasses import replace
from pathlib import Path
import re
from uuid import uuid4

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


def default_project_name(title):
    """Use the Chat title as the editable folder suggestion on every platform."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', str(title or ''))
    return name.strip(' .')[:80].rstrip(' .') or 'Chat'


def _project_name(payload):
    name = payload.get("project_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or re.search(r'[/\\\x00-\x1f]', name):
        raise ValueError("A project name of at most 80 characters is required")
    name = name.strip()
    if name in {".", ".."}:
        raise ValueError("Invalid project name")
    return name


def resolve_target(principal, payload):
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        if workspace_id not in {item["id"] for item in projects(principal)}:
            raise ValueError("Workspace unavailable")
    else:
        if principal.host_mode:
            from modules.host_workspace_manager import create_host_workspace
            path = payload.get("project_path")
            if not isinstance(path, str) or not path.strip():
                path = str(Path.home() / "Desktop" / "Astrion" / _project_name(payload))
            # The selected path is the project folder. The shared creator derives
            # its label from the folder name when no explicit label is supplied.
            workspace_id = create_host_workspace(path)["workspace"]["workspace_id"]
        else:
            from server import state
            name = _project_name(payload)
            if principal.is_api_user:
                workspace_id = f"chat-{uuid4().hex[:12]}"
                state.api_user_manager.ensure_workspace(principal.username, workspace_id)
            else:
                workspace = state.user_manager.create_user_workspace(principal.username,
                    workspace_id=f"chat-{uuid4().hex[:12]}", label=name)
                workspace_id = workspace.workspace_id
    workspace = resolve_workspace(principal.username, workspace_id,
                                  identity=identity_for(principal, workspace_id), prepare=True)
    agent = replace(principal, workspace_id=workspace_id, conversation_kind="agent",
                    host_workspace_id=workspace_id if principal.host_mode else None)
    return workspace, agent

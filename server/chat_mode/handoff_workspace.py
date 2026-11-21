"""The only handoff stage allowed to create/select an execution workspace."""
from dataclasses import replace
from threading import RLock

from server.context.identity import RuntimeIdentity
from server.context.workspace import resolve_workspace


def _require_host(principal) -> None:
    """Only an authenticated Host identity may select an execution workspace."""
    if principal.is_api_user or not principal.host_mode or principal.username != "host":
        raise PermissionError("Host identity required")


def identity_for(principal, workspace_id=None):
    _require_host(principal)
    return RuntimeIdentity(host_mode=True, host_workspace_id=workspace_id,
                           is_api_user=False, role=principal.role or "admin")


def projects(principal):
    _require_host(principal)
    from modules.host_workspace_manager import load_host_workspace_catalog
    return [{"id": item["workspace_id"], "label": item.get("label") or item["workspace_id"]}
            for item in load_host_workspace_catalog().get("workspaces", [])]


_DEFAULT_LOCK = RLock()


def default_target(principal):
    """Resolve the Host default workspace without changing the user's project selection."""
    _require_host(principal)
    with _DEFAULT_LOCK:
        from modules.host_workspace_manager import create_host_workspace, default_workspace_path
        from modules.i18n import tr

        # 与首次启动自动创建的默认工作区同源，避免出现两个「默认工作区」。
        workspace_id = create_host_workspace(
            str(default_workspace_path()),
            label=tr("host_ws.default_workspace_label"),
        )["workspace"]["workspace_id"]
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

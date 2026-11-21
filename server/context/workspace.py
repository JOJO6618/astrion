"""Resolve registered Host workspace paths without creating execution resources."""
from __future__ import annotations

from pathlib import Path

from config import DATA_DIR, LOGS_DIR, UPLOAD_QUARANTINE_SUBDIR
from modules.host_workspace_manager import resolve_host_workspace
from modules.i18n import tr
from modules.workspace import UserWorkspace
from server import state
from server.context._flask_bridge import session_get
from server.context.identity import NoWorkspaceError, RuntimeIdentity


def request_identity() -> RuntimeIdentity:
    from server.auth_helpers import get_current_user_role
    return RuntimeIdentity(
        host_mode=bool(session_get("host_mode")),
        host_workspace_id=session_get("host_workspace_id") or session_get("workspace_id"),
        is_api_user=bool(session_get("is_api_user")),
        role=get_current_user_role(),
        preferred_model_key=session_get("model_key"),
        preferred_run_mode=session_get("run_mode"),
        preferred_thinking_mode=session_get("thinking_mode"),
    )


def resolve_workspace(username: str, workspace_id: str | None = None,
                      *, identity: RuntimeIdentity | None = None, prepare: bool = False) -> UserWorkspace:
    """Explicit identities never touch Flask session; reads never select a workspace."""
    identity = identity if identity is not None else request_identity()
    if username != "host" or not identity.host_mode or identity.is_api_user:
        raise ValueError("A Host identity is required")
    selected = workspace_id or identity.host_workspace_id
    if not selected:
        with state.HOST_ACTIVE_WORKSPACE_LOCK:
            selected = state.HOST_ACTIVE_WORKSPACE_ID
    _, entry = resolve_host_workspace(selected)
    if not entry:
        raise NoWorkspaceError(tr("context.no_workspace"))
    if workspace_id and entry["workspace_id"] != workspace_id:
        raise ValueError(tr("host_ws.workspace_not_found"))

    project = Path(entry["path"]).expanduser().resolve()
    data = Path(DATA_DIR).expanduser().resolve()
    logs = Path(LOGS_DIR).expanduser().resolve()
    quarantine = Path(UPLOAD_QUARANTINE_SUBDIR).expanduser()
    if not quarantine.is_absolute():
        quarantine = (project.parent / quarantine).resolve()
    workspace = UserWorkspace(
        username=username,
        root=project.parent,
        project_path=project,
        data_dir=data,
        logs_dir=logs,
        uploads_dir=project / ".astrion" / "user_upload",
        quarantine_dir=quarantine,
        workspace_id=entry["workspace_id"],
        label=entry.get("label") or entry["workspace_id"],
    )
    if prepare:
        for path in (project, data, logs, workspace.uploads_dir, project / ".astrion" / "skills", quarantine):
            path.mkdir(parents=True, exist_ok=True)
    return workspace

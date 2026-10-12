"""Resolve workspace identity and paths without mounting an execution runtime."""
from __future__ import annotations

from pathlib import Path

from config import DATA_DIR, LOGS_DIR, TERMINAL_SANDBOX_MODE, UPLOAD_QUARANTINE_SUBDIR
from modules.host_workspace_manager import resolve_host_workspace
from modules.i18n import tr
from modules.user_manager import UserWorkspace
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
                      *, identity: RuntimeIdentity | None = None, prepare: bool = False):
    """Explicit identities never touch Flask session; reads never select a workspace."""
    identity = identity if identity is not None else request_identity()
    host = identity.host_mode and (TERMINAL_SANDBOX_MODE or "host").lower() == "host"
    if host:
        selected = workspace_id or identity.host_workspace_id
        if not selected:
            with state.HOST_ACTIVE_WORKSPACE_LOCK:
                selected = state.HOST_ACTIVE_WORKSPACE_ID
        _catalog, entry = resolve_host_workspace(selected)
        if not entry:
            raise NoWorkspaceError(tr("context.no_workspace"))
        if workspace_id and entry.get("workspace_id") != workspace_id:
            raise ValueError(tr("host_ws.workspace_not_found"))
        project = Path(entry["path"]).expanduser().resolve()
        data = Path(DATA_DIR).expanduser().resolve()
        logs = Path(LOGS_DIR).expanduser().resolve()
        quarantine = Path(UPLOAD_QUARANTINE_SUBDIR).expanduser()
        if not quarantine.is_absolute():
            quarantine = (project.parent / quarantine).resolve()
        workspace = UserWorkspace("host", project.parent, project, data, logs,
                                  project / ".astrion" / "user_upload", quarantine)
        workspace.workspace_id = entry["workspace_id"]
        workspace.label = entry.get("label") or workspace.workspace_id
        if prepare:
            for path in (project, data, logs, workspace.uploads_dir,
                         project / ".astrion" / "skills", quarantine):
                path.mkdir(parents=True, exist_ok=True)
        return workspace
    selected = workspace_id or (None if identity.is_api_user else identity.host_workspace_id) or "default"
    if identity.is_api_user:
        if not workspace_id:
            raise RuntimeError(tr("context.missing_workspace_id"))
        return state.api_user_manager.ensure_workspace(username, selected, prepare=prepare)
    if prepare:
        return state.user_manager.ensure_user_workspace(username, selected)
    return state.user_manager.get_user_workspace(username, selected)

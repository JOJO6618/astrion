"""Workspace-scoped storage queries, independent of WebTerminal lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from server.context.identity import RuntimeIdentity
from server.context.workspace import resolve_workspace
from utils.conversation_manager import ConversationManager

_STORES: dict[tuple[str, str, str], ConversationManager] = {}
_LOCK = threading.RLock()


def _store(base, project, workspace_id) -> ConversationManager:
    key = (str(Path(base).resolve()), str(Path(project).resolve()), str(workspace_id))
    with _LOCK:
        manager = _STORES.get(key)
        if manager is None:
            manager = ConversationManager(base_dir=key[0], project_path=key[1], read_only=True)
            _STORES[key] = manager
        return manager


@dataclass(frozen=True)
class WorkspaceData:
    username: str
    workspace: object
    identity: RuntimeIdentity

    @property
    def workspace_id(self) -> str:
        return str(getattr(self.workspace, "workspace_id", None) or "default")

    def manager(self) -> ConversationManager:
        return _store(self.workspace.data_dir, self.workspace.project_path, self.workspace_id)

    def manager_for(self, conversation_id: str) -> ConversationManager:
        return self.manager()

    def list_conversations(self, limit=20, offset=0, non_empty=False):
        return self.manager().get_conversation_list(limit=limit, offset=offset, non_empty=non_empty)

    def search(self, query, limit=20):
        return self.manager().search_conversations(query, limit)


def get_workspace_data(username: str, workspace_id: str | None = None,
                       *, identity: RuntimeIdentity | None = None) -> WorkspaceData:
    if identity is None:
        from server.context.workspace import request_identity
        identity = request_identity()
    workspace = resolve_workspace(username, workspace_id, identity=identity)
    return WorkspaceData(username, workspace, identity)

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

    def manager(self, multi_agent: bool = False) -> ConversationManager:
        base = self.workspace.data_dir
        if multi_agent:
            base = Path(base).parent / 'mutiagents'
        return _store(base, self.workspace.project_path, self.workspace_id)

    def manager_for(self, conversation_id: str) -> ConversationManager:
        """Only probe the two directories of this workspace; never search another."""
        multi = self.manager(True)
        if multi._get_conversation_file_path(conversation_id).is_file():
            return multi
        return self.manager()

    def list_conversations(self, limit=20, offset=0, non_empty=False, multi_agent_mode=None):
        if multi_agent_mode is not None:
            return self.manager(multi_agent_mode).get_conversation_list(
                limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=multi_agent_mode)
        results = [self.manager(ma).get_conversation_list(
            limit=limit + offset, offset=0, non_empty=non_empty, multi_agent_mode=ma)
            for ma in (False, True)]
        rows = [row for result in results for row in result.get("conversations", [])]
        rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
        total = sum(result.get("total", 0) for result in results)
        return {"conversations": rows[offset:offset + limit], "total": total,
                "limit": limit, "offset": offset, "has_more": offset + limit < total}

    def search(self, query, limit=20, multi_agent_mode=None):
        types = (False, True) if multi_agent_mode is None else (multi_agent_mode,)
        rows = [row for ma in types for row in self.manager(ma).search_conversations(
            query, limit, multi_agent_mode=ma)]
        rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
        return rows[:limit]


def get_workspace_data(username: str, workspace_id: str | None = None,
                       *, identity: RuntimeIdentity | None = None) -> WorkspaceData:
    if identity is None:
        from server.context.workspace import request_identity
        identity = request_identity()
    workspace = resolve_workspace(username, workspace_id, identity=identity)
    return WorkspaceData(username, workspace, identity)

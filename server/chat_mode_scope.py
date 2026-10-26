"""Resolve account-owned Chat paths without provisioning a workspace/runtime."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import DATA_DIR, IS_HOST_MODE


@dataclass(frozen=True)
class ChatScope:
    username: str
    root: Path
    preferences_dir: Path


def scope_for_principal(principal) -> ChatScope:
    from server import state
    if principal.conversation_kind != "chat" or principal.workspace_id is not None:
        raise ValueError("Expected an authenticated workspace-free Chat identity")
    if principal.host_mode and IS_HOST_MODE:
        return ChatScope(principal.username, Path(DATA_DIR) / "chat", Path(DATA_DIR))
    if principal.is_api_user:
        manager = state.api_user_manager
        record = manager.list_users().get(principal.username)
        if record is None:
            raise PermissionError("Chat account unavailable")
        account = (manager.workspace_root / record.username).resolve()
        if account.parent != manager.workspace_root.resolve():
            raise PermissionError("Invalid Chat account path")
        personal = account / "personal"
        return ChatScope(record.username, personal / "chat", account / "shared" / "personalization")
    manager = state.user_manager
    record = manager.get_user(principal.username)
    if record is None:
        raise PermissionError("Chat account unavailable")
    personal = manager._personal_root(record.username)
    return ChatScope(record.username, personal / "chat", personal / "personalization")

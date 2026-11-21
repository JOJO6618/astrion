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
    """Resolve the workspace-free Chat root for an authenticated Host identity.

    历史普通用户／API 用户主体只保留可识别信息后拒绝继续执行；
    不再读取这些租户的 Chat 账户目录，也不隐式映射到 Host 的 Chat 根。
    """
    if principal.conversation_kind != "chat" or principal.workspace_id is not None:
        raise ValueError("Expected an authenticated workspace-free Chat identity")
    if principal.is_api_user or not principal.host_mode or not IS_HOST_MODE or principal.username != "host":
        raise PermissionError("Host identity required for Chat")
    return ChatScope(principal.username, Path(DATA_DIR) / "chat", Path(DATA_DIR))

"""Conversation identity and launch adaptation for stateful MCP servers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from modules.mcp_client_manager.exceptions import MCPClientError


@dataclass(frozen=True)
class MCPRuntimeScope:
    """Trusted runtime identity; never sourced from model tool arguments."""

    workspace_path: str
    data_dir: str
    conversation_id: Optional[str]
    actor_id: Optional[str] = None
    working_dir: Optional[str] = None


_PACKAGE = re.compile(r"^@playwright/mcp(?:@[^/\\]+)?$")


def reject_container_session(container_session: Any) -> None:
    """Fail closed instead of converting a legacy container session to Host MCP.

    The Host-only product keeps the ``container_session=None`` call sites for
    compatibility, but a Docker handle must never be silently served by a local
    stdio launch with Host paths.
    """
    if container_session is not None and str(getattr(container_session, "mode", "") or "").lower() == "docker":
        raise MCPClientError("当前版本仅支持宿主机 MCP，不能把容器会话转换为宿主机启动。")


def _is_playwright(server: Dict[str, Any]) -> bool:
    command = str(server.get("command") or "").replace("\\", "/")
    if command.rsplit("/", 1)[-1].lower() in {
        "playwright-mcp", "playwright-mcp.cmd", "playwright-mcp.exe",
    }:
        return True
    for token in [command, *(str(arg) for arg in server.get("args") or [])]:
        # Covers npx/npm exec packages and direct node .../@playwright/mcp/cli.js.
        value = token.replace("\\", "/")
        if _PACKAGE.fullmatch(value) or "/@playwright/mcp/" in value:
            return True
    return False


def _has_option(args: list[str], *names: str) -> bool:
    return any(arg.split("=", 1)[0] in names for arg in args)


def prepare_stdio_server(
    server: Dict[str, Any],
    scope: Optional[MCPRuntimeScope],
    container_session: Any = None,
) -> Dict[str, Any]:
    """Isolate default local Playwright profiles, without changing saved config.

    Explicit browser attachment, isolated mode, profile/config overrides retain
    their configured semantics. HTTP services manage their own browser state.
    """
    reject_container_session(container_session)
    if server.get("transport") == "stdio" and scope and scope.actor_id and scope.working_dir:
        server = dict(server)
        if not server.get("cwd"):
            server["cwd"] = scope.working_dir
    if (server.get("transport") != "stdio" or scope is None
            or not scope.conversation_id or not _is_playwright(server)):
        return server

    args = [str(arg) for arg in server.get("args") or []]
    env = dict(server.get("env") or {})
    if _has_option(
        args, "--isolated", "--user-data-dir", "--config", "--cdp-endpoint",
        "--endpoint", "--extension",
    ):
        return server

    # Host stdio inherits the backend environment; explicit server env wins.
    launch_env = dict(os.environ)
    launch_env.update({key: value for key, value in env.items() if value is not None})
    if any(str(launch_env.get(key) or "").strip() for key in (
        "PLAYWRIGHT_MCP_USER_DATA_DIR", "PLAYWRIGHT_MCP_CONFIG",
        "PLAYWRIGHT_MCP_CDP_ENDPOINT", "PLAYWRIGHT_MCP_ENDPOINT",
    )):
        return server
    if any(str(launch_env.get(key) or "").strip().lower() in {"1", "true"}
           for key in ("PLAYWRIGHT_MCP_ISOLATED", "PLAYWRIGHT_MCP_EXTENSION")):
        return server

    # Hash every identity component, keeping IDs/path separators out of filenames.
    # The data root distinguishes installations/users with identical project IDs.
    identity = [
        str(Path(scope.workspace_path).expanduser().resolve()),
        str(Path(scope.data_dir).expanduser().resolve()),
        str(scope.conversation_id),
        str(server.get("id") or ""),
    ]
    if scope.actor_id:
        identity.append(scope.actor_id)
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
    root = Path(scope.data_dir).expanduser().resolve() / "mcp_profiles"

    prepared = dict(server)
    prepared["args"] = [*args, "--user-data-dir", str(root / digest)]
    return prepared

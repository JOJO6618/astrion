"""Conversation identity and launch adaptation for stateful MCP servers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


@dataclass(frozen=True)
class MCPRuntimeScope:
    """Trusted runtime identity; never sourced from model tool arguments."""

    workspace_path: str
    data_dir: str
    conversation_id: Optional[str]


_PACKAGE = re.compile(r"^@playwright/mcp(?:@[^/\\]+)?$")


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
    container_session: Optional["ContainerHandle"],
) -> Dict[str, Any]:
    """Isolate default local Playwright profiles, without changing saved config.

    Explicit browser attachment, isolated mode, profile/config overrides retain
    their configured semantics. HTTP services manage their own browser state.
    """
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

    # Host MCP inherits the backend environment; docker exec only forwards the
    # server's explicit env. Do not accidentally override either source.
    docker = bool(container_session and container_session.mode == "docker")
    launch_env = {} if docker else dict(os.environ)
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
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
    if docker:
        # The workspace bind mount survives container recreation. Do not pass an
        # unmounted host data path to docker or add any new host mounts.
        mount = str(container_session.mount_path or "/workspace")
        root = PurePosixPath(mount) / ".astrion" / "mcp_profiles"
    else:
        root = Path(scope.data_dir).expanduser().resolve() / "mcp_profiles"

    prepared = dict(server)
    prepared["args"] = [*args, "--user-data-dir", str(root / digest)]
    return prepared

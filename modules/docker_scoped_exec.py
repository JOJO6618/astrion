"""Build the fixed child authority inside the existing Docker toolbox."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List

from modules.execution_scope import current_execution_scope
from modules.scoped_execution_policy import require_scoped_docker_support

_LAUNCHER = Path(__file__).with_name("docker_scoped_launcher.py").read_text(encoding="utf-8")


def wrap_scoped_docker_command(mount_path: str, command: List[str], scope=None) -> List[str]:
    scope = scope if scope is not None else current_execution_scope()
    require_scoped_docker_support(scope)
    if not scope or not scope.workspace_only:
        return command
    actor = hashlib.sha256(scope.actor_id.encode("utf-8")).hexdigest()[:24]
    # Isolated Python + a clean environment prevent user startup hooks from
    # running before the mandatory domain has been installed.
    return [
        "/usr/bin/env", "-i",
        "PATH=/opt/agent-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG=C.UTF-8", "PYTHONUNBUFFERED=1", "GIT_CONFIG_GLOBAL=/dev/null",
        "GIT_CONFIG_COUNT=1", "GIT_CONFIG_KEY_0=safe.directory", "GIT_CONFIG_VALUE_0=*",
        "python3", "-I", "-S", "-c", _LAUNCHER,
        (mount_path or "/workspace").rstrip("/"), actor, *command,
    ]

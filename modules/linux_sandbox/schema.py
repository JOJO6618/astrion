"""Strict request schema. OS identity and privileged paths never come from JSON."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

try:
    from .constants import MAX_ARGUMENTS, MAX_PACKET, MAX_PATHS, RUNTIME_ROOT, SAFE_ENVIRONMENT
except ImportError:  # Isolated, root-owned helper installation.
    from constants import MAX_ARGUMENTS, MAX_PACKET, MAX_PATHS, RUNTIME_ROOT, SAFE_ENVIRONMENT


class RequestError(ValueError):
    pass


def absolute_path(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value or len(value.encode()) > 4096:
        raise RequestError("Expected a bounded absolute Linux path")
    path = PurePosixPath(value)
    if ".." in path.parts or str(path) != value or value == "/":
        raise RequestError("Root, relative, and non-normalized paths are not sandbox grants")
    for reserved in (RUNTIME_ROOT, "/proc", "/dev", "/sys"):
        if value == reserved or value.startswith(reserved + "/"):
            raise RequestError("Kernel and internal runtime directories cannot be host mount grants")
    return value


def path_list(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PATHS:
        raise RequestError("Invalid sandbox path list")
    return list(dict.fromkeys(absolute_path(item) for item in value))


def validate_request(request: object) -> dict:
    if not isinstance(request, dict):
        raise RequestError("Expected a sandbox request object")
    if request.get("operation") == "status":
        if set(request) != {"operation"}:
            raise RequestError("Unexpected status fields")
        return request
    fields = {"operation", "workspace", "cwd", "reads", "writes", "readonly", "workspace_only",
              "network", "argv", "env", "umask"}
    if set(request) != fields or request.get("operation") != "run":
        raise RequestError("Invalid sandbox request fields")
    workspace = absolute_path(request["workspace"])
    cwd = absolute_path(request["cwd"])
    if not PurePosixPath(cwd).is_relative_to(workspace):
        raise RequestError("Working directory is outside the workspace")
    if type(request["readonly"]) is not bool or type(request["workspace_only"]) is not bool:
        raise RequestError("Invalid sandbox write policy")
    if request["network"] not in {"full", "restricted", "none"}:
        raise RequestError("Invalid sandbox network policy")
    argv = request["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= MAX_ARGUMENTS:
        raise RequestError("Invalid command arguments")
    if any(not isinstance(item, str) or "\0" in item or len(item.encode()) > MAX_PACKET for item in argv):
        raise RequestError("Invalid command argument")
    environment = request["env"]
    if not isinstance(environment, dict) or set(environment) - SAFE_ENVIRONMENT:
        raise RequestError("Unapproved environment variable")
    if any(not isinstance(value, str) or "\0" in value or len(value.encode()) > 8192 for value in environment.values()):
        raise RequestError("Invalid command environment")
    if type(request["umask"]) is not int or not 0 <= request["umask"] <= 0o777:
        raise RequestError("Invalid command creation mask")
    result = dict(request)
    result["reads"] = path_list(request["reads"])
    result["writes"] = path_list(request["writes"])
    if len(set(result["reads"] + result["writes"] + [workspace])) > MAX_PATHS:
        raise RequestError("Too many combined sandbox grants")
    if (request["readonly"] or request["workspace_only"]) and result["writes"]:
        raise RequestError("Extra persistent writes are forbidden for this scope")
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_PACKET:
        raise RequestError("Sandbox request is too large")
    return result

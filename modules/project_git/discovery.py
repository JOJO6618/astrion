"""Discover repositories strictly beneath registered workspace roots."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import threading
import time

from .runner import GitQueryError, text

# Dependency/runtime trees are not project discovery roots. Nested source repos
# elsewhere (including ordinary hidden directories) remain discoverable.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".astrion",
             ".codegraph", ".cache", ".next", ".nuxt"}
_LOCK = threading.RLock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
TTL = 60


def repository_id(path: Path) -> str:
    return hashlib.sha256(os.fsencode(str(path))).hexdigest()[:24]


def discover(workspaces: list[dict], *, refresh: bool = False) -> dict:
    key = tuple(sorted((w["workspace_id"], str(w["path"])) for w in workspaces))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < TTL:
            return cached[1]
    repositories: dict[str, dict] = {}
    warnings: list[dict] = []
    for workspace in workspaces:
        root = Path(workspace["path"]).resolve()
        if not root.is_dir():
            warnings.append({"workspace_id": workspace["workspace_id"], "error": "工作区目录不存在或不可访问。"})
            continue
        def walk_error(error):
            warnings.append({"workspace_id": workspace["workspace_id"], "error": str(error)})
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                             and not (Path(folder) / d).is_symlink())
            marker = Path(folder) / ".git"
            if not marker.exists() or marker.is_symlink():
                continue
            try:
                candidate = Path(folder).resolve()
                candidate.relative_to(root)
                actual = Path(text(candidate, ["rev-parse", "--show-toplevel"]).strip()).resolve()
                actual.relative_to(root)
                if actual != candidate:
                    continue
                repo_id = repository_id(actual)
                relative = str(actual.relative_to(root))
                association = {"workspace_id": workspace["workspace_id"],
                               "label": workspace.get("label") or workspace["workspace_id"],
                               "relative_path": relative}
                if repo_id not in repositories:
                    repositories[repo_id] = {"id": repo_id, "name": actual.name,
                                             "path": str(actual), "workspaces": []}
                repositories[repo_id]["workspaces"].append(association)
            except (OSError, ValueError, GitQueryError) as exc:
                warnings.append({"workspace_id": workspace["workspace_id"], "error": str(exc)})
    result = {"repositories": sorted(repositories.values(), key=lambda r: r["path"].casefold()),
              "workspaces": [{"workspace_id": w["workspace_id"], "label": w.get("label"),
                              "path": str(w["path"])} for w in workspaces], "warnings": warnings}
    with _LOCK:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), result)
    return result


def resolve_repository(workspaces: list[dict], repo_id: str, workspace_id: str) -> tuple[Path, dict]:
    workspace = next((w for w in workspaces if w["workspace_id"] == workspace_id), None)
    if not workspace:
        raise GitQueryError("工作区不存在或无权访问。")
    repository = next((r for r in discover(workspaces)["repositories"] if r["id"] == repo_id), None)
    if not repository or not any(w["workspace_id"] == workspace_id for w in repository["workspaces"]):
        raise GitQueryError("仓库不属于该工作区，请刷新仓库列表。")
    path = Path(repository["path"])
    root = Path(workspace["path"]).resolve()
    # Revalidate cached discoveries after directory moves or symlink replacement.
    if path.resolve() != path or not path.is_dir() or (path / ".git").is_symlink():
        raise GitQueryError("仓库路径已变化，请刷新仓库列表。")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GitQueryError("仓库超出已注册工作区范围。") from exc
    if Path(text(path, ["rev-parse", "--show-toplevel"]).strip()).resolve() != path:
        raise GitQueryError("仓库路径已变化，请刷新仓库列表。")
    return path, workspace

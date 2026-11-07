"""Private workspace memory locations and implicit Host sandbox roots.

Only trusted resource construction registers a binding. Policy reads never create
files or grant access to the shared data root. Docker exposes a dedicated bind
under the project; its backing directory is never part of the code tree.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import threading

RELATIVE_DIRECTORY = ".astrion/astrion-memory"
RELATIVE_FILE = f"{RELATIVE_DIRECTORY}/MEMORY.md"
SNAPSHOT_KEY = "workspace_memory_snapshot"
ENABLED_KEY = "workspace_memory_enabled"
PROMPT_KEY = "frozen_workspace_memory_prompt"

_BINDINGS: dict[str, Path] = {}
_LOCK = threading.RLock()


@dataclass(frozen=True)
class MemoryLocation:
    directory: Path
    tool_path: str
    docker: bool = False

    @property
    def file(self) -> Path:
        return self.directory / "MEMORY.md"


def location(project_path: str, data_dir: str, *, docker: bool = False) -> MemoryLocation:
    project = Path(project_path).expanduser().resolve()
    data = Path(data_dir).expanduser().resolve()
    if docker:
        directory = data / "memory"
        return MemoryLocation(directory, RELATIVE_FILE, True)
    identity = os.path.normcase(str(project))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^\w.-]+", "-", project.name, flags=re.UNICODE).strip(".-")[:48] or "workspace"
    directory = data / "memory" / f"{slug}-{digest}"
    return MemoryLocation(directory, str(directory / "MEMORY.md"))


def register_host_memory(project_path: str, data_dir: str) -> None:
    """Register an exact workspace binding, without creating runtime data."""
    if not data_dir:
        return
    target = location(project_path, data_dir)
    with _LOCK:
        _BINDINGS[os.path.normcase(str(Path(project_path).resolve()))] = target.directory


def host_memory_roots(workspace_path: str | None) -> list[str]:
    if not workspace_path:
        return []
    key = os.path.normcase(str(Path(workspace_path).expanduser().resolve()))
    with _LOCK:
        directory = _BINDINGS.get(key)
    if directory is None or directory.is_symlink() or directory.parent.is_symlink():
        return []
    return [str(directory)]


def prepare_directory(target: MemoryLocation) -> None:
    """Prepare only the memory namespace; reject links within that namespace."""
    # DATA_DIR is a trusted resolved root; descendants must not redirect it.
    namespace = target.directory if target.docker else target.directory.parent
    for directory in dict.fromkeys((namespace, target.directory)):
        if directory.is_symlink():
            raise PermissionError(f"记忆目录不能是符号链接：{directory}")
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        if not directory.is_dir():
            raise PermissionError(f"记忆路径不是目录：{directory}")
    if target.file.is_symlink():
        raise PermissionError("MEMORY.md 不能是符号链接")
    if target.docker:
        # The container sees this directory inside the repository. A private
        # ignore file travels with the bind, without editing public .gitignore.
        ignore = target.directory / ".gitignore"
        if ignore.is_symlink():
            raise PermissionError("记忆目录的 .gitignore 不能是符号链接")
        try:
            fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            if ignore.read_text(encoding="utf-8") != "*\n":
                raise PermissionError("记忆目录的 .gitignore 必须保持忽略全部内容")
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("*\n")


def content_version(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

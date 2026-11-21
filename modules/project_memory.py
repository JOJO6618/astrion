"""Storage identity and shared lookup for indexed Host workspace memories."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO, Callable
from uuid import uuid4

from config import WORKSPACE_MEMORY_DIRNAME
from modules.execution_plane.access import get_execution_backend, is_local_backend
from modules.execution_scope import uses_docker
from modules.workspace_memory import location, prepare_directory

NOTICE_NAME = "00_READ_ME_项目记忆已迁移.md"
TOPICS_DIRNAME = "topics"


def topic_directory(project_path: str, data_dir: str) -> Path:
    return location(project_path, data_dir).directory / TOPICS_DIRNAME


def memory_filename(name: str) -> bool:
    return name.endswith(".md") and name != NOTICE_NAME


def legacy_files(project: Path) -> list[Path]:
    directory = project / WORKSPACE_MEMORY_DIRNAME
    for component in (project, directory.parent, directory):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise PermissionError(f"项目记忆路径不能经过链接：{component}")
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []
    return [entry for entry in entries if memory_filename(entry.name)
            and not stat.S_ISDIR(entry.lstat().st_mode)]


def prepare_topic_directory(project_path: str, data_dir: str) -> Path:
    memory = location(project_path, data_dir)
    prepare_directory(memory)
    target = memory.directory / TOPICS_DIRNAME
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise PermissionError(f"项目记忆目标目录不能是链接：{target}")
    target.mkdir(exist_ok=True)
    return target


def publish_memory_file(path: Path, content: bytes | Callable[[BinaryIO], None], *, mode: int, mtime_ns: int | None = None) -> None:
    """Publish a complete file under an unused name without following a substituted parent."""
    def write(fd):
        with os.fdopen(fd, "wb") as stream:
            if isinstance(content, bytes):
                stream.write(content)
            else:
                content(stream)
            stream.flush()
            if os.name == "posix":
                os.fchmod(stream.fileno(), mode)
                if mtime_ns is not None:
                    os.utime(stream.fileno(), ns=(mtime_ns, mtime_ns))
            os.fsync(stream.fileno())

    if os.name == "posix":
        from modules.file_manager.scoped_io import checked_parent

        with checked_parent(path) as (parent, name):
            temporary = f".astrion-memory-{uuid4().hex}"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=parent)
            try:
                write(fd)
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            finally:
                os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
        return

    fd, temporary = tempfile.mkstemp(prefix=".astrion-memory-", dir=path.parent)
    try:
        write(fd)
        if mtime_ns is not None:
            os.utime(temporary, ns=(mtime_ns, mtime_ns))
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def _host_topic_location(owner) -> tuple[str, str] | None:
    if not is_local_backend(owner):
        return None
    # A legacy container session is rejected rather than served the Host path.
    if uses_docker(owner):
        raise PermissionError("当前版本仅支持宿主机专题记忆，不能为容器会话解析目录")
    data_dir = getattr(owner, "data_dir", None)
    return (str(owner.project_path), str(data_dir)) if data_dir else None


def tool_directory(owner) -> str:
    # A non-local backend owns the logical workspace namespace itself.
    host = _host_topic_location(owner)
    if host is None:
        from modules.git_worktree.paths import result_path
        return result_path(owner, WORKSPACE_MEMORY_DIRNAME)
    target = topic_directory(*host)
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise PermissionError("专题记忆目录不能是链接。")
    return str(target)


def prepare_tool_write(owner) -> None:
    host = _host_topic_location(owner)
    if host is not None:
        prepare_topic_directory(*host)


def scan_index(owner) -> list[dict]:
    """Read the Host topic index through the bound filesystem."""
    files = get_execution_backend(owner).files
    directory = tool_directory(owner)
    listing = files.list_files(directory)
    if not listing.get("success"):
        return []
    entries = []
    for item in listing.get("files", []):
        filename = item.get("name", "")
        if not memory_filename(filename) or item.get("type") not in (None, "file"):
            continue
        path = f"{directory}/{filename}"
        info = files.inspect_path(path)
        if not info.get("success") or not info.get("is_file") or info.get("is_symlink"):
            continue
        text = files.read_file(path).get("content")
        if not isinstance(text, str):
            continue
        lines = text.splitlines()
        description = ""
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                key, _, value = line.partition(":")
                if key.strip() == "description":
                    description = value.strip()
        entries.append({"file": filename, "name": Path(filename).stem,
                        "description": description, "mtime": info.get("mtime") or 0})
    entries.sort(key=lambda item: (-item["mtime"], item["file"]))
    return entries


def index_lines(entries: list[dict]) -> str:
    return "\n".join(
        f"{item['name']}：{item['description']}" if item['description'] else item['name']
        for item in entries
    )

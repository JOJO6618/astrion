"""Storage identity and shared lookup for indexed workspace memories."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO, Callable
from uuid import uuid4

from config import WORKSPACE_MEMORY_DIRNAME
from modules.execution_plane.access import get_execution_backend, is_local_backend
from modules.workspace_memory import location, prepare_directory

NOTICE_NAME = "00_READ_ME_项目记忆已迁移.md"
TOPICS_DIRNAME = "topics"
_DOCKER_ACTIVATED = ".project-memory-topics-external"


def topic_directory(project_path: str, data_dir: str, *, docker: bool = False) -> Path:
    return location(project_path, data_dir, docker=docker).directory / TOPICS_DIRNAME


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


def prepare_topic_directory(project_path: str, data_dir: str, *, docker: bool = False) -> Path:
    memory = location(project_path, data_dir, docker=docker)
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


def activate_docker_topics(target: Path) -> None:
    """Publish the selected storage location before removing any old files."""
    from modules.file_manager.scoped_io import open_checked

    for path in (target / ".gitignore", target.parent.parent / _DOCKER_ACTIVATED):
        try:
            publish_memory_file(path, b"*\n", mode=0o644)
        except FileExistsError:
            with open_checked(path) as stream:
                if stream.read() != "*\n":
                    raise PermissionError(f"项目记忆挂载标记无效：{path}")


def docker_bind_directory(project_path: str, data_dir: str) -> Path | None:
    """Keep the legacy view until all copies have been verified."""
    target = topic_directory(project_path, data_dir, docker=True)
    if not (target.parent.parent / _DOCKER_ACTIVATED).is_file() and legacy_files(Path(project_path)):
        return None
    target = prepare_topic_directory(project_path, data_dir, docker=True)
    activate_docker_topics(target)
    return target


def _host_topic_location(owner) -> tuple[str, str] | None:
    if not is_local_backend(owner) or getattr(getattr(owner, "container_session", None), "mode", None) == "docker":
        return None
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
    """Read through the bound filesystem, including a Docker bind over the old path."""
    files = get_execution_backend(owner).files
    directory = tool_directory(owner)
    from modules.execution_plane.files import LocalFileSystem
    if isinstance(files, LocalFileSystem):
        result = files.container_project_memory_index(directory, NOTICE_NAME)
        if result is not None:
            return sorted(result.get("entries", []), key=lambda item: (-item["mtime"], item["file"]))
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

"""Copy, verify and remove legacy indexed memories without overwriting either copy."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat

from config import WORKSPACE_MEMORY_DIRNAME
from modules.file_manager.scoped_io import checked_parent, open_checked, unlink_checked
from modules.project_memory import (
    NOTICE_NAME, legacy_files, prepare_topic_directory,
    publish_memory_file, topic_directory,
)


@dataclass(frozen=True)
class WorkspaceMemories:
    user: str
    workspace_id: str
    label: str
    project: Path
    data: Path
    files: tuple[Path, ...]

    @property
    def destination(self) -> Path:
        return topic_directory(str(self.project), str(self.data))


@dataclass(frozen=True)
class VerifiedCopy:
    source: Path
    destination: Path
    digest: str
    source_identity: tuple[int, int, int, int, int]


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _fingerprint(path: Path) -> tuple[str, os.stat_result]:
    digest = hashlib.sha256()
    with open_checked(path, "rb") as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
        if _identity(before) != _identity(os.fstat(stream.fileno())):
            raise OSError(f"文件在读取时发生变化：{path.name}")
    return digest.hexdigest(), before


def _stream_source(source: Path, expected: os.stat_result, output) -> None:
    with open_checked(source, "rb") as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(expected):
            raise OSError(f"旧文件在复制前发生变化：{source.name}")
        shutil.copyfileobj(stream, output, length=256 * 1024)
        if _identity(os.fstat(stream.fileno())) != _identity(expected):
            raise OSError(f"旧文件在复制时发生变化：{source.name}")


def copy_workspace(workspace: WorkspaceMemories, on_copied) -> list[VerifiedCopy]:
    target = prepare_topic_directory(str(workspace.project), str(workspace.data))
    copies = []
    for source in workspace.files:
        digest, info = _fingerprint(source)
        for attempt in range(1000):
            suffix = "" if attempt == 0 else f"_migrated_{digest[:12]}" + (f"_{attempt}" if attempt > 1 else "")
            destination = target / (f"{source.stem[:45]}{suffix}.md" if attempt else source.name)
            try:
                existing, _ = _fingerprint(destination)
            except FileNotFoundError:
                try:
                    publish_memory_file(destination, lambda output: _stream_source(source, info, output),
                                        mode=0o600, mtime_ns=info.st_mtime_ns)
                except FileExistsError:
                    # A writer claimed this filename while the copy was streaming.
                    continue
            except (PermissionError, IsADirectoryError):
                continue
            else:
                if existing != digest:
                    continue
            copies.append(VerifiedCopy(source, destination, digest, _identity(info)))
            on_copied()
            break
        else:
            raise FileExistsError(f"找不到可用的目标文件名：{source.name}")
    return copies


def _verify_copy(copy: VerifiedCopy) -> None:
    source, info = _fingerprint(copy.source)
    destination, _ = _fingerprint(copy.destination)
    if _identity(info) != copy.source_identity or source != copy.digest:
        raise OSError(f"旧文件在迁移时发生变化：{copy.source.name}")
    if destination != source:
        raise OSError(f"目标文件内容核验失败：{copy.destination.name}")


def _write_notice(workspace: WorkspaceMemories) -> None:
    notice = workspace.project / WORKSPACE_MEMORY_DIRNAME / NOTICE_NAME
    text = (
        "# Astrion 项目记忆已迁移\n\n"
        f"本工作区的专题项目记忆已迁至 `{workspace.destination}`。\n\n"
        "查找专题请用 `search_project_memory`，知道名称时用 `recall_project_memory`，"
        "新增或更新用 `update_project_memory`。这些工具会定位当前工作区的记忆。"
        "请在新位置维护后续专题记忆。\n"
    )
    try:
        publish_memory_file(notice, text.encode("utf-8"), mode=0o644)
    except FileExistsError:
        with open_checked(notice) as stream:
            if stream.read() != text:
                raise FileExistsError(f"已有同名迁移说明，请检查：{notice}")


def verify_workspace(workspace: WorkspaceMemories, copies: list[VerifiedCopy], on_verified) -> None:
    if set(legacy_files(workspace.project)) != set(workspace.files):
        raise OSError("工作区的待迁移文件列表发生变化，请重试。")
    for copy in copies:
        _verify_copy(copy)
        on_verified()


def clean_workspace(workspace: WorkspaceMemories, copies: list[VerifiedCopy]) -> None:
    verify_workspace(workspace, copies, lambda: None)
    _write_notice(workspace)
    for copy in copies:
        # Recheck immediately before removing each source. A file edited during
        # verification remains on disk and can be imported on a later retry.
        _verify_copy(copy)
        if os.name == "posix":
            with checked_parent(copy.source) as (fd, name):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or _identity(info) != copy.source_identity:
                    raise OSError(f"旧文件在清理时发生变化：{copy.source.name}")
                os.unlink(name, dir_fd=fd)
        else:
            if _identity(copy.source.lstat()) != copy.source_identity:
                raise OSError(f"旧文件在清理时发生变化：{copy.source.name}")
            unlink_checked(copy.source)

"""POSIX descriptor-based native IO for scoped sandbox file operations.

Resolve authorization first, then walk from / using O_NOFOLLOW directory fds.
Never follow a substituted symlink while opening or mutating a checked target.
Windows lacks these Python dir_fd primitives, so scoped native writes fail closed.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat

from modules.execution_scope import current_execution_scope


def sandbox_scope():
    scope = current_execution_scope()
    return scope if (
        scope and scope.execution_mode != "direct"
        and (scope.is_sub_agent or os.name == "posix")
    ) else None


def _require_posix():
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise PermissionError("Scoped native file IO requires POSIX no-follow directory descriptors; operation refused")


def _dir_flags():
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


@contextmanager
def checked_parent(path, *, create=False, create_roots=None):
    _require_posix()
    target = Path(path)
    if not target.is_absolute() or ".." in target.parts or target == Path("/"):
        raise PermissionError("Scoped file operation requires a checked absolute non-root path")
    fd = os.open("/", _dir_flags())
    try:
        walked = Path("/")
        for component in target.parts[1:-1]:
            walked = walked / component
            try:
                child = os.open(component, _dir_flags(), dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                if not any(walked == root or root in walked.parents for root in (create_roots or [])):
                    raise PermissionError("Cannot create a missing ancestor outside scoped writable roots")
                try:
                    os.mkdir(component, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(component, _dir_flags(), dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, target.name
    finally:
        os.close(fd)


def open_checked(path, mode="r", encoding="utf-8", create_roots=None):
    if os.name == "nt":
        from .windows_io import open_checked as windows_open
        return windows_open(path, mode, encoding, create_roots)
    _require_posix()
    if mode not in {"r", "rb", "w", "a", "x"}:
        raise ValueError("Unsupported scoped file mode")
    writing = mode not in {"r", "rb"}
    with checked_parent(path, create=writing, create_roots=create_roots) as (parent_fd, name):
        flags = (os.O_WRONLY if writing else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        if writing:
            flags |= os.O_CREAT
        if mode == "x":
            flags |= os.O_EXCL
        if mode == "a":
            flags |= os.O_APPEND
        # Do not truncate until fstat verifies that a swapped target is regular
        # and has no hard-link aliases which could point outside authorized roots.
        fd = os.open(name, flags, 0o666, dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("Scoped file IO refuses special files and multiply linked files")
            if mode == "w":
                os.ftruncate(fd, 0)
            stream = os.fdopen(fd, mode, **({} if "b" in mode else {"encoding": encoding}))
        except BaseException:
            os.close(fd)
            raise
        return stream


def mkdir_checked(path, create_roots=None, exist_ok=True):
    if os.name == "nt":
        from .windows_io import mkdir_checked as windows_mkdir
        return windows_mkdir(path, create_roots, exist_ok)
    with checked_parent(path, create=True, create_roots=create_roots) as (fd, name):
        try:
            os.mkdir(name, dir_fd=fd)
        except FileExistsError:
            if not exist_ok:
                raise
            child = os.open(name, _dir_flags(), dir_fd=fd)
            os.close(child)


def unlink_checked(path):
    if os.name == "nt":
        from .windows_io import unlink_checked as windows_unlink
        return windows_unlink(path)
    with checked_parent(path) as (fd, name):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError("Scoped delete requires a regular file")
        os.unlink(name, dir_fd=fd)


def rename_checked(source, target):
    if os.name == "nt":
        from .windows_io import rename_checked as windows_rename
        return windows_rename(source, target)
    with checked_parent(source) as (src_fd, src_name), checked_parent(target) as (dst_fd, dst_name):
        info = os.stat(src_name, dir_fd=src_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError("Scoped rename requires a regular file")
        os.rename(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)


def rmtree_checked(path):
    if os.name == "nt":
        from .windows_io import rmtree_checked as windows_rmtree
        return windows_rmtree(path)
    if not shutil.rmtree.avoids_symlink_attacks or os.rmdir not in os.supports_dir_fd:
        raise PermissionError("Symlink-safe recursive deletion unavailable; operation refused")
    with checked_parent(path) as (fd, name):
        child = os.open(name, _dir_flags(), dir_fd=fd)
        try:
            _rmtree_contents(child)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=fd)


def _rmtree_contents(fd):
    # os.scandir accepts an fd and keeps its own duplicate alive for iteration.
    with os.scandir(fd) as entries:
        for entry in entries:
            info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, _dir_flags(), dir_fd=fd)
                try:
                    _rmtree_contents(child)
                finally:
                    os.close(child)
                os.rmdir(entry.name, dir_fd=fd)
            else:
                # unlink is no-follow; an inner symlink is removed, never traversed.
                os.unlink(entry.name, dir_fd=fd)

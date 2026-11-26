"""Process-wide writer lock shared with the Rust Astrion backend."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
from typing import BinaryIO

LOCK_FILE_NAME = 'astrion-backend.lock'


class InstanceLockError(RuntimeError):
    """The shared backend lock could not be acquired."""


def acquire_instance_lock(data_dir: str | os.PathLike[str]) -> BinaryIO:
    """Return the locked file. Closing it releases the OS lock and preserves its inode."""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOCK_FILE_NAME
    if path.is_symlink():
        raise InstanceLockError(f'Invalid Astrion backend lock file: {path}')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    lock = os.fdopen(os.open(path, flags, 0o600), 'r+b', buffering=0)
    try:
        info = os.fstat(lock.fileno())
        entry = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(entry.st_mode)
                or info.st_nlink != 1 or not os.path.samestat(info, entry)
                or getattr(entry, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise InstanceLockError(f'Invalid Astrion backend lock file: {path}')
        try:
            if os.name == 'nt':
                import msvcrt

                # Locking past EOF is supported on Windows. Rust locks an overlapping range.
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise InstanceLockError(f'Another Astrion backend is using this data directory: {directory}') from None
            raise
        return lock
    except BaseException:
        lock.close()
        raise

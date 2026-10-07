"""Managers addressing the same directory share one read/write transaction lock."""
from __future__ import annotations

from functools import wraps
from pathlib import Path
import threading

_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def directory_lock(path: Path):
    key = str(path.resolve())
    with _registry_lock:
        return _locks.setdefault(key, threading.RLock())


def locked_write(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._io_lock:
            return function(self, *args, **kwargs)
    return wrapped

"""Prepare legacy storage once per directory; ordinary reads never provision it."""
from __future__ import annotations

import threading

_PREPARED: set[tuple[str, str]] = set()
_LOCK = threading.RLock()


def prepare_storage(manager) -> None:
    key = (str(manager.conversations_root), str(manager.conversations_dir))
    with _LOCK, manager._io_lock:
        if key in _PREPARED and manager.index_file.exists():
            manager._index_verified = True
            return
        manager._ensure_directories()
        manager._load_index(ensure_integrity=True, max_rebuild=20)
        _PREPARED.add(key)

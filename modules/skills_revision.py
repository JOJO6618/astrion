"""Content identity for skill catalogs and workspace copies."""
from __future__ import annotations

import os
from pathlib import Path


def tree_revision(root: Path) -> tuple:
    """Observe every file/dir, including deletions and same-size replacements."""
    entries = []
    if not root.is_dir():
        return ()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted([*dirs, *files]):
            path = Path(directory) / name
            stat = path.lstat()
            entries.append((str(path.relative_to(root)), stat.st_mode, stat.st_size,
                            stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
    return tuple(entries)


def catalog_revision(root: Path) -> tuple:
    if not root.is_dir():
        return ()
    entries = []
    for child in sorted(root.iterdir()):
        skill = child / "SKILL.md"
        if child.is_dir() and skill.is_file():
            stat = skill.stat()
            entries.append((child.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
    return tuple(entries)

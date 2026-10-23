"""Ordered, atomic workspace drafts, with no execution-resource dependency."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from utils.conversation_manager.locking import directory_lock
from utils.atomic_io import replace_with_retry


def read_draft(path: Path) -> dict:
    with directory_lock(path.parent):
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid composer draft")
        return raw


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_draft(path: Path, payload: dict, *, client_id="", sequence=0,
               base_revision=None) -> bool:
    with directory_lock(path.parent):
        previous = read_draft(path)
        versions = dict(previous.get("writer_sequences") or {})
        if client_id and sequence:
            if sequence <= int(versions.get(client_id) or 0):
                return False
            versions.pop(client_id, None)
            versions[client_id] = sequence
            versions = dict(list(versions.items())[-32:])
        revision = int(previous.get("revision") or 0)
        if base_revision is not None and int(base_revision) != revision:
            raise ValueError("Draft changed in another window; local text has been retained")
        # Empty drafts retain ordering and revision watermarks.
        atomic_write(path, {**payload, "writer_sequences": versions, "revision": revision + 1})
        return True

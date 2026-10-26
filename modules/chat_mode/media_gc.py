"""Collect unreferenced media after an upload grace period, scanning every branch."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from utils.media_store import MediaStore

GRACE_SECONDS = 24 * 60 * 60


def references(value):
    result = set()
    if isinstance(value, dict):
        if isinstance(value.get("media_id"), str):
            result.add(value["media_id"])
        for child in value.values():
            result.update(references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(references(child))
    return result


def collect(storage, *, now=None):
    """Fail closed on corrupt references. Shared lock order: conversation -> media."""
    now = time.time() if now is None else now
    removed = []
    with storage.manager._io_lock:
        live = set()
        documents = {}
        paths = list(storage.manager.conversations_dir.glob("conv_*.json"))
        paths += list((storage.root / "drafts").glob("*.json"))
        for path in paths:
            value = json.loads(path.read_text(encoding="utf-8"))
            live.update(references(value))
            if path.name.startswith("conv_") and path.parent == storage.manager.conversations_dir:
                documents[value["id"]] = value
        for path in (storage.root / "handoffs").glob("*/snapshot.json"):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            document = documents.get(snapshot.get("id"))
            if document:
                states = document["metadata"]["chat"].get("handoffs", {}).values()
                if any(state["id"] == path.parent.name and state["status"] not in
                       {"completed", "declined", "superseded"} for state in states):
                    live.update(references(snapshot))
        store = MediaStore(storage.root)
        with store._lock:
            index = json.loads(store.index_file.read_text(encoding="utf-8"))
            media = index["media"]
            root = store.root_dir.resolve()
            for identity, entry in list(media.items()):
                if identity in live:
                    continue
                try:
                    updated = datetime.fromisoformat(entry["updated_at"].replace("Z", "+00:00"))
                    # MediaStore timestamps use the local timezone when naive.
                    age = now - updated.timestamp()
                except (ValueError, KeyError, TypeError):
                    continue
                if age < GRACE_SECONDS:
                    continue
                relative = Path(entry.get("blob_rel_path") or "")
                blob = (root / relative).resolve()
                if relative.is_absolute() or not blob.is_relative_to(store.blobs_dir.resolve()):
                    raise ValueError("Invalid media blob path; collection aborted")
                if blob.is_file():
                    blob.unlink()
                del media[identity]
                removed.append(identity)
            index["message_map"] = {key: value for key, value in index.get("message_map", {}).items()
                                    if key in documents}
            if removed:
                store._save_index(index)
    return removed

"""One quota tracker per account, independent of workspace provisioning."""
import json
from pathlib import Path
from threading import RLock

from modules.usage_tracker import UsageTracker
from utils.atomic_io import atomic_write_json

_LOCK = RLock()
_TRACKERS = {}


def account_tracker(personal_root, role="user"):
    personal = Path(personal_root).resolve()
    directory = personal / "usage"
    key = str(directory)
    with _LOCK:
        tracker = _TRACKERS.get(key)
        if tracker is None:
            target = directory / "usage_stats.json"
            if not target.exists():
                # Earlier releases cached exactly one workspace tracker per user.
                # Preserve the most recently saved snapshot without creating any
                # workspace or summing potentially overlapping legacy counters.
                candidates = list((personal.parent / "projects").glob("*/data/usage_stats.json"))
                candidates += [personal / "chat" / "usage_stats.json"]
                existing = [path for path in candidates if path.is_file()]
                if existing:
                    latest = max(existing, key=lambda path: path.stat().st_mtime_ns)
                    value = json.loads(latest.read_text(encoding="utf-8"))
                    if not isinstance(value, dict):
                        raise ValueError("Invalid legacy account quota snapshot")
                    atomic_write_json(target, value)
            tracker = UsageTracker(str(directory), role=role)
            _TRACKERS[key] = tracker
        tracker.role = role or "user"
        return tracker

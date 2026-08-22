from __future__ import annotations
import time
from typing import Optional, Dict, Any
from .utils_common import debug_log
from .state import MONITOR_SNAPSHOT_CACHE, MONITOR_SNAPSHOT_CACHE_LIMIT

__all__ = ["cache_monitor_snapshot", "get_cached_monitor_snapshot"]


def cache_monitor_snapshot(execution_id: Optional[str], stage: str, snapshot: Optional[Dict[str, Any]]):
    """缓存工具执行前/后的文件快照。"""
    if not execution_id or not snapshot or not snapshot.get('content'):
        return
    normalized_stage = 'after' if stage == 'after' else 'before'
    entry = MONITOR_SNAPSHOT_CACHE.get(execution_id) or {
        'before': None,
        'after': None,
        'path': snapshot.get('path'),
        'timestamp': 0.0
    }
    entry[normalized_stage] = {
        'path': snapshot.get('path'),
        'content': snapshot.get('content'),
        'lines': snapshot.get('lines') if snapshot.get('lines') is not None else None
    }
    entry['path'] = snapshot.get('path') or entry.get('path')
    entry['timestamp'] = time.time()
    MONITOR_SNAPSHOT_CACHE[execution_id] = entry
    if len(MONITOR_SNAPSHOT_CACHE) > MONITOR_SNAPSHOT_CACHE_LIMIT:
        try:
            oldest_key = min(
                MONITOR_SNAPSHOT_CACHE.keys(),
                key=lambda key: MONITOR_SNAPSHOT_CACHE[key].get('timestamp', 0.0)
            )
            MONITOR_SNAPSHOT_CACHE.pop(oldest_key, None)
        except ValueError:
            pass


def get_cached_monitor_snapshot(execution_id: Optional[str], stage: str) -> Optional[Dict[str, Any]]:
    if not execution_id:
        return None
    entry = MONITOR_SNAPSHOT_CACHE.get(execution_id)
    if not entry:
        return None
    normalized_stage = 'after' if stage == 'after' else 'before'
    snapshot = entry.get(normalized_stage)
    if snapshot and snapshot.get('content'):
        return snapshot
    return None

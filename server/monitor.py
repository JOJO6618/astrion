from __future__ import annotations
import time
from typing import Optional, Dict, Any
from .utils_common import debug_log
from .state import MONITOR_SNAPSHOT_CACHE, MONITOR_SNAPSHOT_CACHE_LIMIT

__all__ = ["cache_monitor_snapshot", "get_cached_monitor_snapshot"]


def _cache_key(execution_id: Optional[str], username: Optional[str]) -> Optional[str]:
    """快照缓存键带用户维度，防止跨用户读取他人工具执行快照。"""
    if not execution_id:
        return None
    return f"{username or ''}:{execution_id}"


def cache_monitor_snapshot(execution_id: Optional[str], stage: str, snapshot: Optional[Dict[str, Any]], username: Optional[str] = None):
    """缓存工具执行前/后的文件快照。"""
    if not execution_id or not snapshot or not snapshot.get('content'):
        return
    cache_key = _cache_key(execution_id, username)
    if not cache_key:
        return
    normalized_stage = 'after' if stage == 'after' else 'before'
    entry = MONITOR_SNAPSHOT_CACHE.get(cache_key) or {
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
    MONITOR_SNAPSHOT_CACHE[cache_key] = entry
    if len(MONITOR_SNAPSHOT_CACHE) > MONITOR_SNAPSHOT_CACHE_LIMIT:
        try:
            oldest_key = min(
                MONITOR_SNAPSHOT_CACHE.keys(),
                key=lambda key: MONITOR_SNAPSHOT_CACHE[key].get('timestamp', 0.0)
            )
            MONITOR_SNAPSHOT_CACHE.pop(oldest_key, None)
        except ValueError:
            pass


def get_cached_monitor_snapshot(execution_id: Optional[str], stage: str, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cache_key = _cache_key(execution_id, username)
    if not cache_key:
        return None
    entry = MONITOR_SNAPSHOT_CACHE.get(cache_key)
    if not entry:
        return None
    normalized_stage = 'after' if stage == 'after' else 'before'
    snapshot = entry.get(normalized_stage)
    if snapshot and snapshot.get('content'):
        return snapshot
    return None

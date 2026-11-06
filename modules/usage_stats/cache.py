"""账本目录的增量汇总缓存。

缓存是派生数据：以账本文件 (mtime_ns, size) 判定是否需要重新解析，删除缓存文件即可重建；
账本文件已不存在时同步丢弃其汇总（账本是唯一权威）。缓存写在账本目录同级：
``<data_dir>/usage_stats_rollup.json``（与 api_costs/ 同一 data_dir，随工作区/Chat 隔离）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List

from utils.atomic_io import atomic_write_json

from .rollup import rollup_ledger

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
CACHE_FILENAME = "usage_stats_rollup.json"

_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def _load(path: Path) -> Dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def collect_ledger_dir(ledger_dir: Path) -> Dict[str, Dict[str, List]]:
    """返回 {conversation_id: {桶键: 数值列表}}；只重新解析有变化的账本文件。"""
    ledger_dir = Path(ledger_dir)
    if not ledger_dir.is_dir():
        return {}
    cache_path = ledger_dir.parent / CACHE_FILENAME
    with _lock_for(cache_path):
        cached = _load(cache_path)
        result: Dict[str, dict] = {}
        changed = False
        try:
            entries = list(os.scandir(ledger_dir))
        except OSError:
            logger.warning("usage stats: cannot list %s", ledger_dir, exc_info=True)
            return {}
        for entry in entries:
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            conv_id = entry.name[:-5]
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            signature = [stat.st_mtime_ns, stat.st_size]
            previous = cached.get(conv_id)
            if isinstance(previous, dict) and previous.get("sig") == signature:
                result[conv_id] = previous
                continue
            try:
                payload = json.loads(Path(entry.path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 读失败不写入缓存，下次重试；账本为原子替换写入，正常不会读到半份
                logger.warning("usage stats: skip unreadable ledger %s", entry.path)
                continue
            result[conv_id] = {"sig": signature, "buckets": rollup_ledger(payload)}
            changed = True
        if set(result) != set(cached):
            changed = True
        if changed:
            try:
                atomic_write_json(cache_path, {"version": CACHE_VERSION, "files": result})
            except Exception:
                logger.warning("usage stats: cache write failed %s", cache_path, exc_info=True)
        return {conv_id: item["buckets"] for conv_id, item in result.items()}

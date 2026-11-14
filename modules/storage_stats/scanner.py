"""数据根占用扫描：一次遍历按类别归桶，并附带对话备份明细。"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .catalog import BACKUP_PREFIX, CATEGORY_IDS, classify

# 备份明细只用于前端预估「保留最近 N 天」的删除量，超过上限时截断（按大小降序）
_MAX_BACKUP_ENTRIES = 2000


def _entry_bytes(entry: os.DirEntry) -> int:
    """条目占用的磁盘字节数：优先 st_blocks（与 du 口径一致），Windows 回退 st_size。"""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return 0
    blocks = getattr(st, "st_blocks", 0)
    if blocks:
        return int(blocks) * 512
    return int(getattr(st, "st_size", 0) or 0)


def _iter_files(root: Path) -> Iterator[Tuple[Tuple[str, ...], os.DirEntry]]:
    """深度优先遍历普通文件，跳过符号链接与不可读目录。"""
    if not root.is_dir():
        return
    stack: List[Tuple[str, Tuple[str, ...]]] = [(str(root), ())]
    while stack:
        dir_path, parts = stack.pop()
        try:
            with os.scandir(dir_path) as handle:
                entries = list(handle)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, parts + (entry.name,)))
                elif entry.is_file(follow_symlinks=False):
                    yield parts + (entry.name,), entry
            except OSError:
                continue


def _backup_updated_at(dir_path: Path) -> Optional[str]:
    """读取备份目录 meta.json 的 updated_at；缺失或非法时返回 None。"""
    try:
        with open(dir_path / "meta.json", "r", encoding="utf-8") as handle:
            raw = str(json.load(handle).get("updated_at") or "").strip()
    except (OSError, ValueError, TypeError):
        return None
    if not raw:
        return None
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def _backup_mtime_iso(dir_path: Path) -> Optional[str]:
    try:
        ts = dir_path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _backup_entries(data_root: Path, backup_bytes: Dict[str, int]) -> List[Dict[str, Any]]:
    save_root = data_root / "save"
    if not save_root.is_dir():
        return []
    try:
        with os.scandir(save_root) as handle:
            names = [
                item.name
                for item in handle
                if not item.is_symlink()
                and item.is_dir(follow_symlinks=False)
                and item.name.startswith(BACKUP_PREFIX)
            ]
    except OSError:
        return []
    entries: List[Dict[str, Any]] = []
    for name in names:
        dir_path = save_root / name
        entries.append(
            {
                "id": name,
                "bytes": backup_bytes.get(name, 0),
                "updated_at": _backup_updated_at(dir_path) or _backup_mtime_iso(dir_path),
            }
        )
    entries.sort(key=lambda item: item["bytes"], reverse=True)
    return entries[:_MAX_BACKUP_ENTRIES]


def scan(data_root: Path, logs_root: Path) -> Dict[str, Any]:
    """统计数据根与日志根占用；返回可序列化为 JSON 的结果。"""
    started = time.monotonic()
    totals: Dict[str, int] = {cid: 0 for cid in CATEGORY_IDS}
    counts: Dict[str, int] = {cid: 0 for cid in CATEGORY_IDS}
    backup_bytes: Dict[str, int] = {}

    data_path = Path(data_root)
    logs_path = Path(logs_root)

    for parts, entry in _iter_files(data_path):
        size = _entry_bytes(entry)
        category = classify(parts)
        totals[category] += size
        counts[category] += 1
        # save/<conv_id>/... → 按对话累加
        if category == "backups" and len(parts) >= 3:
            backup_bytes[parts[1]] = backup_bytes.get(parts[1], 0) + size

    for _parts, entry in _iter_files(logs_path):
        totals["logs"] += _entry_bytes(entry)
        counts["logs"] += 1

    return {
        "platform": sys.platform,
        "data_root": str(data_path),
        "logs_root": str(logs_path),
        "scanned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "total_bytes": sum(totals.values()),
        "categories": [
            {"id": cid, "bytes": totals[cid], "files": counts[cid]} for cid in CATEGORY_IDS
        ],
        "backups": _backup_entries(data_path, backup_bytes),
    }


__all__ = ["scan"]

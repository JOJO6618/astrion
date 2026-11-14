"""存储清理：清空日志、按天清理旧对话备份。

安全边界：
- 只处理调用方传入的固定根目录的直接内容，拒绝符号链接与非普通文件；
- 备份只删根下的 ``conv_*`` 目录，删除前再次校验父目录身份；
- 日志主文件用截断而不是 unlink（可能是活动写入句柄，unlink 会让空间无法及时释放）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import BACKUP_PREFIX

_ROTATED_SUFFIX = re.compile(r"\.\d+$")


def _path_bytes(path: Path) -> int:
    try:
        st = path.lstat()
    except OSError:
        return 0
    blocks = getattr(st, "st_blocks", 0)
    if blocks:
        return int(blocks) * 512
    return int(getattr(st, "st_size", 0) or 0)


def _dir_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            total += _path_bytes(Path(dirpath) / name)
    return total


def clean_logs(logs_root: Path) -> Dict[str, Any]:
    """清空日志目录：归档/非活动文件删除，主 ``*.log`` 截断为 0 字节。"""
    root = Path(logs_root)
    result: Dict[str, Any] = {
        "freed_bytes": 0,
        "removed": 0,
        "truncated": 0,
        "failed": [],
    }
    if not root.is_dir():
        return result

    for dirpath, _dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                st = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            size = _path_bytes(path)
            try:
                if name.endswith(".log") and not _ROTATED_SUFFIX.search(name):
                    # 活动句柄：截断即可释放空间，进程可继续写入
                    with open(path, "r+b") as handle:
                        handle.truncate(0)
                    result["truncated"] += 1
                else:
                    path.unlink()
                    result["removed"] += 1
                result["freed_bytes"] += size
            except OSError as exc:
                result["failed"].append({"path": str(path), "error": str(exc)})
    return result


def _backup_timestamp(dir_path: Path) -> Optional[float]:
    """备份最后更新时间：meta.json.updated_at 优先，回退目录 mtime。"""
    try:
        with open(dir_path / "meta.json", "r", encoding="utf-8") as handle:
            raw = str(json.load(handle).get("updated_at") or "").strip()
        if raw:
            return datetime.fromisoformat(raw).timestamp()
    except (OSError, ValueError, TypeError):
        pass
    try:
        return dir_path.stat().st_mtime
    except OSError:
        return None


def clean_backups(save_root: Path, keep_days: int) -> Dict[str, Any]:
    """删除最后更新早于 ``keep_days`` 天的对话备份目录（整份删除）。"""
    root = Path(save_root)
    result: Dict[str, Any] = {
        "freed_bytes": 0,
        "removed_count": 0,
        "removed": [],
        "failed": [],
        "keep_days": keep_days,
    }
    if not root.is_dir():
        return result

    try:
        resolved_root = root.resolve()
        with os.scandir(root) as handle:
            entries = list(handle)
    except OSError as exc:
        result["failed"].append({"path": str(root), "error": str(exc)})
        return result

    cutoff = time.time() - keep_days * 86400
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if not entry.name.startswith(BACKUP_PREFIX):
                continue
            dir_path = Path(entry.path)
            # 防御：必须是 save 根的直接子目录（防止路径替换后越界删除）
            if dir_path.parent.resolve() != resolved_root:
                continue
            updated = _backup_timestamp(dir_path)
            if updated is None or updated >= cutoff:
                continue
            size = _dir_bytes(dir_path)
            shutil.rmtree(dir_path)
        except OSError as exc:
            result["failed"].append({"path": entry.path, "error": str(exc)})
            continue
        result["freed_bytes"] += size
        result["removed_count"] += 1
        result["removed"].append(entry.name)
    return result


__all__ = ["clean_logs", "clean_backups"]

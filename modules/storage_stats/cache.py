"""扫描结果缓存与后台单飞扫描。

策略：进入页面先读缓存瞬间渲染，同时触发一次后台扫描；扫描完成覆盖缓存。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from config import DATA_DIR, LOGS_DIR

from .scanner import scan

logger = logging.getLogger(__name__)

CACHE_FILENAME = "storage_stats.json"
CACHE_VERSION = 1

_scan_guard = threading.Lock()
_scan_running = False


def cache_path() -> Path:
    return Path(DATA_DIR) / CACHE_FILENAME


def load_cache() -> Optional[Dict[str, Any]]:
    """读取缓存；不存在、损坏或版本不符时返回 None。"""
    path = cache_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("storage stats: 缓存读取失败，已忽略", exc_info=True)
        return None
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return None
    return data


def write_cache(payload: Dict[str, Any]) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        logger.warning("storage stats: 缓存写入失败", exc_info=True)


def is_scanning() -> bool:
    with _scan_guard:
        return _scan_running


def run_scan() -> Optional[Dict[str, Any]]:
    """同步扫描并写缓存；调用方负责串行化。"""
    payload = scan(Path(DATA_DIR), Path(LOGS_DIR))
    payload["version"] = CACHE_VERSION
    write_cache(payload)
    return payload


def _scan_worker() -> None:
    global _scan_running
    try:
        run_scan()
    except Exception:
        logger.exception("storage stats: 扫描失败")
    finally:
        with _scan_guard:
            _scan_running = False


def start_scan() -> bool:
    """启动后台扫描；已有扫描在跑时返回 False（单飞，不排队）。"""
    global _scan_running
    with _scan_guard:
        if _scan_running:
            return False
        _scan_running = True
    thread = threading.Thread(target=_scan_worker, name="storage-stats-scan", daemon=True)
    thread.start()
    return True


__all__ = ["cache_path", "load_cache", "write_cache", "is_scanning", "run_scan", "start_scan"]

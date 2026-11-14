"""存储空间统计与清理。

- 只统计数据根（DATA_DIR）与日志根（LOGS_DIR），不遍历工作区项目目录；
- 扫描结果缓存在 ``<DATA_DIR>/storage_stats.json``，进入页面先读缓存再后台刷新；
- 清理只覆盖「日志」与「旧对话备份」两类安全目标。
"""

from __future__ import annotations

from .cache import (
    cache_path,
    is_scanning,
    load_cache,
    run_scan,
    start_scan,
    write_cache,
)
from .cleanup import clean_backups, clean_logs
from .catalog import CATEGORY_IDS
from .scanner import scan

__all__ = [
    "CATEGORY_IDS",
    "cache_path",
    "clean_backups",
    "clean_logs",
    "is_scanning",
    "load_cache",
    "run_scan",
    "scan",
    "start_scan",
    "write_cache",
]

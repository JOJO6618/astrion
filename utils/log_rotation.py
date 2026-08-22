"""日志与落盘文件的轮转/清理工具。

提供两类策略：

1. ``RotatingFileHandler`` 风格的"按大小轮转"——用于持续追加的单文件日志
   （如 host_workspace_debug.log、chunk_*.log）。封装为 :func:`append_line`，
   单文件超过阈值即滚动，保留固定份数。
2. "按份数保留"——用于一请求一文件的落盘目录（如 api_requests/），写入新文件
   后清理最旧的，只保留最近 N 个。

阈值可通过环境变量覆盖：

- ``AGENT_LOG_ROTATE_MAX_BYTES``  单文件轮转阈值（字节，默认 20MB）
- ``AGENT_LOG_ROTATE_BACKUPS``    追加型日志保留的轮转份数（默认 3）
- ``AGENT_DUMP_KEEP``             按份计量目录保留的文件数（默认 30）
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


DEFAULT_MAX_BYTES = _env_int("AGENT_LOG_ROTATE_MAX_BYTES", 20 * 1024 * 1024)  # 20MB
DEFAULT_BACKUPS = _env_int("AGENT_LOG_ROTATE_BACKUPS", 3)
DEFAULT_DUMP_KEEP = _env_int("AGENT_DUMP_KEEP", 30)

# 每个文件路径一把锁，避免并发写入与轮转竞争。
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _rotate(path: Path, backups: int) -> None:
    """执行 ``foo.log`` -> ``foo.log.1`` -> ``foo.log.2`` ... 的滚动。"""
    try:
        # 删除最旧一份
        oldest = path.with_name(f"{path.name}.{backups}")
        if oldest.exists():
            oldest.unlink()
        # 依次后移：.(n-1) -> .n
        for idx in range(backups - 1, 0, -1):
            src = path.with_name(f"{path.name}.{idx}")
            if src.exists():
                src.rename(path.with_name(f"{path.name}.{idx + 1}"))
        # 当前文件 -> .1
        if path.exists():
            path.rename(path.with_name(f"{path.name}.1"))
    except Exception:
        # 轮转失败不应阻断主流程，最坏情况是文件继续增长
        pass


def append_line(
    path: Path,
    line: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
    encoding: str = "utf-8",
) -> None:
    """向 ``path`` 追加一行（自动补换行），超过 ``max_bytes`` 时先轮转。

    线程安全。任何异常都被吞掉，调试日志不应影响主流程。
    """
    try:
        path = Path(path)
        text = line if line.endswith("\n") else line + "\n"
        with _lock_for(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = path.stat().st_size if path.exists() else 0
            except OSError:
                size = 0
            if size and size + len(text.encode(encoding)) > max_bytes:
                _rotate(path, backups)
            with path.open("a", encoding=encoding) as fp:
                fp.write(text)
    except Exception:
        return


def prune_dir(
    directory: Path,
    *,
    pattern: str = "*",
    keep: int = DEFAULT_DUMP_KEEP,
) -> None:
    """只保留 ``directory`` 下匹配 ``pattern`` 的最近 ``keep`` 个文件，删除其余最旧的。

    线程安全。任何异常都被吞掉。
    """
    try:
        directory = Path(directory)
        if keep <= 0 or not directory.is_dir():
            return
        with _lock_for(directory):
            files = [p for p in directory.glob(pattern) if p.is_file()]
            if len(files) <= keep:
                return
            files.sort(key=lambda p: p.stat().st_mtime)
            for stale in files[: len(files) - keep]:
                try:
                    stale.unlink()
                except OSError:
                    pass
    except Exception:
        return

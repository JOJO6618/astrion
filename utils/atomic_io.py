"""原子写文件工具：temp file + os.replace 的 Windows 加固版。

背景：Windows 上目标文件被任何句柄以默认共享模式打开时（msvcrt 默认共享读/写，
不含 FILE_SHARE_DELETE），os.replace 会抛 WinError 5（拒绝访问）；
并发写入者之间则可能抛 WinError 32（共享冲突）。杀毒软件实时扫描、
Windows Search 索引、同进程并发的读取线程都会造成这类短暂持锁。
POSIX 下 rename 不受打开句柄影响，无此问题。

对策：仅对这两种 winerror 做短退避重试；其他错误立即抛出。

2026-08 调整：默认预算由 6 次/约 1.55s 上调至 8 次/约 4.75s——
实测 Defender 实时扫描 / Search 索引器等外部程序偶发持锁超过原预算
（如对话索引 index.json 保存失败 WinError 5），提高预算以覆盖此类场景。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Union

# ERROR_ACCESS_DENIED / ERROR_SHARING_VIOLATION
_RETRY_WINERRORS = {5, 32}

PathLike = Union[str, Path]


def replace_with_retry(
    src: PathLike,
    dst: PathLike,
    *,
    attempts: int = 8,
    initial_delay: float = 0.05,
    max_delay: float = 1.6,
) -> None:
    """os.replace 的 Windows 加固版：对瞬时持锁做指数退避重试。

    attempts 为总尝试次数（含首次），退避序列默认 0.05/0.1/0.2/0.4/0.8/1.6/1.6s，
    最坏情况约 4.75s。最后一次失败时原样抛出该 OSError。
    """
    delay = initial_delay
    total = max(1, int(attempts))
    for i in range(total):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _RETRY_WINERRORS or i == total - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, max_delay)

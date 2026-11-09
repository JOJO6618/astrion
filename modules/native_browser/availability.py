"""能力判定：macOS/Windows 桌面壳启动且收到私有管道凭据的 host 后端。"""
from __future__ import annotations

import os
import sys

from config.desktop_browser import desktop_browser_token


def bridge_port() -> int:
    raw = (os.environ.get("ASTRION_DESKTOP_BROWSER_PORT") or os.environ.get("ASTRION_DESKTOP_BRIDGE_PORT") or "").strip()
    return int(raw) if raw.isdigit() and 0 < int(raw) <= 65535 else 0


def native_browser_supported() -> bool:
    try:
        from config import IS_HOST_MODE
    except Exception:
        IS_HOST_MODE = False
    return (
        sys.platform in {"darwin", "win32"}
        and bool(IS_HOST_MODE)
        and bool((os.environ.get("ASTRION_DESKTOP_VERSION") or "").strip())
        and bridge_port() > 0
        and bool(desktop_browser_token())
    )

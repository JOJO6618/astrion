"""能力判定：仅 macOS 桌面壳启动的 host 后端，且拿到了控制桥 token。"""
from __future__ import annotations

import os
import sys

from config.desktop_browser import desktop_browser_token


def bridge_port() -> int:
    raw = (os.environ.get("ASTRION_DESKTOP_BRIDGE_PORT") or "").strip()
    return int(raw) if raw.isdigit() else 0


def native_browser_supported() -> bool:
    try:
        from config import IS_HOST_MODE
    except Exception:
        IS_HOST_MODE = False
    return (
        sys.platform == "darwin"
        and bool(IS_HOST_MODE)
        and bool((os.environ.get("ASTRION_DESKTOP_VERSION") or "").strip())
        and bridge_port() > 0
        and bool(desktop_browser_token())
    )

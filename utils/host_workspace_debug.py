"""宿主机工作区切换调试日志（JSON Lines）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import LOGS_DIR
from utils.log_rotation import append_line

_LOG_FILE = Path(LOGS_DIR).expanduser().resolve() / "host_workspace_debug.log"


def write_host_workspace_debug(event: str, **payload: Any) -> None:
    """写入一条宿主机工作区调试日志（超过阈值自动轮转）。"""
    try:
        record = {
            "ts": datetime.now().isoformat(),
            "event": event,
            **payload,
        }
        append_line(_LOG_FILE, json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        # 调试日志不应影响主流程
        return


def get_host_workspace_debug_log_path() -> str:
    return str(_LOG_FILE)

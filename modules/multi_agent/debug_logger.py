"""多智能体模式专用调试日志。

把所有子智能体与主智能体之间的消息往来、注入、转发、工具调用记录下来，
便于排查循环/重复输出等问题。日志写入 {LOGS_DIR}/multi_agent_loop.log。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config.paths import LOGS_DIR


_LOG_PATH = Path(LOGS_DIR) / "multi_agent_loop.log"


def ma_debug(event: str, **kwargs: Any) -> None:
    """追加一条结构化调试日志。"""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"t": time.time(), "event": event}
        for k, v in kwargs.items():
            payload[k] = v
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

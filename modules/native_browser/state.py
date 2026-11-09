"""对话级浏览器开关与快照基线代次（均存 conversation metadata）。"""
from __future__ import annotations

from typing import Any, Dict

BROWSER_ENABLED_META_KEY = "browser_tools_enabled"
BROWSER_PENDING_META_KEY = "pending_browser_tools"
BROWSER_EPOCH_META_KEY = "browser_snapshot_epoch"
# 运行期模式漂移机制里的 kind 值
BROWSER_MODE_ON = "on"
BROWSER_MODE_OFF = "off"


def _metadata(terminal: Any) -> Dict[str, Any]:
    cm = getattr(terminal, "context_manager", None)
    meta = getattr(cm, "conversation_metadata", None) if cm else None
    return meta if isinstance(meta, dict) else {}


def browser_tools_enabled(terminal: Any) -> bool:
    return _metadata(terminal).get(BROWSER_ENABLED_META_KEY) is True


def browser_mode(terminal: Any) -> str:
    return BROWSER_MODE_ON if browser_tools_enabled(terminal) else BROWSER_MODE_OFF


def snapshot_epoch(terminal: Any) -> int:
    value = _metadata(terminal).get(BROWSER_EPOCH_META_KEY)
    return value if isinstance(value, int) and value >= 0 else 0


def next_epoch(metadata: Any) -> int:
    value = metadata.get(BROWSER_EPOCH_META_KEY) if isinstance(metadata, dict) else None
    return (value if isinstance(value, int) and value >= 0 else 0) + 1

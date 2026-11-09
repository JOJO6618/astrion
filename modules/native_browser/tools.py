"""工具清单与读取类／动作类划分（唯一来源）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

READ_OPS = frozenset({
    "navigate", "snapshot", "find", "take_screenshot", "wait_for", "console_messages",
    "network_requests", "network_request", "tabs", "scroll", "hover", "emulate_media",
})
ACTION_OPS = frozenset({
    "click", "type", "fill_form", "select_option", "press_key", "drag", "drop",
    "file_upload", "evaluate", "fill_saved_login",
})
# 每次都要审批的动作（站点「以后都允许」不覆盖）
ALWAYS_APPROVE_OPS = frozenset({"evaluate", "fill_saved_login"})
ALL_OPS = READ_OPS | ACTION_OPS | {"handle_dialog"}
BROWSER_TOOL_NAMES = frozenset(f"browser_{op}" for op in ALL_OPS)


def is_browser_tool(name: Any) -> bool:
    return isinstance(name, str) and name in BROWSER_TOOL_NAMES


def op_of(name: str) -> str:
    return name[len("browser_"):]


def is_action(name: str, arguments: Optional[Dict[str, Any]] = None, dialog_type: Optional[str] = None) -> bool:
    """handle_dialog 按回答拆分：确认 alert、取消任意对话框属读取类；其余确认属动作类。"""
    op = op_of(name)
    if op == "handle_dialog":
        accept = (arguments or {}).get("accept") is True
        return accept and dialog_type != "alert"
    return op in ACTION_OPS

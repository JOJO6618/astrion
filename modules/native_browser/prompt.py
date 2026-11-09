"""浏览器冻结提示词段与开关切换通知。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.native_browser.state import BROWSER_MODE_ON, browser_mode

_PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "native_browser.txt"


def build_frozen_prompt(terminal: Any) -> str:
    """冻结段写入「创建时」的开关状态；之后的变化只走切换通知，不改冻结段。"""
    try:
        template = _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    state = browser_mode(terminal)
    if hasattr(terminal, "get_runtime_mode_baseline") and hasattr(terminal, "update_runtime_mode_baseline"):
        try:
            if terminal.get_runtime_mode_baseline():
                terminal.update_runtime_mode_baseline({"browser_tools": state})
        except Exception:
            pass
    return template.replace("{{STATE}}", "开启" if state == BROWSER_MODE_ON else "关闭")


def build_switch_notice(mode: str) -> str:
    if mode == BROWSER_MODE_ON:
        return "用户开启了浏览器工具，现在可以使用 browser_* 工具（规则见系统提示词中的「内置浏览器」）。"
    return "用户关闭了浏览器工具，之后调用 browser_* 工具都会失败，不要再调用；如确有需要，请用户重新开启。已打开的标签仍保留。"

"""macOS 内置浏览器的 AI 操作工具（Agent 主对话专用）。

设计与约束见 docs/native_browser.md「AI 浏览器操作工具」。执行在 Electron 主进程
（每标签 webContents.debugger），后端经桌面控制桥（随机 token）调用。
"""
from modules.native_browser.availability import native_browser_supported
from modules.native_browser.state import (
    browser_tools_enabled,
    BROWSER_ENABLED_META_KEY,
    BROWSER_EPOCH_META_KEY,
)
from modules.native_browser.tools import BROWSER_TOOL_NAMES, is_browser_tool

__all__ = [
    "native_browser_supported",
    "browser_tools_enabled",
    "BROWSER_ENABLED_META_KEY",
    "BROWSER_EPOCH_META_KEY",
    "BROWSER_TOOL_NAMES",
    "is_browser_tool",
]

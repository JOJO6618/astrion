"""终端与会话管理配置。"""

import os

MAX_TERMINALS = 3
TERMINAL_BUFFER_SIZE = 100000
TERMINAL_DISPLAY_SIZE = 50000
TERMINAL_TIMEOUT = 300
TERMINAL_OUTPUT_WAIT = 5
TERMINAL_SNAPSHOT_DEFAULT_LINES = 50
TERMINAL_SNAPSHOT_MAX_LINES = 200
TERMINAL_SNAPSHOT_MAX_CHARS = 6000000
TERMINAL_INPUT_MAX_CHARS = 50000


def _parse_paths(raw_value: str):
    items = []
    for chunk in (raw_value or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        items.append(chunk)
    return items


TERMINAL_SANDBOX_MODE = "host"
HOST_EXECUTION_MODE_DEFAULT = os.environ.get("HOST_EXECUTION_MODE_DEFAULT", "sandbox").strip().lower()
# 沙箱可写路径的「部署通道」（逗号分隔）。路径授权只有两个来源：
# config/host_sandbox_policy.json（前端「路径授权」UI）+ 本变量（真·环境变量），
# 两者合并去重（见 modules/host_sandbox_policy.py::get_macos_writable_paths）。
# settings.json 的 terminal.macos_writable_paths 映射已于 2026-08-30 移除；
# .env 注入技术上仍会生效，但不是受支持的配置通道（不推荐使用）。
HOST_SANDBOX_MACOS_WRITABLE_PATHS = _parse_paths(
    os.environ.get("HOST_SANDBOX_MACOS_WRITABLE_PATHS", "")
)
HOST_SANDBOX_NETWORK_PERMISSION = os.environ.get(
    "HOST_SANDBOX_NETWORK_PERMISSION", "restricted"
).strip().lower()

__all__ = [
    "MAX_TERMINALS",
    "TERMINAL_BUFFER_SIZE",
    "TERMINAL_DISPLAY_SIZE",
    "TERMINAL_TIMEOUT",
    "TERMINAL_OUTPUT_WAIT",
    "TERMINAL_SNAPSHOT_DEFAULT_LINES",
    "TERMINAL_SNAPSHOT_MAX_LINES",
    "TERMINAL_SNAPSHOT_MAX_CHARS",
    "TERMINAL_INPUT_MAX_CHARS",
    "TERMINAL_SANDBOX_MODE",
    "HOST_EXECUTION_MODE_DEFAULT",
    "HOST_SANDBOX_MACOS_WRITABLE_PATHS",
    "HOST_SANDBOX_NETWORK_PERMISSION",
]

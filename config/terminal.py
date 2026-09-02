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


def _parse_bindings(raw_value: str):
    items = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        items.append(chunk)
    return items


def _parse_paths(raw_value: str):
    items = []
    for chunk in (raw_value or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        items.append(chunk)
    return items


_env_prefix = "TERMINAL_SANDBOX_ENV_"
TERMINAL_SANDBOX_MODE = os.environ.get("TERMINAL_SANDBOX_MODE", "host").lower()
TERMINAL_SANDBOX_IMAGE = os.environ.get("TERMINAL_SANDBOX_IMAGE", "python:3.11-slim")
TERMINAL_SANDBOX_MOUNT_PATH = os.environ.get("TERMINAL_SANDBOX_MOUNT_PATH", "/workspace")
TERMINAL_SANDBOX_SHELL = os.environ.get("TERMINAL_SANDBOX_SHELL", "/bin/bash")
TERMINAL_SANDBOX_NETWORK = os.environ.get("TERMINAL_SANDBOX_NETWORK", "bridge")
# 安全默认：限制单容器 CPU/内存/PID，防止单用户资源耗尽型 DoS（2026-09-02 审计）。
# 如需放开可显式设为空字符串（不推荐多用户部署放开）。
TERMINAL_SANDBOX_CPUS = os.environ.get("TERMINAL_SANDBOX_CPUS", "1")
TERMINAL_SANDBOX_MEMORY = os.environ.get("TERMINAL_SANDBOX_MEMORY", "1g")
TERMINAL_SANDBOX_PIDS_LIMIT = os.environ.get("TERMINAL_SANDBOX_PIDS_LIMIT", "512")
TERMINAL_SANDBOX_BINDS = _parse_bindings(os.environ.get("TERMINAL_SANDBOX_BINDS", ""))
TERMINAL_SANDBOX_BIN = os.environ.get("TERMINAL_SANDBOX_BIN", "docker")
TERMINAL_SANDBOX_NAME_PREFIX = os.environ.get("TERMINAL_SANDBOX_NAME_PREFIX", "agent-term")
TERMINAL_SANDBOX_ENV = {
    key[len(_env_prefix):]: value
    for key, value in os.environ.items()
    if key.startswith(_env_prefix)
}
TERMINAL_SANDBOX_REQUIRE = os.environ.get("TERMINAL_SANDBOX_REQUIRE", "0") not in {"0", "false", "False"}
LINUX_SAFETY = os.environ.get("LINUX_SAFETY", "0") not in {"0", "false", "False"}
TOOLBOX_TERMINAL_IDLE_SECONDS = int(os.environ.get("TOOLBOX_TERMINAL_IDLE_SECONDS", "900"))
MAX_ACTIVE_USER_CONTAINERS = int(os.environ.get("MAX_ACTIVE_USER_CONTAINERS", "8"))
# 每用户同时活跃的容器上限（防单用户多工作区占满全局容器池，2026-09-02 审计新增）
MAX_ACTIVE_CONTAINERS_PER_USER = int(os.environ.get("MAX_ACTIVE_CONTAINERS_PER_USER", "3"))
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
    "TERMINAL_SANDBOX_IMAGE",
    "TERMINAL_SANDBOX_MOUNT_PATH",
    "TERMINAL_SANDBOX_SHELL",
    "TERMINAL_SANDBOX_NETWORK",
    "TERMINAL_SANDBOX_CPUS",
    "TERMINAL_SANDBOX_MEMORY",
    "TERMINAL_SANDBOX_PIDS_LIMIT",
    "TERMINAL_SANDBOX_BINDS",
    "TERMINAL_SANDBOX_BIN",
    "TERMINAL_SANDBOX_NAME_PREFIX",
    "TERMINAL_SANDBOX_ENV",
    "TERMINAL_SANDBOX_REQUIRE",
    "LINUX_SAFETY",
    "TOOLBOX_TERMINAL_IDLE_SECONDS",
    "MAX_ACTIVE_USER_CONTAINERS",
    "MAX_ACTIVE_CONTAINERS_PER_USER",
    "HOST_EXECUTION_MODE_DEFAULT",
    "HOST_SANDBOX_MACOS_WRITABLE_PATHS",
    "HOST_SANDBOX_NETWORK_PERMISSION",
]

"""MCP（Model Context Protocol）工具扩展配置。"""

import os
from .paths import DATA_DIR

# 是否启用 MCP 工具桥接
MCP_TOOLS_ENABLED = os.environ.get("MCP_TOOLS_ENABLED", "1") not in {"0", "false", "False"}

# MCP 服务器配置文件路径
MCP_SERVERS_FILE = os.environ.get("MCP_SERVERS_FILE", f"{DATA_DIR}/mcp_servers.json")

# 客户端声明的协议版本（可按需覆盖）
MCP_PROTOCOL_VERSION = os.environ.get("MCP_PROTOCOL_VERSION", "2025-06-18")

# 工具发现/调用默认超时（秒）
MCP_DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("MCP_DEFAULT_TIMEOUT_SECONDS", "25"))

__all__ = [
    "MCP_TOOLS_ENABLED",
    "MCP_SERVERS_FILE",
    "MCP_PROTOCOL_VERSION",
    "MCP_DEFAULT_TIMEOUT_SECONDS",
]

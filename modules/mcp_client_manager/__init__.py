# modules/mcp_client_manager/__init__.py - MCP 客户端管理器兼容入口
from modules.mcp_client_manager.exceptions import MCPClientError
from modules.mcp_client_manager.models import MCPToolBinding, MCPClientPoolEntry
from modules.mcp_client_manager.stdio_client import _StdioMCPClient
from modules.mcp_client_manager.http_client import _StreamableHTTPMCPClient
from modules.mcp_client_manager.manager import MCPClientManager

__all__ = [
    "MCPClientError",
    "MCPToolBinding",
    "MCPClientPoolEntry",
    "_StdioMCPClient",
    "_StreamableHTTPMCPClient",
    "MCPClientManager",
]

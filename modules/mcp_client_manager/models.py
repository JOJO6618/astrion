"""MCP 客户端管理：工具发现、调用与 OpenAI 工具映射。"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

import httpx

from config import MCP_PROTOCOL_VERSION, MCP_DEFAULT_TIMEOUT_SECONDS
from modules.mcp_server_registry import MCPServerRegistry

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


@dataclass
class MCPToolBinding:
    alias: str
    server_id: str
    server_name: str
    remote_name: str
    transport: str
    timeout_seconds: int


@dataclass
class MCPClientPoolEntry:
    """长连接 MCP 客户端池条目。"""

    server_id: str
    signature: str
    client: Any
    timeout_seconds: int
    created_at: float
    last_used_at: float

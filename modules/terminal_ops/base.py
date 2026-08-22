# modules/terminal_ops.py - 终端操作模块（修复Python命令检测）

import os
import sys
import asyncio
import subprocess
import shutil
import time
import signal
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, TYPE_CHECKING
from types import SimpleNamespace
try:
    from config import (
        TERMINAL_COMMAND_TIMEOUT,
        FORBIDDEN_COMMANDS,
        OUTPUT_FORMATS,
        MAX_RUN_COMMAND_CHARS,
        TOOLBOX_TERMINAL_IDLE_SECONDS,
        HOST_SANDBOX_NETWORK_PERMISSION,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        TERMINAL_COMMAND_TIMEOUT,
        FORBIDDEN_COMMANDS,
        OUTPUT_FORMATS,
        MAX_RUN_COMMAND_CHARS,
        TOOLBOX_TERMINAL_IDLE_SECONDS,
        HOST_SANDBOX_NETWORK_PERMISSION,
    )
from modules.toolbox_container import ToolboxContainer
from modules.host_sandbox_runner import (
    HostSandboxError,
    NETWORK_PERMISSION_RESTRICTED,
    build_host_sandbox_plan,
    build_host_sandbox_readonly_plan,
    host_sandbox_enabled,
)

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle
    from modules.terminal_manager import TerminalManager


class TerminalOperatorBase:
    """TerminalOperator 基础类。"""

    def __init__(self, project_path: str, container_session: Optional["ContainerHandle"] = None):
        self.project_path = Path(project_path).resolve()
        self.process = None
        # 自动检测Python命令，并记录虚拟环境变量（仅宿主机使用）
        self._python_env: Dict[str, str] = {}
        self.python_cmd = self._detect_python_runtime()
        # Docker 容器内的 Python 命令（默认指向预装 venv）
        self.container_python_cmd = os.environ.get("CONTAINER_PYTHON_CMD", "/opt/agent-venv/bin/python3")
        print(f"{OUTPUT_FORMATS['info']} 检测到Python命令: {self.python_cmd}")
        self._toolbox: Optional[ToolboxContainer] = None
        self.container_session: Optional["ContainerHandle"] = container_session
        # 记录 TerminalManager 引用，便于 CLI 场景复用同一容器
        self._terminal_manager: Optional["TerminalManager"] = None
        self.host_execution_mode: str = "sandbox"

    def set_host_execution_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        self.host_execution_mode = "direct" if normalized == "direct" else "sandbox"

    def _reset_toolbox(self):
        """强制关闭并重建工具终端，保证每次命令/脚本运行独立环境。"""
        if self._toolbox:
            try:
                self._toolbox.shutdown()
            except Exception:
                pass
            self._toolbox = None

    def attach_terminal_manager(self, manager: Optional["TerminalManager"]):
        """由 MainTerminal/WebTerminal 注入 TerminalManager，便于共享终端容器。"""
        self._terminal_manager = manager

    def set_container_session(self, session: Optional["ContainerHandle"]):
        if session is self.container_session:
            return
        self.container_session = session
        if self._toolbox:
            self._toolbox.set_container_session(session)

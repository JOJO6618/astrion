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


class ContainerMixin:
    """TerminalOperator container 能力 mixin。"""

    def _resolve_active_container_session(self) -> Optional[SimpleNamespace]:
        """
        若已存在活动终端且在容器内运行，返回一个临时的容器句柄，
        以便 run_command 复用同一个容器环境。
        """
        manager = self._terminal_manager
        if not manager:
            return None
        active_name = getattr(manager, "active_terminal", None)
        if not active_name:
            return None
        terminal = manager.terminals.get(active_name) if getattr(manager, "terminals", None) else None
        if not terminal or not getattr(terminal, "using_container", False):
            return None
        container_name = getattr(terminal, "sandbox_container_name", None)
        if not container_name:
            return None
        try:
            mount_path = (terminal.sandbox_options.get("mount_path") or "/workspace").rstrip("/") or "/workspace"
        except Exception:
            mount_path = "/workspace"
        return SimpleNamespace(mode="docker", container_name=container_name, mount_path=mount_path)

    def _will_use_container(self, session_override: Optional["ContainerHandle"]) -> bool:
        """根据会话/回退策略判断此次执行是否在容器中进行。"""
        if session_override:
            return getattr(session_override, "mode", None) == "docker"
        if self.container_session:
            return getattr(self.container_session, "mode", None) == "docker"
        # 未绑定容器会话时会使用工具箱容器（同样是 Docker）
        return True

    def _get_toolbox(self) -> ToolboxContainer:
        if self._toolbox is None:
            self._toolbox = ToolboxContainer(
                project_path=str(self.project_path),
                idle_timeout=TOOLBOX_TERMINAL_IDLE_SECONDS,
                container_session=self.container_session,
            )
        return self._toolbox

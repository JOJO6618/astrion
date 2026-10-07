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
        OUTPUT_FORMATS,
        MAX_RUN_COMMAND_CHARS,
        HOST_SANDBOX_NETWORK_PERMISSION,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        TERMINAL_COMMAND_TIMEOUT,
        OUTPUT_FORMATS,
        MAX_RUN_COMMAND_CHARS,
        HOST_SANDBOX_NETWORK_PERMISSION,
    )
from modules.host_sandbox_runner import (
    HostSandboxError,
    NETWORK_PERMISSION_RESTRICTED,
    build_host_sandbox_plan,
    build_host_sandbox_readonly_plan,
    host_sandbox_enabled,
)
from modules.i18n import tr
from modules.scoped_execution_policy import scoped_work_path

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle
    from modules.terminal_manager import TerminalManager


class CommandMixin:
    """TerminalOperator command 能力 mixin。"""

    def _validate_command(self, command: str) -> Tuple[bool, str]:
        """读取执行器所属用户的最新规则，不使用部署级或内置词表。"""
        from modules.command_blocking import validate_command

        return validate_command(command, getattr(self, "data_dir", None))

    @staticmethod
    def _clamp_timeout(requested: Optional[int], default: int, max_limit: int) -> int:
        """对timeout进行默认化与上限夹紧。"""
        if not requested or requested <= 0:
            return default
        return min(int(requested), max_limit)

    def _resolve_work_path(self, working_dir: Optional[str]) -> Path:
        return scoped_work_path(self.project_path, working_dir)

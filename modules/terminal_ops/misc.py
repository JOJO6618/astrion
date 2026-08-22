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


class MiscMixin:
    """TerminalOperator misc 能力 mixin。"""

    def kill_process(self):
        """终止当前运行的进程"""
        if self.process and self.process.returncode is None:
            self.process.kill()
            print(f"{OUTPUT_FORMATS['warning']} 进程已终止")

    @staticmethod
    def _materialize_seccomp_fd(plan_command: list[str], seccomp_path: Optional[str]) -> tuple[list[str], tuple[int, ...], Optional[int]]:
        if not seccomp_path:
            return plan_command, tuple(), None
        seccomp_fd = os.open(seccomp_path, os.O_RDONLY)
        fd_num = seccomp_fd
        cmd = [str(fd_num) if token == "__SECCOMP_FD__" else token for token in plan_command]
        return cmd, (fd_num,), seccomp_fd

    @staticmethod
    def _filter_ignored_stderr_lines(text: str, patterns: list[str]) -> str:
        """按行过滤 stderr 中匹配指定模式的噪音（如 wsl.exe 的 localhost 代理警告）。"""
        if not text or not patterns:
            return text
        kept = [
            line for line in text.splitlines(keepends=True)
            if not any(re.search(pattern, line) for pattern in patterns)
        ]
        return "".join(kept)

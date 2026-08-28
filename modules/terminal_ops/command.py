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
from modules.i18n import tr

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle
    from modules.terminal_manager import TerminalManager


class CommandMixin:
    """TerminalOperator command 能力 mixin。"""

    def _validate_command(self, command: str) -> Tuple[bool, str]:
        """验证命令安全性"""
        # 检查禁止的命令
        for forbidden in FORBIDDEN_COMMANDS:
            if forbidden in command.lower():
                return False, tr("terminal.forbidden_command", pattern=forbidden)
        
        # 检查危险的命令模式
        dangerous_patterns = [
            "sudo",
            "chmod 777",
            "rm -rf",
            "> /dev/",
            "fork bomb"
        ]
        
        for pattern in dangerous_patterns:
            if pattern in command.lower():
                return False, tr("terminal.forbidden_command", pattern=pattern)
        
        return True, ""

    @staticmethod
    def _clamp_timeout(requested: Optional[int], default: int, max_limit: int) -> int:
        """对timeout进行默认化与上限夹紧。"""
        if not requested or requested <= 0:
            return default
        return min(int(requested), max_limit)

    def _resolve_work_path(self, working_dir: Optional[str]) -> Path:
        if working_dir:
            work_path = (self.project_path / working_dir).resolve()
            work_path.relative_to(self.project_path)
            return work_path
        return self.project_path

    def _format_toolbox_output(self, payload: Dict) -> Dict:
        success = bool(payload.get("success"))
        output_text = payload.get("output", "") or ""
        # 去掉常见的交互式shell警告
        noisy_lines = (
            "bash: cannot set terminal process group",
            "bash: no job control in this shell",
        )
        filtered = []
        for line in output_text.splitlines():
            if any(noise in line for noise in noisy_lines):
                continue
            filtered.append(line)
        output_text = "\n".join(filtered)
        result = {
            "success": success,
            "output": output_text,
            "return_code": 0 if success else -1
        }
        if not success:
            result["error"] = payload.get("error") or payload.get("message") or tr("terminal.command_failed")
        if "message" in payload:
            result["message"] = payload["message"]
        if "status" in payload:
            result["status"] = payload["status"]
        if "truncated" in payload:
            result["truncated"] = payload["truncated"]
        return result

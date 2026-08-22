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


class PythonMixin:
    """TerminalOperator python 能力 mixin。"""

    def _detect_python_runtime(self) -> str:
        """
        自动检测可用的 Python 命令，优先使用预装虚拟环境。

        1) 优先查找 AGENT_TOOLBOX_VENV /opt/agent-venv / 当前进程 VIRTUAL_ENV。
        2) 找不到预装虚拟环境时，回退到系统可执行文件。
        """
        preferred = self._detect_preinstalled_python()
        if preferred:
            # 记录虚拟环境相关环境变量，供宿主机子进程复用
            self._python_env = self._build_python_env(preferred)
            return preferred

        return self._detect_system_python()

    def _detect_preinstalled_python(self) -> Optional[str]:
        """尝试定位预装虚拟环境的 python 可执行文件。"""
        # 允许显式指定可执行文件（优先级最高）
        explicit_python = os.environ.get("AGENT_PYTHON_BIN")
        if explicit_python:
            p = Path(explicit_python).expanduser()
            if p.exists() and os.access(p, os.X_OK) and not self._is_blocked_python_candidate(str(p)):
                try:
                    return str(p.resolve())
                except Exception:
                    return str(p)

        candidates = []
        if sys.platform == "darwin":
            # 优先使用非 shim 的系统 Python 路径（避免 /usr/bin/python3 xcode shim）
            mac_python_candidates = [
                "/Library/Developer/CommandLineTools/usr/bin/python3",
                "/opt/homebrew/bin/python3",
                "/usr/local/bin/python3",
            ]
            for raw_py in mac_python_candidates:
                p = Path(raw_py)
                if p.exists() and os.access(p, os.X_OK) and not self._is_blocked_python_candidate(str(p)):
                    try:
                        return str(p.resolve())
                    except Exception:
                        return str(p)

        # 环境变量优先（Dockerfile 已设置 AGENT_TOOLBOX_VENV=/opt/agent-venv）
        env_venv = os.environ.get("AGENT_TOOLBOX_VENV")
        if env_venv:
            candidates.append(env_venv)

        # 默认预装路径
        candidates.append("/opt/agent-venv")

        # 当前进程若已激活虚拟环境，也纳入候选
        current_venv = os.environ.get("VIRTUAL_ENV")
        if current_venv:
            candidates.append(current_venv)

        # 去重并依次检查
        seen = set()
        for raw in candidates:
            if not raw or raw in seen:
                continue
            seen.add(raw)
            root = Path(raw).expanduser()
            bin_dir = root / ("Scripts" if sys.platform == "win32" else "bin")
            for name in ("python3", "python"):
                candidate = bin_dir / name
                if candidate.exists() and os.access(candidate, os.X_OK):
                    return str(candidate.resolve())
        return None

    def _detect_system_python(self) -> str:
        """在系统 PATH 中探测可用的 Python3 可执行文件。"""
        commands_to_try = ["python", "py", "python3"] if sys.platform == "win32" else ["python3", "python"]
        for cmd in commands_to_try:
            resolved = shutil.which(cmd)
            if not resolved:
                continue
            if self._is_blocked_python_candidate(resolved):
                continue
            try:
                result = subprocess.run(
                    [resolved, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    output = result.stdout + result.stderr
                    if "Python 3" in output or "Python 2" not in output:
                        return resolved
            except Exception:
                continue

        fallback = "python" if sys.platform == "win32" else "python3"
        fallback_resolved = shutil.which(fallback) or fallback
        if sys.platform == "darwin" and self._is_blocked_python_candidate(fallback_resolved):
            raise RuntimeError(
                "未找到可用的 Python3 可执行文件：检测到 /usr/bin/python3 (xcode shim) 在沙箱中不可用。"
                "请设置 AGENT_PYTHON_BIN 或安装非 shim 的 python3。"
            )
        return fallback

    def _is_blocked_python_candidate(self, candidate: str) -> bool:
        """过滤已知在当前沙箱下不可靠的 Python 候选。"""
        if sys.platform != "darwin":
            return False
        try:
            resolved = str(Path(candidate).expanduser().resolve())
        except Exception:
            resolved = candidate
        # macOS 的 /usr/bin/python3 常为 xcode shim，会调用 xcodebuild 并触发 /dev/null 拦截
        if resolved == "/usr/bin/python3":
            return True
        return False

    def _build_python_env(self, python_path: str) -> Dict[str, str]:
        """构造与预装虚拟环境匹配的环境变量（仅作用于宿主机子进程）。"""
        env: Dict[str, str] = {}
        try:
            py_path = Path(python_path).resolve()
            bin_dir = py_path.parent
            venv_dir = bin_dir.parent
            env["VIRTUAL_ENV"] = str(venv_dir)
            current_path = os.environ.get("PATH", "")
            # 避免重复添加
            prefix = str(bin_dir)
            if current_path.startswith(prefix + os.pathsep) or current_path == prefix:
                env["PATH"] = current_path
            else:
                env["PATH"] = f"{prefix}{os.pathsep}{current_path}" if current_path else prefix
        except Exception:
            pass
        return env

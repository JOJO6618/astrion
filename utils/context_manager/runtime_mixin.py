# utils/context_manager.py - 上下文管理器（集成对话持久化和Token统计）

import os
import json
import base64
import mimetypes
import io
import uuid
import platform
import shutil
import subprocess
from copy import deepcopy
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
try:
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
from utils.conversation_manager import ConversationManager
from utils.host_workspace_debug import write_host_workspace_debug
from utils.media_store import MediaStore
from utils.token_usage import normalize_usage_payload

AUTO_SHALLOW_PLACEHOLDER = "过早的工具结果已经被自动压缩"
AUTO_SHALLOW_TOOL_WHITELIST = {
    "write_file",
    "read_file",
    "edit_file",
    "terminal_input",
    "terminal_snapshot",
    "web_search",
    "extract_webpage",
    "run_command",
    "view_image",
    "view_video",
}


class RuntimeMixin:
    """ContextManager runtime mixin 能力 mixin。"""

    def _run_command(self, cmd: List[str], *, timeout: float = 1.5, cwd: Optional[Path] = None) -> str:
        """运行命令并返回标准输出 / Run command and return stdout."""
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if completed.returncode != 0:
            return ""
        return (completed.stdout or "").strip()

    def _read_first_line(self, path: Path) -> str:
        """读取文件首行并去除空白 / Read first line and strip."""
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                return fh.readline().strip()
        except OSError:
            return ""

    def _read_os_release_pretty(self) -> str:
        """读取 Linux 发行版信息 / Read Linux distro from os-release."""
        if sys.platform != "linux":
            return ""
        path = Path("/etc/os-release")
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        for line in content.splitlines():
            if line.startswith("PRETTY_NAME="):
                value = line.split("=", 1)[1].strip().strip('"')
                return value
        return ""

    def _get_os_description(self) -> str:
        """获取 OS 描述 / Get OS description."""
        system = platform.system()
        if system == "Darwin":
            version = platform.mac_ver()[0] or platform.release()
            return f"macOS {version}".strip()
        if system == "Windows":
            release, version, _csd, _ptype = platform.win32_ver()
            if release and version and version not in release:
                return f"Windows {release} ({version})".strip()
            return f"Windows {release or version or platform.release()}".strip()
        if system == "Linux":
            pretty = self._read_os_release_pretty()
            if pretty:
                return f"Linux {pretty}".strip()
            version = platform.release() or platform.version()
            return f"Linux {version}".strip()
        version = platform.release() or platform.version()
        name = system or "Unknown"
        return f"{name} {version}".strip()

    def _parse_wmic_model(self, output: str) -> str:
        """解析 WMIC 输出 / Parse WMIC output."""
        if not output:
            return ""
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for line in lines:
            if line.lower() == "model":
                continue
            return line
        return ""

    def _get_device_model(self) -> str:
        """获取设备型号 / Get device model."""
        system = platform.system()
        if system == "Darwin":
            return self._run_command(["sysctl", "-n", "hw.model"])
        if system == "Windows":
            model = self._run_command(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "(Get-CimInstance -ClassName Win32_ComputerSystem).Model"]
            )
            if model:
                return model
            output = self._run_command(["wmic", "computersystem", "get", "model"])
            return self._parse_wmic_model(output)
        if system == "Linux":
            product = self._read_first_line(Path("/sys/devices/virtual/dmi/id/product_name"))
            vendor = self._read_first_line(Path("/sys/devices/virtual/dmi/id/sys_vendor"))
            if vendor and product and vendor not in product:
                return f"{vendor} {product}".strip()
            return product or vendor
        return ""

    def _get_python_info(self) -> str:
        """获取 Python 版本与可用命令 / Get Python version and commands."""
        version = platform.python_version()
        commands: List[str] = []
        if shutil.which("python"):
            commands.append("python")
        if shutil.which("python3"):
            commands.append("python3")
        if commands:
            return f"{version} ({', '.join(commands)})"
        return f"{version} (未在PATH)"

    def _get_node_info(self) -> str:
        """获取 Node 版本信息 / Get Node version info."""
        node_cmd = None
        if shutil.which("node"):
            node_cmd = "node"
        elif shutil.which("nodejs"):
            node_cmd = "nodejs"
        if not node_cmd:
            return "nodejs 未安装"
        version = self._run_command([node_cmd, "-v"])
        if version:
            if node_cmd == "nodejs":
                return f"{version} (nodejs)"
            return version
        return f"{node_cmd} 可用"

    def _get_git_info(self) -> str:
        """获取 Git 分支与状态 / Get git branch and status."""
        if not shutil.which("git"):
            return "无git环境"
        cwd = self.project_path if self.project_path.exists() else None
        if not cwd:
            return "未初始化"
        inside = self._run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd)
        if inside.strip() != "true":
            return "未初始化"
        branch = self._run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if not branch or branch == "HEAD":
            sha = self._run_command(["git", "rev-parse", "--short", "HEAD"], cwd=cwd)
            branch = f"detached@{sha}" if sha else "detached"
        status = self._run_command(["git", "status", "--porcelain"], cwd=cwd)
        dirty = bool(status.strip())
        return f"{branch} ({'dirty' if dirty else 'clean'})"

    def _get_host_runtime_cache(self) -> Dict[str, str]:
        """获取宿主机固定信息 / Get cached host info."""
        if self._host_runtime_cache:
            return self._host_runtime_cache
        os_desc = self._get_os_description() or "unknown"
        arch = platform.machine() or platform.processor() or "unknown"
        model = self._get_device_model() or "unknown"
        python_info = self._get_python_info() or "unknown"
        node_info = self._get_node_info() or "unknown"
        git_info = self._get_git_info() or "unknown"
        self._host_runtime_cache = {
            "os": os_desc,
            "arch": arch,
            "model": model,
            "python": python_info,
            "node": node_info,
            "git": git_info,
        }
        return self._host_runtime_cache

    def _build_host_runtime_environment(self) -> str:
        """构建宿主机运行环境提示 / Build host runtime environment text."""
        base = self._get_host_runtime_cache()
        lines = [
            "宿主机模式",
            f"  OS: {base.get('os', 'unknown')} | Arch: {base.get('arch', 'unknown')} | Model: {base.get('model', 'unknown')}",
            f"  Python: {base.get('python', 'unknown')}",
            f"  Node: {base.get('node', 'unknown')}",
            f"  Git: {base.get('git', 'unknown')}",
        ]
        return "\n".join(lines)

# modules/toolbox_container.py - 专用工具容器管理器

import asyncio
import time
import uuid
import shlex
from pathlib import Path
from typing import Optional, Dict, TYPE_CHECKING

from modules.persistent_terminal import PersistentTerminal
from config import (
    TERMINAL_SANDBOX_MODE,
    TERMINAL_SANDBOX_IMAGE,
    TERMINAL_SANDBOX_MOUNT_PATH,
    TERMINAL_SANDBOX_SHELL,
    TERMINAL_SANDBOX_NETWORK,
    TERMINAL_SANDBOX_CPUS,
    TERMINAL_SANDBOX_MEMORY,
    TERMINAL_SANDBOX_BINDS,
    TERMINAL_SANDBOX_BIN,
    TERMINAL_SANDBOX_NAME_PREFIX,
    TERMINAL_SANDBOX_ENV,
    TERMINAL_SANDBOX_REQUIRE,
    TOOLBOX_TERMINAL_IDLE_SECONDS,
)

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


def _build_sandbox_options(container_session: Optional["ContainerHandle"] = None) -> Dict:
    """构造与终端一致的沙箱配置."""
    name_prefix = TERMINAL_SANDBOX_NAME_PREFIX
    if name_prefix:
        name_prefix = f"{name_prefix}-toolbox"
    else:
        name_prefix = "toolbox-term"
    options = {
        "image": TERMINAL_SANDBOX_IMAGE,
        "mount_path": TERMINAL_SANDBOX_MOUNT_PATH,
        "shell": TERMINAL_SANDBOX_SHELL,
        "network": TERMINAL_SANDBOX_NETWORK,
        "cpus": TERMINAL_SANDBOX_CPUS,
        "memory": TERMINAL_SANDBOX_MEMORY,
        "binds": list(TERMINAL_SANDBOX_BINDS),
        "bin": TERMINAL_SANDBOX_BIN,
        "name_prefix": name_prefix,
        "env": dict(TERMINAL_SANDBOX_ENV),
        "require": TERMINAL_SANDBOX_REQUIRE,
    }
    if container_session and container_session.mode == "docker":
        options["container_name"] = container_session.container_name
        options["mount_path"] = container_session.mount_path
    return options


class ToolboxContainer:
    """为 run_command 提供的专用容器."""

    def __init__(
        self,
        project_path: str,
        sandbox_mode: Optional[str] = None,
        sandbox_options: Optional[Dict] = None,
        idle_timeout: int = TOOLBOX_TERMINAL_IDLE_SECONDS,
        container_session: Optional["ContainerHandle"] = None,
    ):
        self.project_path = Path(project_path).resolve()
        self.default_mode = (sandbox_mode or TERMINAL_SANDBOX_MODE or "host").lower()
        self._default_mount_path = TERMINAL_SANDBOX_MOUNT_PATH or "/workspace"
        self.container_session: Optional["ContainerHandle"] = None
        self.sandbox_mode = self.default_mode
        options = _build_sandbox_options(container_session)
        if sandbox_options:
            for key, value in sandbox_options.items():
                if key == "binds" and isinstance(value, list):
                    options[key] = list(value)
                elif key == "env" and isinstance(value, dict):
                    options[key] = dict(value)
                else:
                    options[key] = value
        self.sandbox_options = options
        self._apply_container_session(container_session)
        self.idle_timeout = max(0, int(idle_timeout)) if idle_timeout is not None else 0

        self._terminal: Optional[PersistentTerminal] = None
        self._lock = asyncio.Lock()
        self._session_name = f"toolbox-{uuid.uuid4().hex[:10]}"
        self._last_used = 0.0

    def _apply_container_session(self, session: Optional["ContainerHandle"]):
        self.container_session = session
        if session and session.mode == "docker":
            self.sandbox_mode = "docker"
            self.sandbox_options["container_name"] = session.container_name
            self.sandbox_options["mount_path"] = session.mount_path
        elif session:
            self.sandbox_mode = "host"
            self.sandbox_options.pop("container_name", None)
            self.sandbox_options["mount_path"] = self._default_mount_path
        else:
            self.sandbox_mode = self.default_mode
            if session is None:
                self.sandbox_options.pop("container_name", None)
            self.sandbox_options["mount_path"] = self._default_mount_path

    def set_container_session(self, session: Optional["ContainerHandle"]):
        if session is self.container_session:
            return
        self._apply_container_session(session)
        if self._terminal:
            self._terminal.close()
            self._terminal = None

    async def _ensure_terminal(self) -> PersistentTerminal:
        """确保容器已启动。"""
        async with self._lock:
            if self._terminal and self._terminal.is_running:
                return self._terminal

            terminal = PersistentTerminal(
                session_name=self._session_name,
                working_dir=str(self.project_path),
                broadcast_callback=None,
                project_path=str(self.project_path),
                sandbox_mode=self.sandbox_mode,
                sandbox_options=self.sandbox_options,
            )

            if not terminal.start():
                raise RuntimeError("工具容器启动失败，请检查 Docker 或本地 shell 环境。")

            self._terminal = terminal
            self._last_used = time.time()
            print(f"[Toolbox] 工具容器已启动 (session={self._session_name}, mode={terminal.execution_mode})")
            return terminal

    def _wrap_command(self, command: str, work_path: Path, execution_mode: Optional[str]) -> str:
        """根据执行模式动态拼接 cd 指令。"""
        if work_path == self.project_path:
            return command

        try:
            relative = work_path.relative_to(self.project_path).as_posix()
        except ValueError:
            relative = ""

        if not relative:
            return command

        if execution_mode == "docker":
            mount_path = self.sandbox_options.get("mount_path", "/workspace").rstrip("/")
            target = f"{mount_path}/{relative}"
        else:
            target = str(work_path)

        return f"cd {shlex.quote(target)} && {command}"

    async def run(self, command: str, work_path: Path, timeout: Optional[float] = None) -> Dict:
        """在容器/主机终端中执行命令。"""
        terminal = await self._ensure_terminal()
        shell_command = self._wrap_command(command, work_path, terminal.execution_mode)
        result = await asyncio.to_thread(
            terminal.send_command,
            shell_command,
            timeout,
            timeout,  # timeout_cutoff 与等待时间一致
            True,     # enforce_full_timeout：避免因空闲过早返回
            None,     # sentinel
        )
        self._last_used = time.time()
        self._cleanup_if_idle()
        return result

    def _cleanup_if_idle(self):
        if self.idle_timeout <= 0 or not self._terminal:
            return
        if time.time() - self._last_used >= self.idle_timeout:
            self._terminal.close()
            print(f"[Toolbox] 工具容器空闲超时，已释放 (session={self._session_name})")
            self._terminal = None

    def shutdown(self):
        """立即关闭容器。"""
        if self._terminal:
            self._terminal.close()
            print(f"[Toolbox] 工具容器已关闭 (session={self._session_name})")
            self._terminal = None

    @property
    def execution_mode(self) -> Optional[str]:
        if self._terminal:
            return self._terminal.execution_mode
        return None

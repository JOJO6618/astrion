# modules/persistent_terminal/base.py - 持久化终端实例（Host-only）
#
# 持久终端只支持宿主机：Host 沙箱交互 shell 或 direct 直连 shell。
# Docker 容器终端（docker exec 进入用户容器、容器只读 uid、容器挂载与
# 容器生命周期）已拆到独立 Docker fork。构造时显式传入 Docker 模式会被
# 拒绝，不会静默回落成宿主机终端。

import os
import sys
import time
import queue
from collections import deque
from pathlib import Path
from typing import Optional, Callable, Dict

from modules.execution_scope import current_execution_scope
from modules.scoped_execution_policy import scoped_work_path


class PersistentTerminalBase:
    """PersistentTerminal 基础类，包含初始化与清理。"""

    def __init__(
        self,
        session_name: str,
        working_dir: str = None,
        shell_command: str = None,
        broadcast_callback: Callable = None,
        max_buffer_size: int = 20000,
        display_size: int = 5000,
        project_path: Optional[str] = None,
        sandbox_mode: Optional[str] = None,
        sandbox_options: Optional[Dict] = None,
        network_permission_getter: Optional[Callable] = None,
        execution_scope=None,
    ):
        """
        初始化持久化终端

        Args:
            session_name: 会话名称
            working_dir: 工作目录
            shell_command: shell命令（None则自动选择）
            broadcast_callback: 广播回调函数（用于WebSocket）
            max_buffer_size: 最大缓冲区大小
            display_size: 显示大小限制
        """
        normalized_mode = str(sandbox_mode or "host").strip().lower()
        if normalized_mode != "host":
            raise ValueError(f"PersistentTerminal 仅接受 Host 终端模式，收到 {normalized_mode!r}。")
        options = dict(sandbox_options or {})
        if options.get("container_name"):
            raise ValueError(
                "PersistentTerminal 是 Host-only 构建，不支持容器挂载/容器终端参数。"
            )
        if options.get("docker_readonly_exec"):
            raise ValueError(
                "PersistentTerminal 是 Host-only 构建，不支持 Docker 只读执行身份。"
            )
        # Docker 专属键即使为假值也不再参与 Host 执行，直接丢弃。
        for legacy_key in ("container_name", "mount_path", "image", "cpus", "memory",
                           "binds", "bin", "name_prefix", "env", "require",
                           "docker_readonly_exec"):
            options.pop(legacy_key, None)

        self.session_name = session_name
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.project_path = Path(project_path).resolve() if project_path else self.working_dir
        self.execution_scope = execution_scope or (sandbox_options or {}).get("execution_scope") or current_execution_scope()
        if self.execution_scope:
            self.working_dir = scoped_work_path(self.project_path, str(self.working_dir), self.execution_scope)
        self.host_shell_command = shell_command
        self.shell_command = shell_command
        self.broadcast = broadcast_callback
        self.max_buffer_size = max_buffer_size
        self.display_size = display_size
        # 网络权限来源：优先由所属 WebTerminal 注入 getter 实时取值；
        # 不可用时才回落进程级环境变量。
        self.network_permission_getter = network_permission_getter
        self._network_permission_snapshot = None
        if self.execution_scope:
            self._network_permission_snapshot = self._resolve_network_permission()

        # 进程相关
        self.process = None
        self.is_running = False
        self.start_time = None

        # 输出缓冲
        self.output_buffer = []
        self.command_history = []
        self.total_output_size = 0
        self.truncated_lines = 0
        self.output_history = deque()
        self._output_event_counter = 0
        self.last_output_time = None
        self.last_input_time = None
        self.last_input_text = ""
        self.echo_loop_detected = False
        self._consecutive_echo_matches = 0
        self.io_history = deque()
        self._io_history_max = 4000

        # 线程和队列
        self.output_queue = queue.Queue()
        self.reader_thread = None
        self.is_reading = False
        # _read_output 自适应解码（io.py）用于暂存被块边界切断的不完整多字节序列
        self._pending_bytes = b''

        # 状态标志
        self.is_interactive = False  # 是否在等待输入
        self.last_command = ""
        self.last_activity = time.time()

        # 系统特定设置
        self.is_windows = sys.platform == "win32"
        self.sandbox_mode = "host"
        self.sandbox_options = options
        self.allow_direct_host_execution = (
            self.execution_scope.execution_mode == "direct" if self.execution_scope
            else bool(options.get("allow_direct_host_execution", False))
        )
        if self.execution_scope and self.execution_scope.is_sub_agent:
            # 子智能体的写边界由固定执行作用域控制；终端本身不再额外叠加只读身份。
            self.sandbox_options["host_terminal_readonly"] = False
        self.execution_mode = "host"

    def _resolve_network_permission(self) -> str:
        """取当前网络权限设置：getter 实时值优先，环境变量兼容回落。"""
        snapshot = getattr(self, "_network_permission_snapshot", None)
        if snapshot is not None:
            return snapshot
        getter = getattr(self, "network_permission_getter", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value).strip().lower()
            except Exception:
                pass
        return os.environ.get("HOST_SANDBOX_NETWORK_PERMISSION", "restricted")

    def __del__(self):
        """析构函数，确保进程被关闭"""
        if hasattr(self, 'is_running') and self.is_running:
            self.close()

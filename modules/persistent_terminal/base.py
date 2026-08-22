# modules/persistent_terminal.py - 持久化终端实例（修复版）

import asyncio
import subprocess
import os
import sys
import time
import signal
from pathlib import Path
from typing import Optional, Callable, Dict, List, Tuple
from datetime import datetime
import threading
import queue
from collections import deque
import shutil
import uuid
import codecs
from modules.host_sandbox_runner import (
    HostSandboxError,
    build_host_sandbox_shell_plan,
    host_sandbox_enabled,
)
try:
    from config import (
        OUTPUT_FORMATS,
        TERMINAL_OUTPUT_WAIT,
        TERMINAL_INPUT_MAX_CHARS,
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
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        TERMINAL_OUTPUT_WAIT,
        TERMINAL_INPUT_MAX_CHARS,
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
    )


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
        self.session_name = session_name
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.project_path = Path(project_path).resolve() if project_path else self.working_dir
        self.host_shell_command = shell_command
        self.shell_command = shell_command
        self.broadcast = broadcast_callback
        self.max_buffer_size = max_buffer_size
        self.display_size = display_size
        # 网络权限来源：优先由所属 WebTerminal 注入 getter 实时取值；
        # 不可用时才回落进程级环境变量（兼容 toolbox_container 等独立使用方）。
        self.network_permission_getter = network_permission_getter
        
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
        sandbox_defaults = {
            "image": TERMINAL_SANDBOX_IMAGE,
            "mount_path": TERMINAL_SANDBOX_MOUNT_PATH,
            "shell": TERMINAL_SANDBOX_SHELL,
            "network": TERMINAL_SANDBOX_NETWORK,
            "cpus": TERMINAL_SANDBOX_CPUS,
            "memory": TERMINAL_SANDBOX_MEMORY,
            "binds": list(TERMINAL_SANDBOX_BINDS),
            "bin": TERMINAL_SANDBOX_BIN,
            "name_prefix": TERMINAL_SANDBOX_NAME_PREFIX,
            "env": dict(TERMINAL_SANDBOX_ENV),
            "require": TERMINAL_SANDBOX_REQUIRE,
        }
        if sandbox_options:
            for key, value in sandbox_options.items():
                if key == "binds" and isinstance(value, list):
                    sandbox_defaults[key] = list(value)
                elif key == "env" and isinstance(value, dict):
                    sandbox_defaults[key] = dict(value)
                else:
                    sandbox_defaults[key] = value
        self.sandbox_mode = (sandbox_mode or TERMINAL_SANDBOX_MODE or "host").lower()
        self.sandbox_options = sandbox_defaults
        self.sandbox_required = bool(self.sandbox_options.get("require"))
        self.allow_direct_host_execution = bool(self.sandbox_options.get("allow_direct_host_execution", False))
        self.sandbox_container_name = None
        self.execution_mode = "host"
        self.using_container = False
        self._sandbox_bin_path = None
        self._owns_container = False

    def _resolve_network_permission(self) -> str:
        """取当前网络权限设置：getter 实时值优先，环境变量兼容回落。"""
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

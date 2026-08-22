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


class LifecycleMixin:
    """PersistentTerminal lifecycle 能力 mixin。"""

    def get_snapshot(self, last_n_lines: Optional[int], max_chars: Optional[int]) -> Dict:
        """获取终端快照，包含按顺序排列的输入/输出"""
        include_all_lines = last_n_lines is None or last_n_lines <= 0
        if not include_all_lines:
            last_n_lines = max(1, last_n_lines)

        segments: List[str] = []
        reset = "\x1b[0m"
        command_color = "\x1b[1;32m"
        for event_type, _, data in self.io_history:
            if event_type == 'input':
                # 显示输入命令并加上ANSI颜色，保持与实时终端一致
                input_text = data.rstrip('\n')
                decorated = f"{command_color}➜ {input_text}{reset}" if input_text.strip() else f"{command_color}➜{reset}"
                if not decorated.endswith('\n'):
                    decorated += '\n'
                segments.append(decorated)
            else:
                cleaned = data.replace('\r', '')
                segments.append(cleaned)

        full_text = ''.join(segments)
        if full_text:
            line_chunks = full_text.splitlines(keepends=True)
            total_lines = len(line_chunks)
            if include_all_lines:
                selected_lines = line_chunks
                lines_requested = total_lines
            else:
                count = min(last_n_lines, total_lines)
                selected_lines = line_chunks[-count:] if count else []
                lines_requested = last_n_lines
            output_text = ''.join(selected_lines)
        else:
            output_text = ''
            lines_requested = 0
            total_lines = 0

        truncated = False
        if max_chars is not None and max_chars > 0 and len(output_text) > max_chars:
            output_text = output_text[-max_chars:]
            truncated = True

        # 统计行数
        if output_text:
            lines_returned = output_text.count('\n') + (0 if output_text.endswith('\n') else 1)
        else:
            lines_returned = 0

        if include_all_lines:
            lines_requested = total_lines

        return {
            "success": True,
            "session": self.session_name,
            "output": output_text,
            "lines_requested": lines_requested,
            "lines_returned": lines_returned,
            "truncated": truncated,
            "is_interactive": self.is_interactive,
            "echo_loop_detected": self.echo_loop_detected,
            "seconds_since_last_output": self._seconds_since_last_output(),
            "last_command": self.last_command,
            "buffer_size": self.total_output_size,
            "timestamp": datetime.now().isoformat(),
            "last_event_time": self._get_last_event_time()
        }

    def _get_last_event_time(self) -> Optional[float]:
        """获取 io_history 中最后一条事件的时间戳，用于前端去重"""
        if self.io_history:
            return self.io_history[-1][1]
        return None

    def get_status(self) -> Dict:
        """获取终端状态"""
        return {
            "session_name": self.session_name,
            "is_running": self.is_running,
            "working_dir": str(self.working_dir),
            "shell": self.shell_command,
            "execution_mode": self.execution_mode,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "is_interactive": self.is_interactive,
            "last_command": self.last_command,
            "command_count": len(self.command_history),
            "buffer_size": self.total_output_size,
            "truncated_lines": self.truncated_lines,
            "last_activity": datetime.fromtimestamp(self.last_activity).isoformat(),
            "uptime_seconds": (datetime.now() - self.start_time).total_seconds() if self.start_time else 0,
            "seconds_since_last_output": self._seconds_since_last_output(),
            "echo_loop_detected": self.echo_loop_detected
        }

    def close(self) -> bool:
        """关闭终端"""
        if not self.is_running:
            return False
        
        try:
            # 停止读取线程
            self.is_reading = False
            
            # 发送退出命令
            if self.process and self.process.poll() is None:
                exit_cmd = "exit\n"
                try:
                    self.process.stdin.write(exit_cmd.encode('utf-8'))
                    self.process.stdin.flush()
                except:
                    pass
                
                # 等待进程结束
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # 强制终止
                    self.process.terminate()
                    time.sleep(0.5)
                    if self.process.poll() is None:
                        self.process.kill()
            
            self.is_running = False
            
            # 等待读取线程结束
            if self.reader_thread and self.reader_thread.is_alive():
                self.reader_thread.join(timeout=1)
            
            # 广播终端关闭事件
            if self.broadcast:
                self.broadcast('terminal_closed', {
                    'session': self.session_name,
                    'time': datetime.now().isoformat()
                })
            
            if self.using_container:
                self._stop_sandbox_container()
                self.using_container = False
                self.execution_mode = "host"

            print(f"{OUTPUT_FORMATS['info']} 终端会话关闭: {self.session_name}")
            return True
            
        except Exception as e:
            print(f"{OUTPUT_FORMATS['error']} 关闭终端失败: {e}")
            return False

    def _stop_sandbox_container(self, force: bool = False):
        """确保容器终端被停止"""
        if not self._owns_container or not self.sandbox_container_name or not self._sandbox_bin_path:
            return
        try:
            subprocess.run(
                [self._sandbox_bin_path, "kill", self.sandbox_container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False
            )
        except Exception:
            if force:
                print(f"{OUTPUT_FORMATS['warning']} 强制终止容器 {self.sandbox_container_name} 失败，可能已退出。")
        finally:
            self.sandbox_container_name = None
            self._owns_container = False

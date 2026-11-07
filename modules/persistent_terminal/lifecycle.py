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

    @staticmethod
    def _close_owned_process_streams(process) -> None:
        """只关闭本实例自己经过 ``PIPE`` 持有的 stdin/stdout/stderr。

        ``Popen`` 仅在对应参数为 ``PIPE`` 时才把句柄放到 ``process.stdin`` /
        ``process.stdout`` / ``process.stderr``；属性为 ``None``（例如
        ``stderr=STDOUT`` 或调用方传入共享文件对象）说明句柄不属于本实例，
        绝不能关闭。关闭本身幂等，重复调用不会报错。
        """
        if process is None:
            return
        for stream in (
            getattr(process, "stdin", None),
            getattr(process, "stdout", None),
            getattr(process, "stderr", None),
        ):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _process_is_alive(process) -> bool:
        """鸭子类型安全的存活判断：拿不到 poll 就不当成存活。"""
        poll = getattr(process, "poll", None)
        if not callable(poll):
            return False
        try:
            return poll() is None
        except Exception:
            return False

    def _stop_owned_process(self, process) -> None:
        """结束并回收本实例 spawn 的 shell（不碰外部进程）。

        先发 ``exit`` 让交互 shell 正常结束，读线程才能收到 EOF 并排空剩余输出；
        超时后 terminate → kill，且无论如何都 ``wait`` 到进程被回收。
        """
        if process is None:
            return
        if not self._process_is_alive(process):
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None and not getattr(stdin, "closed", False):
            try:
                stdin.write("exit\n".encode("utf-8"))
                stdin.flush()
            except Exception:
                pass
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=0.5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            pass

    def _close_streams_after_reader(self, process, reader) -> None:
        """只在 reader 已经退出后关闭 PIPE；否则推迟到 reader 退出后。

        reader 还活着说明它阻塞在 ``readline``（孙进程继承了写端，EOF 未到）。
        此时从本线程 close 会去抢 reader 的内部锁，可能永久阻塞；
        所以这里只登记一个后台收口线程（daemon，不阻塞 close）。
        """
        if reader is not None and reader.is_alive():
            self._defer_close_streams(process, reader)
            return
        self._close_owned_process_streams(process)

    def _defer_close_streams(self, process, reader) -> None:
        """reader 退出后再关句柄：保证不丢尾部输出，也不让 close 阻塞。"""
        def _reap() -> None:
            try:
                reader.join()
            except Exception:
                pass
            self._close_owned_process_streams(process)

        threading.Thread(
            target=_reap,
            name=f"terminal-close-{self.session_name}",
            daemon=True,
        ).start()

    def close(self) -> bool:
        """关闭终端。

        幂等：重复调用只做句柄收口（不再重复广播/打印），返回 False。
        资源顺序固定为「结束并 wait 自己的进程 → 等 reader 读到 EOF 排空尾部输出
        → 关自己持有的 PIPE 句柄」。

        注意：不能在结束进程前就置 ``is_reading = False``——io.py::_read_output
        的循环条件是 ``while self.is_reading``，提前置假会让 reader 读完当前块就
        退出循环，丢掉管道里尚未读出的尾部输出（与“等 reader 排空”矛盾）。
        """
        process = getattr(self, "process", None)
        reader = getattr(self, "reader_thread", None)
        if not self.is_running:
            # 已经关闭过（或启动中途失败）：仍要幂等收口，防止 Popen 的管道泄漏。
            self._stop_owned_process(process)
            self.is_reading = False
            self._close_streams_after_reader(process, reader)
            return False

        try:
            # 结束并回收自己的 shell；此后 stdout 才会到达 EOF。
            self._stop_owned_process(process)

            self.is_running = False

            # 等待 reader 读到 EOF 并排空剩余输出（不是“清空循环条件”）。
            if reader is not None and reader.is_alive() and reader is not threading.current_thread():
                reader.join(timeout=2)

            self.is_reading = False
            self._close_streams_after_reader(process, reader)

            # 广播终端关闭事件
            if self.broadcast:
                self.broadcast('terminal_closed', {
                    'session': self.session_name,
                    'time': datetime.now().isoformat()
                })

            if self.using_container:
                # 终端经 docker exec 附着到 UserContainerManager 拥有的用户容器，
                # 关闭终端只结束 exec 进程，绝不停容器本身（容器生命周期由容器
                # 管理器负责）。历史上「终端自建临时容器」路径使用的
                # _stop_sandbox_container 已随该路径一并删除。
                self.using_container = False
                self.execution_mode = "host"

            print(f"{OUTPUT_FORMATS['info']} 终端会话关闭: {self.session_name}")
            return True

        except Exception as e:
            print(f"{OUTPUT_FORMATS['error']} 关闭终端失败: {e}")
            self.is_reading = False
            self._close_streams_after_reader(process, reader)
            return False

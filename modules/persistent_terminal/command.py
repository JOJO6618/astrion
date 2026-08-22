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


class CommandMixin:
    """PersistentTerminal command 能力 mixin。"""

    def send_command(
        self,
        command: str,
        timeout: float = None,
        timeout_cutoff: float = None,
        enforce_full_timeout: bool = False,
        sentinel: str = None,
    ) -> Dict:
        """
        发送命令到终端（统一编码处理）。

        Args:
            command: 要执行的命令文本
            timeout: 等待输出的最大秒数（可大于真实超时，用于等待收尾输出）
            timeout_cutoff: 将耗时大于此值视为超时，用于外层业务区分；默认为 timeout
            enforce_full_timeout: 若为 True，则不因空闲提前返回（除非捕获 sentinel）
            sentinel: 若提供，在输出中捕获到该标记即认为命令结束，并从输出中移除
        """
        if not self.is_running or not self.process:
            return {
                "success": False,
                "error": "终端未运行，请先打开终端会话。",
                "session": self.session_name
            }
        
        try:
            # 清空残留输出，防止上一条命令的输出干扰
            try:
                while True:
                    self.output_queue.get_nowait()
            except queue.Empty:
                pass

            marker = self._capture_history_marker()
            if timeout is None:
                timeout = TERMINAL_OUTPUT_WAIT
            else:
                try:
                    timeout = float(timeout)
                except (TypeError, ValueError):
                    timeout = TERMINAL_OUTPUT_WAIT
            if timeout < 0:
                timeout = 0
            start_time = time.time()
            command_text = command.rstrip('\n')
            # 记录命令
            self.command_history.append({
                "command": command_text,
                "timestamp": datetime.now().isoformat()
            })
            self.last_command = command_text
            self.is_interactive = False
            self.last_input_text = command_text
            self.last_input_time = time.time()
            self.echo_loop_detected = False
            self._consecutive_echo_matches = 0
            self._append_io_event('input', command_text + '\n', timestamp=self.last_input_time)

            # 广播输入事件
            if self.broadcast:
                self.broadcast('terminal_input', {
                    'session': self.session_name,
                    'data': command_text + '\n',
                    'timestamp': time.time()
                })
            
            # 确保命令有换行符
            to_send = command if command.endswith('\n') else command + '\n'
            
            # 发送命令（统一使用UTF-8编码）
            try:
                # 首先尝试UTF-8
                command_bytes = to_send.encode('utf-8')
            except UnicodeEncodeError:
                # 如果UTF-8失败，Windows系统尝试GBK
                if self.is_windows:
                    command_bytes = to_send.encode('gbk', errors='replace')
                else:
                    command_bytes = to_send.encode('utf-8', errors='replace')
            
            try:
                self.process.stdin.write(command_bytes)
                self.process.stdin.flush()
            except Exception:
                return {
                    "success": False,
                    "error": "终端已不可用或输入失败，请重新打开终端会话。",
                    "session": self.session_name
                }
            
            # 等待输出
            output, timed_out, marker_seen = self._wait_for_output(
                timeout=timeout,
                timeout_cutoff=timeout_cutoff,
                enforce_full_timeout=enforce_full_timeout,
                sentinel=sentinel,
                command_echo=command_text,
            )
            recent_output = self._get_output_since_marker(marker)
            if recent_output:
                if sentinel:
                    recent_output = recent_output.replace(sentinel, "")
                output = recent_output
            output = self._clean_output(output, command_text, sentinel)
            output_truncated = False
            if len(output) > TERMINAL_INPUT_MAX_CHARS:
                output = output[-TERMINAL_INPUT_MAX_CHARS:]
                output_truncated = True
            output_clean = output.strip()
            has_output = bool(output_clean)
            status = "completed"
            if timed_out:
                status = "timeout"
            elif not has_output:
                if self.echo_loop_detected:
                    status = "echo_loop"
                elif self.is_interactive:
                    status = "awaiting_input"
                else:
                    status = "no_output"
            else:
                if self.echo_loop_detected:
                    status = "output_with_echo"
            if marker_seen and status == "completed":
                # 明确捕获到结束标记，视为完成
                status = "completed"
            message_map = {
                "completed": "命令执行完成",
                "no_output": "未捕获输出，命令可能未产生结果",
                "awaiting_input": "命令已发送，终端等待进一步输入或仍在运行",
                "echo_loop": "检测到终端正在回显输入，命令可能未成功执行",
                "output_with_echo": "命令产生输出，但终端疑似重复回显",
                "timeout": f"输出等待达到上限（{int(timeout)}秒）"
            }
            if timeout >= 60:
                message = f"[已收集约{int(timeout)}秒内的输出]"
            else:
                message = message_map.get(status, "命令执行完成")
            if output_truncated:
                message += f"（输出已截断，保留末尾{TERMINAL_INPUT_MAX_CHARS}字符）"
            elapsed_ms = int((time.time() - start_time) * 1000)
            return {
                "success": status in {"completed", "output_with_echo"},
                "session": self.session_name,
                "command": command_text,
                "output": output,
                "message": message,
                "status": status,
                "truncated": output_truncated,
                "elapsed_ms": elapsed_ms,
                "timeout": timeout_cutoff or timeout
            }
                
        except Exception as e:
            error_msg = f"发送命令失败: {str(e)}"
            print(f"{OUTPUT_FORMATS['error']} {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "session": self.session_name
            }

    def _wait_for_output(
        self,
        timeout: float = 5,
        timeout_cutoff: Optional[float] = None,
        enforce_full_timeout: bool = False,
        sentinel: Optional[str] = None,
        command_echo: Optional[str] = None,
    ) -> Tuple[str, bool, bool]:
        """
        等待并收集输出，返回 (output, timed_out, marker_seen)。

        - 若提供 sentinel，捕获后立即返回（仍会吸干队列中的剩余片段）。
        - 若 enforce_full_timeout=True，则不因空闲提前返回；否则在输出后短暂空闲可提前返回。
        - timed_out 判定使用 timeout_cutoff（若未提供则与 timeout 相同）。
        """
        collected_output = []
        start_time = time.time()
        last_output_time = start_time
        output_seen = False
        marker_seen = False

        if timeout is None or timeout <= 0:
            timeout = 0

        if timeout == 0:
            try:
                while True:
                    output = self.output_queue.get_nowait()
                    if sentinel and sentinel in output:
                        output = output.replace(sentinel, "")
                        marker_seen = True
                    if output:
                        collected_output.append(output)
                # unreachable
            except queue.Empty:
                return ''.join(collected_output), False, marker_seen

        end_time = start_time + timeout
        cutoff = timeout_cutoff if timeout_cutoff is not None else timeout
        # 空闲提前返回仅在未强制等待且未使用结束标记时有效
        idle_threshold = None if enforce_full_timeout or sentinel else 1.5

        while True:
            now = time.time()
            if now >= end_time:
                break
            remaining = max(0.05, min(0.5, end_time - now))
            try:
                output = self.output_queue.get(timeout=remaining)
                if sentinel and sentinel in output:
                    # 避免把命令回显中的标记误判为完成信号
                    if command_echo and command_echo in output:
                        output = output.replace(sentinel, "")
                    else:
                        output = output.replace(sentinel, "")
                        marker_seen = True
                if output:
                    collected_output.append(output)
                    last_output_time = time.time()
                    output_seen = True
                # 尽量一次性收集当前批次，但受时间上限约束，避免无限循环
                while time.time() < end_time:
                    try:
                        extra = self.output_queue.get(timeout=0.01)
                        if sentinel and sentinel in extra:
                            if command_echo and command_echo in extra:
                                extra = extra.replace(sentinel, "")
                            else:
                                extra = extra.replace(sentinel, "")
                                marker_seen = True
                        if extra:
                            collected_output.append(extra)
                            last_output_time = time.time()
                            output_seen = True
                    except queue.Empty:
                        break
            except queue.Empty:
                pass

            if marker_seen:
                # 捕获到结束标记，立即返回
                break
            if idle_threshold and output_seen and (time.time() - last_output_time) > idle_threshold:
                break

        elapsed = time.time() - start_time
        timed_out = bool(cutoff and cutoff > 0 and elapsed >= cutoff)
        return ''.join(collected_output), timed_out, marker_seen

    @staticmethod
    def _clean_output(output: str, command_text: str, sentinel: Optional[str]) -> str:
        """
        移除封装命令回显和完成标记，保留纯净的命令输出。
        """
        if not output:
            return output
        lines = output.splitlines()
        cleaned = []
        for idx, line in enumerate(lines):
            # 去掉标记行
            if sentinel and sentinel in line:
                continue
            # 尝试剥离提示符
            for token in ("# ", "$ "):
                pos = line.find(token)
                if 0 <= pos <= 40 and "@" in line[:pos]:
                    line = line[pos + len(token):]
                    break
            # 去掉封装命令回显
            if idx == 0 and command_text:
                if line.strip() == command_text.strip():
                    continue
                # 包含 timeout/sh -c 的封装行也忽略
                if "timeout -k" in line and "sh -c" in line:
                    continue
            cleaned.append(line)
        # 保持末尾换行与原输出一致
        out = "\n".join(cleaned)
        if output.endswith("\n") and cleaned:
            out += "\n"
        return out

    def get_output(self, last_n_lines: int = 50) -> str:
        """
        获取终端输出
        
        Args:
            last_n_lines: 获取最后N行
            
        Returns:
            输出内容
        """
        if last_n_lines <= 0:
            return ''.join(self.output_buffer)
        
        # 获取最后N行
        lines = []
        for line in reversed(self.output_buffer):
            lines.insert(0, line)
            if len(lines) >= last_n_lines:
                break
        
        return ''.join(lines)

    def get_display_output(self) -> str:
        """获取用于显示的输出（截断到display_size）"""
        output = self.get_output()
        if len(output) > self.display_size:
            # 保留最后的display_size字符
            output = output[-self.display_size:]
            output = f"[输出已截断，显示最后{self.display_size}字符]\n{output}"
        return output

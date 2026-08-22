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


def _gbk_complete_prefix_len(data: bytes) -> int:
    """返回 data 中按 GBK 可完整解析的前缀长度。

    GBK：ASCII 单字节（0x00-0x7F）；双字节 lead 0x81-0xFE + trail 0x40-0xFE（不含 0x7F）。
    末尾孤立的 lead byte 视为被块边界切断的半字，不计入前缀（留给下一块拼合）。
    """
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b <= 0x7F:
            i += 1
        elif 0x81 <= b <= 0xFE:
            if i + 1 < n and 0x40 <= data[i + 1] <= 0xFE and data[i + 1] != 0x7F:
                i += 2
            else:
                break  # 孤立 lead byte（可能是边界切断的半字）
        else:
            i += 1  # 0x80/0xFF 非法字节，按单字节跳过（由 replace 兜底）
    return i


class IoMixin:
    """PersistentTerminal io 能力 mixin。"""

    def _read_output(self):
        """后台线程：持续读取输出。

        Windows 下不再写死 GBK：cmd 会话启动时已注入 chcp 65001（见 start.py），
        且 PYTHONIOENCODING=utf-8 使子进程 Python 程序输出 UTF-8；但用户可能
        手动 chcp 回 936、或运行只输出 GBK 的旧程序。因此逐块自适应解码：
        先试 UTF-8，失败则整块按 GBK；块边界切断的多字节序列用
        _pending_bytes 暂存，与下一块拼合后再解（见 _decode_chunk）。
        """
        while self.is_reading and self.process:
            try:
                # 读取任意字节块，不依赖换行符
                chunk = self.process.stdout.read(1024)
                if chunk:
                    text = self._decode_chunk(chunk)
                    if text:
                        self.output_queue.put(text)
                        self._process_output(text)
                elif self.process.poll() is not None:
                    # 进程已结束
                    self.is_running = False
                    break
                else:
                    # 没有输出，短暂休眠
                    time.sleep(0.01)

            except Exception as e:
                # 不要因为单个错误而停止
                print(f"[Terminal] 读取输出警告: {e}")
                time.sleep(0.01)
                continue
        # 进程结束后冲刷残留的不完整字节（多为被截断的多字节序列半字）
        pending = getattr(self, '_pending_bytes', b'')
        if pending:
            fallback = 'gbk' if self.is_windows else 'utf-8'
            text = pending.decode(fallback, errors='replace')
            self._pending_bytes = b''
            if text:
                self.output_queue.put(text)
                self._process_output(text)

    def _decode_chunk(self, chunk: bytes) -> str:
        """把 stdout 字节块解码为文本（Windows：UTF-8 优先、GBK 回退）。

        判定依据：UTF-8 有严格的多字节结构校验，GBK 字节流极少恰好构成
        合法 UTF-8，因此「UTF-8 能解」是强信号；解不了再按 GBK。
        两种路径都会把块末尾不完整的多字节序列暂存 _pending_bytes，
        与下一块拼合后再解，避免 read 边界切断汉字。

        已知限制（业界通病，VS Code/Windows Terminal 同样无法处理）：
        极少数 GBK 汉字对的字节恰好也是合法 UTF-8（如「目录」= C4BF C2BC
        会解为 Ŀ¼），此时若后续块紧跟 UTF-8 内容会连锁误判。终端会话已
        注入 chcp 65001（start.py），正常路径全程 UTF-8，GBK 仅为兜底，
        该巧合仅影响显示、不丢数据，故不做「罕见字符惩罚」式启发式猜测。
        """
        data = getattr(self, '_pending_bytes', b'') + chunk
        self._pending_bytes = b''
        if not data:
            return ''
        if not self.is_windows:
            return data.decode('utf-8', errors='replace')
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError as exc:
            # 末尾 1~3 字节可能是不完整 UTF-8 序列（read 边界切断），暂存待拼
            if exc.start >= len(data) - 3:
                head, pending = data[:exc.start], data[exc.start:]
                try:
                    text = head.decode('utf-8')
                    self._pending_bytes = pending
                    return text
                except UnicodeDecodeError:
                    pass  # head 也含非法序列 → 并非 UTF-8 流，落入 GBK 路径
            # 非法序列位于中部（或 UTF-8 边界暂存失败）→ 确定不是 UTF-8
            # （如 cmd 内置命令的 GBK 输出），按 GBK 边界感知解码
            return self._decode_gbk_boundary(data)

    def _decode_gbk_boundary(self, data: bytes) -> str:
        """按 GBK 解码，末尾不完整的双字节序列（被切断的半字）暂存待拼。"""
        safe_len = _gbk_complete_prefix_len(data)
        self._pending_bytes = data[safe_len:]
        return data[:safe_len].decode('gbk', errors='replace')

    def _decode_output(self, data):
        """安全地解码输出"""
        # 如果已经是字符串，直接返回
        if isinstance(data, str):
            return data
        
        # 如果是字节，尝试解码
        if isinstance(data, bytes):
            # Windows系统尝试的编码顺序
            if self.is_windows:
                encodings = ['utf-8', 'gbk', 'gb2312', 'cp936', 'latin-1']
            else:
                encodings = ['utf-8', 'latin-1']
            
            for encoding in encodings:
                try:
                    return data.decode(encoding)
                except (UnicodeDecodeError, AttributeError):
                    continue
            
            # 如果所有编码都失败，使用替换模式
            return data.decode('utf-8', errors='replace')
        
        # 其他类型，转换为字符串
        return str(data)

    def _process_output(self, output: str):
        """处理输出行"""
        now = time.time()
        noisy_markers = (
            "bash: cannot set terminal process group",
            "bash: no job control in this shell",
        )
        for line in output.splitlines(keepends=True):
            if any(noise in line for noise in noisy_markers):
                continue
            self.output_buffer.append(line)
            self.total_output_size += len(line)
            now = time.time()
            self.last_output_time = now
            
            # 记录输出事件
            self._output_event_counter += 1
            self.output_history.append((self._output_event_counter, now, line))
            self._append_io_event('output', line, timestamp=now)

        # 控制输出历史长度
        if len(self.output_history) > 2000:
            self.output_history.popleft()

        # 检查是否需要截断
        if self.total_output_size > self.max_buffer_size:
            self._truncate_buffer()
        
        # 更新活动时间
        self.last_activity = now

        # 检测命令回显死循环
        cleaned_output = output.replace('\r', '').strip()
        cleaned_input = self.last_input_text.strip() if self.last_input_text else ""
        if cleaned_output and cleaned_input and cleaned_output == cleaned_input:
            self._consecutive_echo_matches += 1
        else:
            self._consecutive_echo_matches = 0
            if cleaned_output:
                self.echo_loop_detected = False
        if self._consecutive_echo_matches >= 1 and self.last_input_time:
            if now - self.last_input_time <= 2:
                self.echo_loop_detected = True
        
        # 检测交互式提示
        self._detect_interactive_prompt(output)
        
        # 广播输出
        if self.broadcast:
            self.broadcast('terminal_output', {
                'session': self.session_name,
                'data': output,
                'timestamp': time.time()
            })

    def _truncate_buffer(self):
        """截断缓冲区以保持在限制内"""
        # 保留最后的N个字符
        while self.total_output_size > self.max_buffer_size and self.output_buffer:
            removed = self.output_buffer.pop(0)
            self.total_output_size -= len(removed)
            self.truncated_lines += 1
            if self.output_history:
                self.output_history.popleft()

    def _detect_interactive_prompt(self, output: str):
        """检测是否在等待交互输入"""
        self.is_interactive = False
        # 常见的交互提示模式
        interactive_patterns = [
            "? ",  # 问题提示
            ": ",  # 输入提示
            "> ",  # 命令提示
            "$ ",  # shell提示
            "# ",  # root提示
            ">>> ",  # Python提示
            "... ",  # Python续行
            "(y/n)",  # 确认提示
            "[Y/n]",  # 确认提示
            "Password:",  # 密码提示
            "password:",  # 密码提示
            "Enter",  # 输入提示
            "选择",  # 中文选择
            "请输入",  # 中文输入
        ]
        
        output_lower = output.lower().strip()
        for pattern in interactive_patterns:
            if pattern.lower() in output_lower:
                self.is_interactive = True
                return
        
        # 如果输出以常见提示符结尾且没有换行，也认为是交互式
        if output and not output.endswith('\n'):
            last_chars = output.strip()[-3:]
            if last_chars in ['> ', '$ ', '# ', ': ']:
                self.is_interactive = True

    def _capture_history_marker(self) -> int:
        return self._output_event_counter

    def _get_output_since_marker(self, marker: int) -> str:
        if marker is None:
            return ''.join(item[2] for item in self.output_history)
        return ''.join(item[2] for item in self.output_history if item[0] > marker)

    def _append_io_event(self, event_type: str, data: str, timestamp: Optional[float] = None):
        """记录终端输入输出事件"""
        if timestamp is None:
            timestamp = time.time()
        self.io_history.append((event_type, timestamp, data))
        while len(self.io_history) > self._io_history_max:
            self.io_history.popleft()

    def _seconds_since_last_output(self) -> Optional[float]:
        if not self.last_output_time:
            return None
        return round(time.time() - self.last_output_time, 3)

# modules/terminal_manager.py - 终端会话管理器

import json
import time
import shlex
import shutil
from typing import Dict, List, Optional, Callable, TYPE_CHECKING
from pathlib import Path
from datetime import datetime
try:
    from config import (
        OUTPUT_FORMATS,
        MAX_TERMINALS,
        TERMINAL_BUFFER_SIZE,
        TERMINAL_DISPLAY_SIZE,
        TERMINAL_SNAPSHOT_DEFAULT_LINES,
        TERMINAL_SNAPSHOT_MAX_LINES,
        TERMINAL_SNAPSHOT_MAX_CHARS,
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
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        MAX_TERMINALS,
        TERMINAL_BUFFER_SIZE,
        TERMINAL_DISPLAY_SIZE,
        TERMINAL_SNAPSHOT_DEFAULT_LINES,
        TERMINAL_SNAPSHOT_MAX_LINES,
        TERMINAL_SNAPSHOT_MAX_CHARS,
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

from modules.persistent_terminal import PersistentTerminal
from utils.terminal_factory import TerminalFactory

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle

class TerminalManager:
    """管理多个终端会话"""
    
    def __init__(
        self,
        project_path: str,
        max_terminals: int = None,
        terminal_buffer_size: int = None,
        terminal_display_size: int = None,
        broadcast_callback: Callable = None,
        sandbox_mode: Optional[str] = None,
        sandbox_options: Optional[Dict] = None,
        container_session: Optional["ContainerHandle"] = None,
        network_permission_getter: Optional[Callable] = None,
    ):
        """
        初始化终端管理器
        
        Args:
            project_path: 项目路径
            max_terminals: 最大终端数量
            terminal_buffer_size: 每个终端的缓冲区大小
            terminal_display_size: 显示大小限制
            broadcast_callback: WebSocket广播回调
        """
        self.project_path = Path(project_path)
        self.max_terminals = max_terminals or MAX_TERMINALS
        self.terminal_buffer_size = terminal_buffer_size or TERMINAL_BUFFER_SIZE
        self.terminal_display_size = terminal_display_size or TERMINAL_DISPLAY_SIZE
        self.default_snapshot_lines = TERMINAL_SNAPSHOT_DEFAULT_LINES
        self.max_snapshot_lines = TERMINAL_SNAPSHOT_MAX_LINES
        self.max_snapshot_chars = TERMINAL_SNAPSHOT_MAX_CHARS
        self.broadcast = broadcast_callback
        self.default_sandbox_mode = (sandbox_mode or TERMINAL_SANDBOX_MODE or "host").lower()
        self.sandbox_mode = self.default_sandbox_mode
        default_sandbox_options = {
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
            # 深拷贝，确保不会影响默认值
            for key, value in sandbox_options.items():
                if key == "binds" and isinstance(value, list):
                    default_sandbox_options[key] = list(value)
                elif key == "env" and isinstance(value, dict):
                    default_sandbox_options[key] = dict(value)
                else:
                    default_sandbox_options[key] = value
        self.sandbox_options = default_sandbox_options
        self.container_session: Optional["ContainerHandle"] = None
        if sandbox_options and sandbox_options.get("container_name"):
            self.sandbox_mode = "docker"
        self._apply_container_session(container_session)
        # 网络权限 getter（由所属 WebTerminal 注入）：新建持久终端时透传，
        # 使 shell 沙箱实时跟随该终端实例的权限设置而非进程级 env。
        self.network_permission_getter = network_permission_getter
        # 终端命令超时工具（兼容 macOS 无 coreutils 的情况）
        self._timeout_bin = shutil.which("timeout") or shutil.which("gtimeout")
        
        # 终端会话字典
        self.terminals: Dict[str, PersistentTerminal] = {}
        
        # 当前活动终端
        self.active_terminal: Optional[str] = None

        # 记录每个终端的连续 timeout 次数，用于给出更合适的提示
        self._terminal_input_timeout_streaks: Dict[str, int] = {}
        
        # 终端工厂（跨平台支持）
        self.factory = TerminalFactory()
        self.host_execution_mode: str = "sandbox"

    def _apply_container_session(self, session: Optional["ContainerHandle"]):
        """根据容器句柄调整执行模式。"""
        self.container_session = session
        if session and session.mode == "docker":
            self.sandbox_mode = "docker"
        elif session:
            self.sandbox_mode = "host"
        else:
            self.sandbox_mode = self.default_sandbox_mode

    def _build_sandbox_options(self) -> Dict:
        """构造当前终端应使用的沙箱参数。"""
        options = dict(self.sandbox_options)
        options["allow_direct_host_execution"] = self.sandbox_mode == "host" and self.host_execution_mode == "direct"
        if self.container_session and self.container_session.mode == "docker":
            options["container_name"] = self.container_session.container_name
            options["mount_path"] = self.container_session.mount_path
        else:
            options.pop("container_name", None)
        return options

    def set_host_execution_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        target = "direct" if normalized == "direct" else "sandbox"
        if self.host_execution_mode == target:
            return
        self.host_execution_mode = target
        if self.sandbox_mode == "host" and self.terminals:
            print(f"{OUTPUT_FORMATS['warning']} 执行环境已切换为 {target}，正在关闭现有终端会话。")
            self.close_all()

    @staticmethod
    def _same_container(a: Optional["ContainerHandle"], b: Optional["ContainerHandle"]) -> bool:
        if a is b:
            return True
        if not a or not b:
            return False
        if a.mode != b.mode:
            return False
        if a.mode == "docker":
            return a.container_id == b.container_id and a.container_name == b.container_name
        return True

    def update_container_session(self, session: Optional["ContainerHandle"]):
        """外部更新容器信息，必要时重置终端。"""
        if self._same_container(self.container_session, session):
            self._apply_container_session(session)
            return
        self._apply_container_session(session)
        if self.terminals:
            print(f"{OUTPUT_FORMATS['warning']} 容器已切换，正在关闭现有终端会话。")
            self.close_all()
    
    def open_terminal(
        self,
        session_name: str,
        working_dir: str = None,
        make_active: bool = True
    ) -> Dict:
        """
        打开新终端会话
        
        Args:
            session_name: 会话名称
            working_dir: 工作目录（相对于项目路径）
            make_active: 是否设为活动终端
            
        Returns:
            操作结果
        """
        # 检查是否已存在
        if session_name in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{session_name}' 已存在",
                "existing_sessions": list(self.terminals.keys())
            }
        
        # 检查数量限制
        if len(self.terminals) >= self.max_terminals:
            return {
                "success": False,
                "error": f"已达到最大终端数量限制 ({self.max_terminals})",
                "existing_sessions": list(self.terminals.keys()),
                "suggestion": "请先关闭一个终端会话"
            }
        
        # 确定工作目录
        if working_dir:
            work_path = self.project_path / working_dir
            if not work_path.exists():
                work_path.mkdir(parents=True, exist_ok=True)
        else:
            work_path = self.project_path
        
        # 获取合适的shell命令（用于宿主机或回退模式）
        shell_command = self.factory.get_shell_command()
        
        # 创建终端实例
        sandbox_options = self._build_sandbox_options()
        terminal = PersistentTerminal(
            session_name=session_name,
            working_dir=str(work_path),
            shell_command=shell_command,
            broadcast_callback=self.broadcast,
            max_buffer_size=self.terminal_buffer_size,
            display_size=self.terminal_display_size,
            project_path=str(self.project_path),
            sandbox_mode=self.sandbox_mode,
            sandbox_options=sandbox_options,
            network_permission_getter=self.network_permission_getter
        )
        
        # 启动终端
        if not terminal.start():
            return {
                "success": False,
                "error": "终端启动失败",
                "session": session_name
            }
        
        # 保存终端实例
        self.terminals[session_name] = terminal
        
        # 设为活动终端
        if make_active:
            self.active_terminal = session_name
        
        print(f"{OUTPUT_FORMATS['success']} 终端会话已打开: {session_name}")
        
        # 广播终端列表更新
        if self.broadcast:
            self.broadcast('terminal_list_update', {
                'terminals': self.get_terminal_list(),
                'active': self.active_terminal
            })
        
        # 对外返回容器视角/相对路径，避免暴露宿主绝对路径
        try:
            if terminal.using_container:
                mount_path = (self.sandbox_options.get("mount_path") or "/workspace").rstrip("/")
                mount_path = mount_path or "/workspace"
                try:
                    rel = work_path.relative_to(self.project_path)
                    if str(rel) == ".":
                        display_work_dir = mount_path
                    else:
                        display_work_dir = f"{mount_path}/{rel.as_posix()}"
                except Exception:
                    display_work_dir = mount_path
            else:
                display_work_dir = "."
                if work_path != self.project_path:
                    display_work_dir = work_path.relative_to(self.project_path).as_posix()
        except Exception:
            display_work_dir = str(work_path)

        return {
            "success": True,
            "session": session_name,
            "working_dir": display_work_dir,
            "shell": terminal.shell_command or shell_command,
            "is_active": make_active,
            "total_sessions": len(self.terminals)
        }
    
    def close_terminal(self, session_name: str) -> Dict:
        """
        关闭终端会话
        
        Args:
            session_name: 会话名称
            
        Returns:
            操作结果
        """
        if session_name not in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{session_name}' 不存在",
                "existing_sessions": list(self.terminals.keys())
            }
        
        # 获取终端实例
        terminal = self.terminals[session_name]
        
        # 关闭终端
        terminal.close()
        
        # 从字典中移除
        del self.terminals[session_name]
        self._terminal_input_timeout_streaks.pop(session_name, None)
        
        # 如果是活动终端，切换到另一个
        if self.active_terminal == session_name:
            if self.terminals:
                self.active_terminal = list(self.terminals.keys())[0]
            else:
                self.active_terminal = None
        
        print(f"{OUTPUT_FORMATS['info']} 终端会话已关闭: {session_name}")
        
        # 广播终端列表更新
        if self.broadcast:
            self.broadcast('terminal_list_update', {
                'terminals': self.get_terminal_list(),
                'active': self.active_terminal
            })
        
        return {
            "success": True,
            "session": session_name,
            "remaining_sessions": list(self.terminals.keys()),
            "new_active": self.active_terminal
        }
    
    def reset_terminal(self, session_name: Optional[str]) -> Dict:
        """
        重置终端会话：关闭并重新创建同名会话
        
        Args:
            session_name: 会话名称
        
        Returns:
            操作结果
        """
        target_session = session_name or self.active_terminal
        if not target_session:
            return {
                "success": False,
                "error": "没有活动终端会话",
                "suggestion": "请先使用 terminal_session 打开一个终端",
                "status": "error",
                "output": "没有活动终端会话"
            }
        
        if target_session not in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{target_session}' 不存在",
                "existing_sessions": list(self.terminals.keys()),
                "status": "error",
                "output": f"终端会话 '{target_session}' 不存在"
            }
        
        terminal = self.terminals[target_session]
        working_dir = str(terminal.working_dir)
        shell_command = self.factory.get_shell_command()
        
        terminal.close()
        del self.terminals[target_session]
        self._terminal_input_timeout_streaks.pop(target_session, None)
        
        sandbox_options = self._build_sandbox_options()
        new_terminal = PersistentTerminal(
            session_name=target_session,
            working_dir=working_dir,
            shell_command=shell_command,
            broadcast_callback=self.broadcast,
            max_buffer_size=self.terminal_buffer_size,
            display_size=self.terminal_display_size,
            project_path=str(self.project_path),
            sandbox_mode=self.sandbox_mode,
            sandbox_options=sandbox_options
        )
        
        if not new_terminal.start():
            if self.terminals:
                self.active_terminal = next(iter(self.terminals.keys()))
            else:
                self.active_terminal = None
            if self.broadcast:
                self.broadcast('terminal_list_update', {
                    'terminals': self.get_terminal_list(),
                    'active': self.active_terminal
                })
            return {
                "success": False,
                "error": f"终端会话 '{target_session}' 重置失败：无法重新启动进程",
                "working_dir": working_dir
            }
        
        self.terminals[target_session] = new_terminal
        self.active_terminal = target_session
        
        if self.broadcast:
            self.broadcast('terminal_reset', {
                'session': target_session,
                'working_dir': working_dir,
                'shell': new_terminal.shell_command or shell_command,
                'time': datetime.now().isoformat()
            })
            self.broadcast('terminal_list_update', {
                'terminals': self.get_terminal_list(),
                'active': self.active_terminal
            })
        
        return {
            "success": True,
            "session": target_session,
            "working_dir": working_dir,
            "shell": new_terminal.shell_command or shell_command,
            "message": "终端会话已重置并重新启动"
        }
    
    def switch_terminal(self, session_name: str) -> Dict:
        """
        切换活动终端
        
        Args:
            session_name: 会话名称
            
        Returns:
            操作结果
        """
        if session_name not in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{session_name}' 不存在",
                "existing_sessions": list(self.terminals.keys())
            }
        
        previous_active = self.active_terminal
        self.active_terminal = session_name
        
        print(f"{OUTPUT_FORMATS['info']} 切换到终端: {session_name}")
        
        # 广播切换事件
        if self.broadcast:
            self.broadcast('terminal_switched', {
                'previous': previous_active,
                'current': session_name
            })
        
        return {
            "success": True,
            "previous": previous_active,
            "current": session_name,
            "status": self.terminals[session_name].get_status()
        }
    
    def list_terminals(self) -> Dict:
        """
        列出所有终端会话
        
        Returns:
            终端列表
        """
        sessions = []
        for name, terminal in self.terminals.items():
            status = terminal.get_status()
            status['is_active'] = (name == self.active_terminal)
            sessions.append(status)
        
        return {
            "success": True,
            "sessions": sessions,
            "active": self.active_terminal,
            "total": len(self.terminals),
            "max_allowed": self.max_terminals
        }
    
    def send_to_terminal(
        self,
        command: str,
        session_name: str = None,
        output_wait: float = None
    ) -> Dict:
        """
        向终端发送命令
        
        Args:
            command: 要执行的命令
            session_name: 目标终端（None则使用活动终端）
            output_wait: 必填，收集/等待输出的最长秒数
            
        Returns:
            执行结果
        """
        # 确定目标终端
        target_session = session_name or self.active_terminal
        
        if not target_session:
            return {
                "success": False,
                "error": "没有活动终端会话",
                "suggestion": "请先使用 terminal_session 打开一个终端",
                "status": "error",
                "output": "没有活动终端会话"
            }

        if target_session not in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{target_session}' 不存在",
                "existing_sessions": list(self.terminals.keys()),
                "status": "error",
                "output": f"终端会话 '{target_session}' 不存在"
            }
        
        # 发送命令
        terminal = self.terminals[target_session]
        if isinstance(output_wait, str):
            try:
                output_wait = float(output_wait)
            except (TypeError, ValueError):
                return {
                    "success": False,
                    "error": "output_wait 参数必须是数字",
                    "status": "error",
                    "output": "output_wait 参数无效"
                }

        if output_wait is None or output_wait <= 0:
            return {
                "success": False,
                "error": "output_wait 参数必填且需大于0",
                "status": "error",
                "output": "output_wait 参数缺失"
            }
        output_wait = min(output_wait, 300)

        result = terminal.send_command(
            command,
            timeout=output_wait,
            timeout_cutoff=output_wait,
            enforce_full_timeout=True,
            sentinel=None,
        )
        result["timeout"] = output_wait
        result["output_wait"] = output_wait
        result["never_timeout"] = False
        self._apply_terminal_input_timeout_hint(target_session, result)
        return result

    def _apply_terminal_input_timeout_hint(self, session_name: str, result: Dict) -> None:
        """在终端输入连续超时时追加提示标记。/ Add hint after consecutive timeouts."""
        status = (result.get("status") or "").lower()
        if status == "timeout":
            streak = self._terminal_input_timeout_streaks.get(session_name, 0) + 1
        else:
            streak = 0
        self._terminal_input_timeout_streaks[session_name] = streak
        if streak == 2:
            result["timeout_hint"] = "suggest_adjust_timeout"

    def _build_wrapped_command(self, command: str, marker: str, timeout: int) -> (str, int):
        """
        构造带超时与完成标记的包装命令。

        - 优先使用 coreutils timeout / gtimeout
        - 若不可用（如 macOS 默认环境），退化为 sleep+kill 方案
        """
        safe_timeout = max(1, int(timeout))
        wait_timeout = safe_timeout + 3

        if self._timeout_bin:
            wrapped_inner = f"{command} ; echo {marker}"
            wrapped_command = f"{self._timeout_bin} -k 2 {safe_timeout}s sh -c {shlex.quote(wrapped_inner)}"
            return wrapped_command, wait_timeout

        # fallback: 使用 sh + sleep/kill 实现超时（适用于缺少 timeout 的环境）
        fallback_inner = (
            f"{command} & CMD_PID=$!; "
            f"(sleep {safe_timeout} && kill -s INT $CMD_PID >/dev/null 2>&1 && sleep 2 && kill -s KILL $CMD_PID >/dev/null 2>&1) & "
            f"WAITER=$!; "
            f"wait $CMD_PID; CMD_STATUS=$?; kill $WAITER >/dev/null 2>&1; "
            f"echo {marker}; exit $CMD_STATUS"
        )
        wrapped_command = f"sh -c {shlex.quote(fallback_inner)}"
        # 额外留 1s 收尾
        return wrapped_command, safe_timeout + 4
    
    def get_terminal_output(
        self,
        session_name: str = None,
        last_n_lines: int = 50
    ) -> Dict:
        """
        获取终端输出
        
        Args:
            session_name: 终端名称（None则使用活动终端）
            last_n_lines: 获取最后N行
            
        Returns:
            输出内容
        """
        target_session = session_name or self.active_terminal
        
        if not target_session:
            return {
                "success": False,
                "error": "没有活动终端会话"
            }
        
        if target_session not in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{target_session}' 不存在"
            }
        
        terminal = self.terminals[target_session]
        requested_lines = last_n_lines
        if requested_lines is None:
            snapshot_lines = self.default_snapshot_lines
        elif requested_lines <= 0:
            snapshot_lines = None
        else:
            snapshot_lines = min(requested_lines, self.max_snapshot_lines)
        
        snapshot_char_limit = self.max_snapshot_chars if snapshot_lines is not None else None
        snapshot = terminal.get_snapshot(
            snapshot_lines,
            snapshot_char_limit
        )
        
        fallback_lines = requested_lines
        if fallback_lines is None:
            fallback_lines = self.default_snapshot_lines
        elif fallback_lines <= 0:
            fallback_lines = 0
        
        output = snapshot.get("output") if snapshot.get("success") else terminal.get_output(fallback_lines)
        
        return {
            "success": True,
            "session": target_session,
            "output": output,
            "is_interactive": snapshot.get("is_interactive", terminal.is_interactive),
            "last_command": snapshot.get("last_command", terminal.last_command),
            "seconds_since_last_output": snapshot.get("seconds_since_last_output", terminal._seconds_since_last_output()),
            "echo_loop_detected": snapshot.get("echo_loop_detected", terminal.echo_loop_detected),
            "lines_returned": snapshot.get("lines_returned"),
            "truncated": snapshot.get("truncated", False),
            "last_event_time": snapshot.get("last_event_time")
        }
    
    def get_terminal_snapshot(
        self,
        session_name: str = None,
        lines: int = None,
        max_chars: int = None
    ) -> Dict:
        """
        获取终端输出快照
        
        Args:
            session_name: 指定会话（默认使用活动会话）
            lines: 返回的最大行数
            max_chars: 返回的最大字符数
        
        Returns:
            包含快照内容和状态的字典
        """
        target_session = session_name or self.active_terminal
        if not target_session:
            return {
                "success": False,
                "error": "没有活动终端会话",
                "suggestion": "请先使用 terminal_session 打开一个终端"
            }
        
        if target_session not in self.terminals:
            return {
                "success": False,
                "error": f"终端会话 '{target_session}' 不存在",
                "existing_sessions": list(self.terminals.keys())
            }
        
        if lines is None:
            line_limit = self.default_snapshot_lines
        elif lines <= 0:
            line_limit = None
        else:
            line_limit = max(1, min(lines, self.max_snapshot_lines))
        char_limit = max(100, min(max_chars if max_chars else self.max_snapshot_chars, self.max_snapshot_chars))
        
        terminal = self.terminals[target_session]
        char_limit = None if line_limit is None else char_limit
        snapshot = terminal.get_snapshot(line_limit, char_limit)
        snapshot.update({
            "line_limit": line_limit,
            "char_limit": char_limit,
            "session": target_session
        })
        
        if snapshot.get("truncated"):
            snapshot["note"] = f"输出已截断，仅返回了末尾的 {char_limit} 个字符"
        
        return snapshot
    
    def get_terminal_list(self) -> List[Dict]:
        """获取终端列表（简化版）"""
        return [
            {
                "name": name,
                "is_active": name == self.active_terminal,
                "is_running": terminal.is_running,
                "working_dir": str(terminal.working_dir)
            }
            for name, terminal in self.terminals.items()
        ]
    
    def close_all(self):
        """关闭所有终端会话"""
        # 无终端时静默返回：避免 __del__/竞态重建路径对空实例输出噪音日志
        if not getattr(self, "terminals", None):
            self.active_terminal = None
            return
        print(f"{OUTPUT_FORMATS['info']} 关闭所有终端会话...")
        
        for session_name in list(self.terminals.keys()):
            self.close_terminal(session_name)
        
        self.active_terminal = None
        print(f"{OUTPUT_FORMATS['success']} 所有终端会话已关闭")
    
    def __del__(self):
        """析构函数，确保所有终端被关闭"""
        self.close_all()

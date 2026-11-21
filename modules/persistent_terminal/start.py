# modules/persistent_terminal/start.py - 持久终端启动（Host-only）
#
# 只保留两条 Host 路径：
# 1. Host 沙箱交互 shell（build_host_sandbox_shell_plan，可只读身份）；
# 2. direct 完全访问下的普通宿主机 shell。
# Docker 容器终端（docker exec、容器只读 uid、容器挂载/存活检查）已拆到
# 独立 Docker fork，这里不再包含任何容器启动分支。

import os
import sys
import time
import threading
import subprocess
from pathlib import Path
from datetime import datetime

from modules.host_sandbox_runner import (
    HostSandboxError,
    build_host_sandbox_shell_plan,
    host_sandbox_enabled,
)
try:
    from config import OUTPUT_FORMATS
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import OUTPUT_FORMATS

from modules.i18n import tr
from modules.execution_scope import bind_execution_scope
from modules.scoped_execution_policy import scoped_work_path


class StartMixin:
    """PersistentTerminal start 能力 mixin。"""

    def start(self) -> bool:
        # Background launch threads have no ContextVar inheritance. Bind the
        # instance's creation-time authority for all sandbox builders.
        with bind_execution_scope(self.execution_scope):
            return self._start_bound()

    def _start_bound(self) -> bool:
        """启动终端进程（Host 沙箱或 direct）"""
        if self.is_running:
            return False

        try:
            if self.execution_scope:
                scoped_work_path(self.project_path, str(self.working_dir), self.execution_scope)
            # 防御性检查：属性可能被旧调用点改写；Docker 模式必须拒绝而非回落 Host。
            if str(getattr(self, "sandbox_mode", "host") or "host").strip().lower() == "docker":
                raise RuntimeError(
                    "PersistentTerminal 是 Host-only 构建，不支持 Docker 容器终端。"
                )
            process = self._start_host_terminal()

            if not process:
                return False

            self.process = process
            self.is_running = True
            self.execution_mode = "host"
            self.start_time = datetime.now()
            self.last_output_time = None
            self.last_input_time = None
            self.last_input_text = ""
            self.echo_loop_detected = False
            self._consecutive_echo_matches = 0

            # 启动输出读取线程
            self.is_reading = True
            self.reader_thread = threading.Thread(target=self._read_output)
            self.reader_thread.daemon = True
            self.reader_thread.start()

            # 宿主机Windows初始化
            if self.is_windows:
                time.sleep(0.5)
                self.send_command("chcp 65001", timeout=1)
                time.sleep(0.5)
                self.send_command("cls", timeout=1)
                time.sleep(0.3)
                self.output_buffer.clear()
                self.total_output_size = 0

            # 广播终端启动事件
            if self.broadcast:
                self.broadcast('terminal_started', {
                    'session': self.session_name,
                    'working_dir': str(self.working_dir),
                    'shell': self.shell_command,
                    'mode': self.execution_mode,
                    'time': self.start_time.isoformat()
                })

            print(f"{OUTPUT_FORMATS['success']} 终端会话启动(宿主机): {self.session_name}")
            return True

        except Exception as e:
            print(f"{OUTPUT_FORMATS['error']} 终端启动失败: {e}")
            self.is_running = False
            return False

    def _start_host_terminal(self):
        with bind_execution_scope(self.execution_scope):
            return self._start_host_terminal_bound()

    def _start_host_terminal_bound(self):
        """启动宿主机终端"""
        direct = self.execution_scope.execution_mode == "direct" if self.execution_scope else self.allow_direct_host_execution
        if direct:
            return self._start_plain_host_terminal()
        if not host_sandbox_enabled():
            raise RuntimeError(tr("terminal_start.host_sandbox_disabled"))
        self.is_windows = sys.platform == "win32"

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['TERM'] = 'xterm-256color'
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'

        try:
            # 实时读取所属终端实例的权限设置，而非进程级 env（多 terminal 并存时 env 会被覆盖）
            network_permission = self._resolve_network_permission()
            # 终端读写身份创建时钉死：受限权限档（只读/批准/自动审核）→ 只读 profile
            # （写入 EPERM 由系统强制）；unrestricted → 可写 profile。
            # 权限跨界切换时所属 terminal 会销毁现有会话，保证身份与当前档位一致。
            readonly = bool(self.sandbox_options.get("host_terminal_readonly"))
            plan = build_host_sandbox_shell_plan(
                self.working_dir, env, network_permission=network_permission,
                readonly=readonly, workspace_root=self.project_path,
            )
            cmd_args = plan.command
            pass_fds = ()
            seccomp_fd = None
            if plan.seccomp_bpf_path:
                seccomp_fd = os.open(plan.seccomp_bpf_path, os.O_RDONLY)
                cmd_args = [str(seccomp_fd) if token == "__SECCOMP_FD__" else token for token in cmd_args]
                pass_fds = (seccomp_fd,)
            try:
                process = subprocess.Popen(
                    cmd_args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=plan.cwd or str(self.working_dir),
                    shell=False,
                    bufsize=0,
                    env=plan.env,
                    pass_fds=pass_fds
                )
            finally:
                if seccomp_fd is not None:
                    try:
                        os.close(seccomp_fd)
                    except OSError:
                        pass
            self.shell_command = "host-sandbox-shell"
            self.is_windows = False
            return process
        except HostSandboxError as exc:
            raise RuntimeError(str(exc))
        except FileNotFoundError:
            print(f"{OUTPUT_FORMATS['error']} 无法找到宿主机沙箱终端运行时")
            return None

    def _start_plain_host_terminal(self):
        self.is_windows = sys.platform == "win32"
        shell_cmd = self.host_shell_command
        if self.is_windows:
            shell_cmd = shell_cmd or "cmd.exe"
        else:
            shell_cmd = shell_cmd or os.environ.get('SHELL', '/bin/bash')
        self.shell_command = shell_cmd
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['TERM'] = 'xterm-256color'
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'
        process = subprocess.Popen(
            shell_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self.working_dir),
            shell=False,
            bufsize=0,
            env=env
        )
        if self.is_windows and not self.host_shell_command:
            # 默认 cmd.exe：注入 chcp 65001，把会话统一切换到 UTF-8 代码页。
            # 1) 与上方 PYTHONIOENCODING=utf-8 对齐（子进程 Python 程序输出 UTF-8）；
            # 2) 让 cmd 内置命令（dir/echo 等）也输出 UTF-8，配合 io.py 的
            #    UTF-8 优先自适应解码，避免中文输出乱码；
            # 3) 仅默认 shell 注入——用户自定义 shell（如 pwsh）默认即为 UTF-8。
            try:
                process.stdin.write(b'chcp 65001 >nul\r\n')
                process.stdin.flush()
            except (OSError, ValueError, BrokenPipeError):
                # 注入失败不致命：io.py 的 GBK 回退仍能处理默认代码页输出
                pass
        return process

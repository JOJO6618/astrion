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
import codecs
from modules.host_sandbox_runner import (
    HostSandboxError,
    build_host_sandbox_shell_plan,
    host_sandbox_enabled,
)
# 注意：本模块不再导入任何 TERMINAL_SANDBOX_* 配置常量——历史上它们是为
# 「docker run 新建临时容器」路径（_start_new_container_terminal /
# toolbox_container）准备的，该路径 2026-09 核实为不可达死代码已删除；
# 现有唯一容器路径是 docker exec 进入 UserContainerManager 拥有的用户容器，
# 所有参数来自 TerminalManager 构建的 sandbox_options。
try:
    from config import (
        OUTPUT_FORMATS,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
    )

from modules.docker_readonly_exec import docker_readonly_exec_args, docker_readonly_wrap_inner
from modules.i18n import tr


class StartMixin:
    """PersistentTerminal start 能力 mixin。"""

    def start(self) -> bool:
        """启动终端进程（支持容器沙箱）"""
        if self.is_running:
            return False

        try:
            process = None
            selected_mode = self.sandbox_mode
            if selected_mode == "docker":
                try:
                    process = self._start_docker_terminal()
                except Exception as exc:
                    message = f"容器终端启动失败: {exc}"
                    print(f"{OUTPUT_FORMATS['error']} {message}")
                    return False
            if process is None:
                process = self._start_host_terminal()
                selected_mode = "host"

            if not process:
                return False

            self.process = process
            self.is_running = True
            self.execution_mode = "docker" if self.using_container else "host"
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
            if self.is_windows and not self.using_container:
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

            mode_label = "容器" if self.using_container else "宿主机"
            print(f"{OUTPUT_FORMATS['success']} 终端会话启动({mode_label}): {self.session_name}")
            return True

        except Exception as e:
            print(f"{OUTPUT_FORMATS['error']} 终端启动失败: {e}")
            self.is_running = False
            return False

    def _start_host_terminal(self):
        """启动宿主机终端"""
        if self.allow_direct_host_execution:
            return self._start_plain_host_terminal()
        if not host_sandbox_enabled():
            raise RuntimeError(tr("terminal_start.host_sandbox_disabled"))
        self.using_container = False
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
                readonly=readonly,
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
        self.using_container = False
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

    def _start_docker_terminal(self):
        """连接容器化终端（docker exec 进入已有用户容器）。"""
        docker_bin = self.sandbox_options.get("bin") or "docker"
        docker_path = shutil.which(docker_bin)
        if not docker_path:
            message = tr("terminal_start.runtime_not_found", runtime=docker_bin)
            if self.sandbox_required:
                raise RuntimeError(message)
            print(f"{OUTPUT_FORMATS['warning']} {message}")
            return None

        target_container = self.sandbox_options.get("container_name")
        if not target_container:
            # 架构不变量（2026-09 核实）：docker 模式下容器会话必然存在——
            # ensure_container() 要么返回有效句柄、要么直接抛异常，TerminalManager
            # 构建 sandbox_options 时必然写入 container_name，本分支理论上不可达。
            # 历史上这里曾 fallback 到「docker run 新建临时容器」
            # （_start_new_container_terminal / modules/toolbox_container.py），
            # 该路径缺 --memory-swap/--pids-limit 等防护参数，且曾在服务器宕机
            # 排查中误导分析方向，已整体删除。此处直接报错，防止未来新入口在
            # 无会话时静默创建无防护容器。
            raise RuntimeError(tr("terminal_start.container_name_missing"))
        return self._start_existing_container_terminal(docker_path, target_container)

    def _start_existing_container_terminal(self, docker_path: str, container_name: str):
        """通过 docker exec 连接到已有容器。"""
        if not self._ensure_container_alive(docker_path, container_name):
            raise RuntimeError(tr("terminal_start.container_not_running", container_name=container_name))

        mount_path = self.sandbox_options.get("mount_path") or "/workspace"
        container_workdir = self._resolve_container_workdir(mount_path)
        shell_path = self.sandbox_options.get("shell") or "/bin/bash"
        cmd = [
            docker_path,
            "exec",
            "-i",
        ]
        readonly_exec = bool(self.sandbox_options.get("docker_readonly_exec"))
        if readonly_exec:
            # 只读身份会话：与 run_command 只读执行同一非特权 uid（内核 DAC 强制）
            cmd += docker_readonly_exec_args()
        if container_workdir:
            cmd += ["-w", container_workdir]

        envs = {
            "PYTHONIOENCODING": "utf-8",
            "TERM": "xterm-256color",
        }
        for key, value in (self.sandbox_options.get("env") or {}).items():
            if value is not None:
                envs[key] = value
        for key, value in envs.items():
            cmd += ["-e", f"{key}={value}"]

        inner_cmd = [shell_path]
        if shell_path.endswith("sh"):
            inner_cmd.append("-i")
        if readonly_exec:
            # Landlock 加固：可用时 shell 及其子进程全程处于工作区只读域；失败自动降级纯 DAC。
            inner_cmd = docker_readonly_wrap_inner(container_name, mount_path, inner_cmd, docker_path)

        cmd.append(container_name)
        cmd.extend(inner_cmd)

        env = os.environ.copy()
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env
        )

        self.sandbox_container_name = container_name
        self.shell_command = f"{shell_path} (attach:{container_name})"
        self.using_container = True
        self.is_windows = False
        return process

    def _resolve_container_workdir(self, mount_path: str) -> str:
        """推导容器内工作目录路径。"""
        mount_path = (mount_path or "/workspace").rstrip("/") or "/workspace"
        try:
            relative = self.working_dir.relative_to(self.project_path)
            if str(relative) == ".":
                return mount_path
            return f"{mount_path}/{relative.as_posix()}"
        except Exception:
            return mount_path

    def _ensure_container_alive(self, docker_path: str, container_name: str) -> bool:
        """确认目标容器正在运行。"""
        try:
            result = subprocess.run(
                [
                    docker_path,
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    container_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

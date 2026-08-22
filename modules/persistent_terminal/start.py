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
            if self.using_container:
                self._stop_sandbox_container(force=True)
            return False

    def _start_host_terminal(self):
        """启动宿主机终端"""
        if self.allow_direct_host_execution:
            return self._start_plain_host_terminal()
        if not host_sandbox_enabled():
            raise RuntimeError("宿主机沙箱已禁用（HOST_SANDBOX_ENABLED=0），拒绝启动 host 终端。")
        self.using_container = False
        self._owns_container = False
        self.is_windows = sys.platform == "win32"

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['TERM'] = 'xterm-256color'
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'

        try:
            # 实时读取所属终端实例的权限设置，而非进程级 env（多 terminal 并存时 env 会被覆盖）
            network_permission = self._resolve_network_permission()
            plan = build_host_sandbox_shell_plan(
                self.working_dir, env, network_permission=network_permission
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
        self._owns_container = False
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
        """启动或连接容器化终端。"""
        docker_bin = self.sandbox_options.get("bin") or "docker"
        docker_path = shutil.which(docker_bin)
        if not docker_path:
            message = f"未找到容器运行时: {docker_bin}"
            if self.sandbox_required:
                raise RuntimeError(message)
            print(f"{OUTPUT_FORMATS['warning']} {message}")
            return None

        self._sandbox_bin_path = docker_path
        target_container = self.sandbox_options.get("container_name")
        if target_container:
            return self._start_existing_container_terminal(docker_path, target_container)
        return self._start_new_container_terminal(docker_path)

    def _start_existing_container_terminal(self, docker_path: str, container_name: str):
        """通过 docker exec 连接到已有容器。"""
        if not self._ensure_container_alive(docker_path, container_name):
            raise RuntimeError(f"目标容器未运行: {container_name}")

        mount_path = self.sandbox_options.get("mount_path") or "/workspace"
        container_workdir = self._resolve_container_workdir(mount_path)
        shell_path = self.sandbox_options.get("shell") or "/bin/bash"
        cmd = [
            docker_path,
            "exec",
            "-i",
        ]
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

        cmd.append(container_name)
        cmd.append(shell_path)
        if shell_path.endswith("sh"):
            cmd.append("-i")

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
        self._owns_container = False
        return process

    def _start_new_container_terminal(self, docker_path: str):
        """启动全新的容器终端。"""
        image = self.sandbox_options.get("image")
        if not image:
            raise RuntimeError("TERMINAL_SANDBOX_IMAGE 未配置")

        mount_path = self.sandbox_options.get("mount_path") or "/workspace"
        working_dir = str(self.working_dir)
        if not self.working_dir.exists():
            self.working_dir.mkdir(parents=True, exist_ok=True)

        container_name = f"{self.sandbox_options.get('name_prefix', 'agent-term')}-{uuid.uuid4().hex[:10]}"
        cmd = [
            docker_path,
            "run",
            "--rm",
            "-i",
        ]
        cmd += [
            "--name",
            container_name,
            "-w",
            mount_path,
            "-v",
            f"{working_dir}:{mount_path}",
        ]

        network = self.sandbox_options.get("network")
        if network:
            cmd += ["--network", network]
        cpus = self.sandbox_options.get("cpus")
        if cpus:
            cmd += ["--cpus", cpus]
        memory = self.sandbox_options.get("memory")
        if memory:
            cmd += ["--memory", memory]

        for bind in self.sandbox_options.get("binds", []):
            bind = bind.strip()
            if bind:
                cmd += ["-v", bind]

        envs = {
            "PYTHONIOENCODING": "utf-8",
            "TERM": "xterm-256color",
        }
        for key, value in (self.sandbox_options.get("env") or {}).items():
            if value is not None:
                envs[key] = value
        for key, value in envs.items():
            cmd += ["-e", f"{key}={value}"]

        cmd.append(image)
        shell_path = self.sandbox_options.get("shell") or "/bin/bash"
        if shell_path:
            cmd.append(shell_path)
            if shell_path.endswith("sh"):
                cmd.append("-i")

        env = os.environ.copy()
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                env=env
            )
        except FileNotFoundError:
            message = f"无法执行容器运行时: {docker_path}"
            if self.sandbox_required:
                raise RuntimeError(message)
            print(f"{OUTPUT_FORMATS['warning']} {message}")
            return None

        self.sandbox_container_name = container_name
        self.shell_command = f"{shell_path} (sandbox:{image})"
        self.using_container = True
        self.is_windows = False
        self._owns_container = True
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

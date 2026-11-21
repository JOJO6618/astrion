# modules/terminal_ops/run.py - 前台命令执行（Host-only）
#
# 执行目标只有两种：Host 沙箱计划（sandbox）或宿主机直连（direct）。
# Docker 容器执行（docker exec、容器只读 uid、容器作用域包装）已随 Docker
# fork 拆出，本文件不再包含任何容器分支。

import os
import sys
import asyncio
import subprocess
import time
import signal
from pathlib import Path
from typing import Dict, Optional

try:
    from config import (
        TERMINAL_COMMAND_TIMEOUT,
        OUTPUT_FORMATS,
        MAX_RUN_COMMAND_CHARS,
    )
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        TERMINAL_COMMAND_TIMEOUT,
        OUTPUT_FORMATS,
        MAX_RUN_COMMAND_CHARS,
    )
from modules.host_sandbox_runner import (
    HostSandboxError,
    build_host_sandbox_plan,
    build_host_sandbox_readonly_plan,
    host_sandbox_enabled,
)
from modules.i18n import tr
from modules.execution_scope import current_execution_scope
from modules.scoped_execution_policy import scoped_work_path, scoped_write_access


class RunMixin:
    """TerminalOperator run 能力 mixin。"""

    @staticmethod
    async def _taskkill_tree(process) -> None:
        """Windows：taskkill /F /T 强制终止整棵进程树，失败退化为 process.kill()。

        避免 shell 子进程被杀后孙进程残留为孤儿。
        """
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=5,
            )
            return
        except Exception:
            pass
        try:
            process.kill()
        except Exception:
            pass

    async def _interrupt_subprocess(self, process) -> None:
        """超时后的中断。

        POSIX 用 killpg(SIGINT) 中断整个进程组。
        Windows 弃用 CTRL_BREAK_EVENT：实测（2026-07 WSL 沙箱排查）即使子进程以
        start_new_session=True（CREATE_NEW_PROCESS_GROUP）启动，控制台事件仍会
        投递到本进程自身，把后端一并杀死（终端仅显示 ^C、无任何报错）。
        Windows 控制台事件没有安全的定向语义，超时场景直接 taskkill 杀整棵树。
        """
        if os.name == "nt":
            await self._taskkill_tree(process)
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

    async def _kill_subprocess(self, process) -> None:
        """强制终止。

        POSIX 用 killpg(SIGKILL)；Windows 没有 SIGKILL，用 taskkill /F /T 终止整棵进程树，
        避免 shell 子进程被杀后孙进程残留为孤儿。
        """
        if os.name == "nt":
            await self._taskkill_tree(process)
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    async def _run_command_subprocess(
        self,
        command: str,
        work_path: Path,
        timeout: int,
        sandbox_write_access: bool = True,
        network_permission: Optional[str] = None,
    ) -> Dict:
        start_ts = time.time()
        try:
            process = None
            stderr_ignore_regexes: list = []
            self._assert_host_execution_target()
            scope = current_execution_scope()
            work_path = scoped_work_path(self.project_path, str(work_path), scope)
            sandbox_write_access = scoped_write_access(sandbox_write_access, scope)

            # 统一环境，确保 Python 输出无缓冲
            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")

            use_host_sandbox = (scope.execution_mode if scope else self.host_execution_mode) != "direct"
            if use_host_sandbox:
                if not host_sandbox_enabled():
                    return {
                        "success": False,
                        "status": "error",
                        "error": tr("terminal.host_sandbox_disabled"),
                        "output": "",
                        "return_code": -1,
                        "timeout": timeout,
                        "elapsed_ms": int((time.time() - start_ts) * 1000)
                    }
                if sandbox_write_access:
                    plan = build_host_sandbox_plan(
                        command, work_path, env, network_permission=network_permission,
                        workspace_root=self.project_path,
                    )
                else:
                    plan = build_host_sandbox_readonly_plan(
                        command, work_path, env, network_permission=network_permission,
                        workspace_root=self.project_path,
                    )
                cmd_args, pass_fds, seccomp_fd = self._materialize_seccomp_fd(
                    plan.command,
                    plan.seccomp_bpf_path,
                )
                stderr_ignore_regexes = list(getattr(plan, "stderr_ignore_regexes", None) or [])
                try:
                    process = await asyncio.create_subprocess_exec(
                        *cmd_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=plan.cwd,
                        env=plan.env,
                        start_new_session=True,
                        pass_fds=pass_fds,
                    )
                finally:
                    if seccomp_fd is not None:
                        try:
                            os.close(seccomp_fd)
                        except OSError:
                            pass
            else:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(work_path),
                    shell=True,
                    env=env,
                    start_new_session=True,
                )

            stdout_buf: list[bytes] = []
            stderr_buf: list[bytes] = []

            async def _read_stream(stream, collector):
                try:
                    async for chunk in stream:
                        if chunk:
                            collector.append(chunk)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

            stdout_task = asyncio.create_task(_read_stream(process.stdout, stdout_buf))
            stderr_task = asyncio.create_task(_read_stream(process.stderr, stderr_buf))

            async def _finish_reader_tasks(force: bool = False) -> None:
                """收口 reader 任务，避免因后台子进程持续占用管道而卡死。"""
                join_timeout = 0.8 if force else 3.0
                try:
                    await asyncio.wait_for(
                        asyncio.gather(stdout_task, stderr_task, return_exceptions=True),
                        timeout=join_timeout
                    )
                except asyncio.TimeoutError:
                    stdout_task.cancel()
                    stderr_task.cancel()
                    try:
                        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                    except Exception:
                        pass
                except Exception:
                    pass

            timed_out = False
            try:
                try:
                    await asyncio.wait_for(process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    timed_out = True
                    await self._interrupt_subprocess(process)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        await self._kill_subprocess(process)
                        await process.wait()
                except asyncio.CancelledError:
                    # 用户主动停止任务或会话断开，立即终止子进程
                    await self._kill_subprocess(process)
                    raise
            finally:
                # 收口 reader 任务（正常/超时/取消路径统一执行），避免泄漏
                # pending 任务及 Windows Proactor 下未关闭的管道传输层
                # （Task was destroyed but it is pending / unclosed transport 告警）
                await _finish_reader_tasks(force=timed_out)

            # 非超时场景下兜底再读一次，防止剩余缓冲未被读取
            if not timed_out:
                try:
                    remaining_out = await asyncio.wait_for(process.stdout.read(), timeout=0.2)
                    if remaining_out:
                        stdout_buf.append(remaining_out)
                except Exception:
                    pass
                try:
                    remaining_err = await asyncio.wait_for(process.stderr.read(), timeout=0.2)
                    if remaining_err:
                        stderr_buf.append(remaining_err)
                except Exception:
                    pass

            stdout = b"".join(stdout_buf)
            stderr = b"".join(stderr_buf)

            stdout_text = stdout.decode('utf-8', errors='replace') if stdout else ""
            stderr_text = stderr.decode('utf-8', errors='replace') if stderr else ""
            if stderr_ignore_regexes and stderr_text:
                stderr_text = self._filter_ignored_stderr_lines(stderr_text, stderr_ignore_regexes)

            success = (process.returncode == 0) and not timed_out
            status = "completed" if success else ("timeout" if timed_out else "error")

            output_parts = []
            if stdout_text:
                output_parts.append(stdout_text)
            if stderr_text:
                output_parts.append(stderr_text)
            combined_output = "\n".join(output_parts)

            truncated = False
            if MAX_RUN_COMMAND_CHARS and len(combined_output) > MAX_RUN_COMMAND_CHARS:
                truncated = True
                combined_output = combined_output[-MAX_RUN_COMMAND_CHARS:]

            response = {
                "success": success,
                "status": status,
                "command": command,
                "output": combined_output,
                "stdout": stdout_text[-MAX_RUN_COMMAND_CHARS:] if MAX_RUN_COMMAND_CHARS else stdout_text,
                "stdout_truncated": bool(MAX_RUN_COMMAND_CHARS and len(stdout_text) > MAX_RUN_COMMAND_CHARS),
                "return_code": process.returncode,
                "truncated": truncated,
                "timeout": timeout,
                "elapsed_ms": int((time.time() - start_ts) * 1000)
            }
            if not success and timed_out:
                response["message"] = tr("terminal.exec_timeout_seconds", timeout=timeout)
            elif not success and process.returncode is not None:
                response["message"] = tr("terminal.exec_failed_code", code=process.returncode)
            if stderr_text:
                response["stderr"] = stderr_text
            return response
        except HostSandboxError as exc:
            return {
                "success": False,
                "status": "error",
                "error": tr("terminal.host_sandbox_unavailable", error=exc),
                "output": "",
                "return_code": -1,
                "timeout": timeout,
                "elapsed_ms": int((time.time() - start_ts) * 1000)
            }
        except Exception as exc:
            return {
                "success": False,
                "status": "error",
                "error": tr("terminal.exec_failed_generic", error=exc),
                "output": "",
                "return_code": -1,
                "timeout": timeout,
                "elapsed_ms": int((time.time() - start_ts) * 1000)
            }

    async def run_command(
        self,
        command: str,
        working_dir: str = None,
        timeout: int = None,
        sandbox_write_access: bool = True,
        network_permission: Optional[str] = None,
    ) -> Dict:
        """
        执行终端命令

        Args:
            command: 要执行的命令
            working_dir: 工作目录
            timeout: 超时时间（秒）

        Returns:
            执行结果字典
        """
        if timeout is None or timeout <= 0:
            return {
                "success": False,
                "error": tr("terminal.timeout_required"),
                "status": "error",
                "output": tr("terminal.timeout_missing"),
                "return_code": -1
            }
        # Host-only：任何外部直接写入的 Docker 会话句柄都必须拒绝，不能回落 Host。
        try:
            self._assert_host_execution_target()
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
                "status": "error",
                "output": "",
                "return_code": -1,
            }
        # 验证命令
        valid, error = self._validate_command(command)
        if not valid:
            return {
                "success": False,
                "error": error,
                "output": "",
                "return_code": -1
            }

        # 设置工作目录
        try:
            work_path = self._resolve_work_path(working_dir)
        except ValueError:
            return {
                "success": False,
                "error": tr("terminal.work_dir_outside_project"),
                "output": "",
                "return_code": -1
            }

        # 默认10s，上限由 TERMINAL_COMMAND_TIMEOUT 控制
        timeout = self._clamp_timeout(timeout, default=10, max_limit=TERMINAL_COMMAND_TIMEOUT)

        print(f"{OUTPUT_FORMATS['terminal']} 执行命令: {command}")
        print(f"{OUTPUT_FORMATS['info']} 工作目录: {work_path}")

        start_ts = time.time()

        try:
            result_payload = await self._run_command_subprocess(
                command,
                work_path,
                timeout,
                sandbox_write_access=sandbox_write_access,
                network_permission=network_permission,
            )
        except asyncio.CancelledError:
            return {
                "success": False,
                "message": tr("terminal.command_cancelled"),
                "output": "",
                "status": "cancelled",
                "return_code": -1,
                "timeout": timeout,
                "elapsed_ms": int((time.time() - start_ts) * 1000)
            }

        # 字符数检查
        if result_payload.get("success") and "output" in result_payload:
            char_count = len(result_payload["output"])
            if char_count > MAX_RUN_COMMAND_CHARS:
                return {
                    "success": False,
                    "error": tr("terminal.output_too_large", char_count=char_count),
                    "char_count": char_count,
                    "limit": MAX_RUN_COMMAND_CHARS,
                    "command": command
                }

        result_payload.setdefault("status", "completed" if result_payload.get("success") else "error")
        result_payload["timeout"] = timeout
        result_payload["elapsed_ms"] = int((time.time() - start_ts) * 1000)
        return result_payload

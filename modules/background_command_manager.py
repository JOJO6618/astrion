"""后台 run_command 任务管理。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import MAX_RUN_COMMAND_CHARS
from modules.host_sandbox_runner import (
    HostSandboxError,
    build_host_sandbox_plan,
    build_host_sandbox_readonly_plan,
    host_sandbox_enabled,
)
from modules.i18n import tr


TERMINAL_STATUSES = {"completed", "failed", "timeout", "cancelled"}


class BackgroundCommandManager:
    """管理 run_command 的后台执行、轮询与等待。"""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self._records: Dict[str, Dict[str, Any]] = {}
        self._processes: Dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)

    def create_background_command(
        self,
        *,
        terminal_ops,
        command: str,
        timeout: Optional[int],
        conversation_id: Optional[str],
        wait_seconds: float = 5.0,
        network_permission: Optional[str] = None,
        sandbox_write_access: bool = True,
    ) -> Dict[str, Any]:
        """启动后台命令；先等待一小段时间返回已有输出。"""
        if timeout is None or float(timeout) <= 0:
            return {
                "success": False,
                "error": tr("terminal.timeout_required"),
                "status": "error",
                "output": tr("terminal.timeout_missing"),
                "return_code": -1,
            }

        timeout_value = min(int(timeout), 3600)
        if timeout_value <= 0:
            timeout_value = 1

        session_override = None
        if not getattr(terminal_ops, "container_session", None):
            session_override = terminal_ops._resolve_active_container_session()

        final_command = command

        valid, error = terminal_ops._validate_command(final_command)
        if not valid:
            return {
                "success": False,
                "error": error,
                "status": "error",
                "output": "",
                "return_code": -1,
            }

        try:
            work_path = terminal_ops._resolve_work_path(None)
        except ValueError:
            return {
                "success": False,
                "error": tr("terminal.work_dir_outside_project"),
                "status": "error",
                "output": "",
                "return_code": -1,
            }

        command_id = f"cmd_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        now = time.time()
        with self._lock:
            self._records[command_id] = {
                "command_id": command_id,
                "conversation_id": conversation_id,
                "status": "running",
                "command": final_command,
                "timeout": timeout_value,
                "created_at": now,
                "updated_at": now,
                "finished_at": None,
                "stdout_chunks": [],
                "stderr_chunks": [],
                "truncated": False,
                "result": None,
                "notified": False,
                "claimed_by_sleep": False,
                "pid": None,
            }

        thread = threading.Thread(
            target=self._run_command_thread,
            kwargs={
                "command_id": command_id,
                "command": final_command,
                "work_path": work_path,
                "timeout": timeout_value,
                "session": session_override or getattr(terminal_ops, "container_session", None),
                "python_env": getattr(terminal_ops, "_python_env", None) or {},
                "host_execution_mode": getattr(terminal_ops, "host_execution_mode", "sandbox"),
                # 网络权限必须按调用方（对话级 terminal）快照传入，不能读进程级
                # 环境变量——env 会被其他 terminal 实例覆盖，导致权限不生效或跨工作区串扰。
                "network_permission": network_permission,
                # 写权限同理：只读模式下后台命令必须使用只读沙箱计划，
                # 否则宿主机只读会被后台路径绕过（此前固定用可写计划）。
                "sandbox_write_access": bool(sandbox_write_access),
            },
            name=f"bg-run-command-{command_id}",
            daemon=True,
        )
        thread.start()

        wait_seconds = max(0.1, min(float(wait_seconds or 5.0), 5.0))
        deadline = time.time() + wait_seconds
        with self._cv:
            while time.time() < deadline:
                rec = self._records.get(command_id)
                if not rec:
                    break
                if rec.get("status") in TERMINAL_STATUSES:
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=remaining)

        with self._lock:
            rec = self._records.get(command_id)
            if not rec:
                return {
                    "success": False,
                    "status": "error",
                    "error": tr("terminal.record_lost"),
                    "output": "",
                    "return_code": -1,
                }

            if rec.get("status") in TERMINAL_STATUSES and isinstance(rec.get("result"), dict):
                rec["claimed_by_sleep"] = True
                rec["notified"] = True
                rec["updated_at"] = time.time()
                result = dict(rec["result"])
                result["command_id"] = command_id
                result["run_in_background"] = True
                result["background_task_created"] = False
                result["message"] = tr("terminal.finished_within_5s")
                return result

            output = self._build_current_output(rec)
            return {
                "success": True,
                "status": "running_background",
                "command_id": command_id,
                "command": final_command,
                "message": tr("terminal.background_created_with_output"),
                "output": output,
                "return_code": None,
                "timeout": timeout_value,
                "elapsed_ms": int((time.time() - now) * 1000),
                "run_in_background": True,
                "background_task_created": True,
            }

    def _run_command_thread(
        self,
        *,
        command_id: str,
        command: str,
        work_path: Path,
        timeout: int,
        session,
        python_env: Dict[str, str],
        host_execution_mode: str = "sandbox",
        network_permission: Optional[str] = None,
        sandbox_write_access: bool = True,
    ) -> None:
        start_ts = time.time()
        process: Optional[subprocess.Popen] = None
        stdout_buf: List[str] = []
        stderr_buf: List[str] = []
        status = "failed"
        return_code = -1
        message: Optional[str] = None

        try:
            exec_cmd: Optional[List[str]] = None
            use_shell = True
            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            if python_env:
                env.update(python_env)

            if session and getattr(session, "mode", None) == "docker":
                container_name = getattr(session, "container_name", None)
                mount_path = getattr(session, "mount_path", "/workspace") or "/workspace"
                docker_bin = shutil.which("docker") or "docker"
                if not container_name:
                    raise RuntimeError(tr("bg_cmd.container_name_missing"))
                try:
                    relative = work_path.relative_to(self.project_path).as_posix()
                except ValueError:
                    relative = ""
                container_workdir = mount_path.rstrip("/")
                if relative:
                    container_workdir = f"{container_workdir}/{relative}"
                exec_cmd = [
                    docker_bin,
                    "exec",
                    "-e",
                    "PATH=/opt/agent-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "-e",
                    "VIRTUAL_ENV=/opt/agent-venv",
                    "-w",
                    container_workdir,
                    container_name,
                    "/bin/bash",
                    "-lc",
                    command,
                ]
                use_shell = False

            if use_shell:
                use_host_sandbox = host_execution_mode != "direct"
                if use_host_sandbox and host_sandbox_enabled():
                    # 优先使用创建时快照的权限；缺省才回落环境变量（兼容旧调用方）。
                    effective_network_permission = (
                        str(network_permission).strip().lower()
                        if network_permission
                        else os.environ.get("HOST_SANDBOX_NETWORK_PERMISSION", "restricted")
                    )
                    if sandbox_write_access:
                        plan = build_host_sandbox_plan(command, work_path, env, network_permission=effective_network_permission)
                    else:
                        plan = build_host_sandbox_readonly_plan(command, work_path, env, network_permission=effective_network_permission)
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
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            cwd=plan.cwd,
                            env=plan.env,
                            start_new_session=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            pass_fds=pass_fds,
                        )
                    finally:
                        if seccomp_fd is not None:
                            try:
                                os.close(seccomp_fd)
                            except OSError:
                                pass
                else:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(work_path),
                        shell=True,
                        env=env,
                        start_new_session=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                    )
            else:
                process = subprocess.Popen(
                    exec_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    start_new_session=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )

            with self._lock:
                rec = self._records.get(command_id)
                if rec is not None:
                    rec["pid"] = process.pid
                    rec["updated_at"] = time.time()
                    self._processes[command_id] = process

            def _reader(stream, collector, rec_key: str):
                try:
                    while True:
                        line = stream.readline()
                        if line == "":
                            break
                        collector.append(line)
                        with self._lock:
                            rec = self._records.get(command_id)
                            if rec is not None:
                                rec[rec_key].append(line)
                                rec["updated_at"] = time.time()
                except Exception:
                    return

            t_out = threading.Thread(target=_reader, args=(process.stdout, stdout_buf, "stdout_chunks"), daemon=True)
            t_err = threading.Thread(target=_reader, args=(process.stderr, stderr_buf, "stderr_chunks"), daemon=True)
            t_out.start()
            t_err.start()

            try:
                process.wait(timeout=timeout)
                return_code = process.returncode if process.returncode is not None else -1
                if return_code == 0:
                    status = "completed"
                else:
                    status = "failed"
                    message = tr("terminal.exec_failed_code", code=return_code)
            except subprocess.TimeoutExpired:
                status = "timeout"
                message = tr("terminal.exec_timeout_seconds", timeout=timeout)
                # 跨平台终止：POSIX killpg(SIGINT→SIGKILL)，Windows taskkill /F /T
                self._terminate_pid(process.pid)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    try:
                        process.wait(timeout=2)
                    except Exception:
                        pass
                return_code = process.returncode if process.returncode is not None else -1

            t_out.join(timeout=1)
            t_err.join(timeout=1)

        except Exception as exc:
            status = "failed"
            message = tr("terminal.exec_failed_generic", error=exc)
        finally:
            combined_output = "".join(stdout_buf + stderr_buf)
            truncated = False
            if MAX_RUN_COMMAND_CHARS and len(combined_output) > MAX_RUN_COMMAND_CHARS:
                combined_output = combined_output[-MAX_RUN_COMMAND_CHARS:]
                truncated = True

            success = status == "completed"
            result = {
                "success": success,
                "status": status,
                "command": command,
                "output": combined_output,
                "return_code": return_code,
                "truncated": truncated,
                "timeout": timeout,
                "elapsed_ms": int((time.time() - start_ts) * 1000),
                "command_id": command_id,
                "run_in_background": True,
            }
            if message:
                result["message"] = message

            with self._cv:
                rec = self._records.get(command_id)
                if rec is not None:
                    existing_status = rec.get("status")
                    existing_result = rec.get("result")
                    if existing_status == "cancelled" and isinstance(existing_result, dict):
                        existing_output = str(existing_result.get("output") or "")
                        if not existing_output and combined_output:
                            existing_result["output"] = combined_output
                        rec["result"] = existing_result
                        rec["updated_at"] = time.time()
                        rec["finished_at"] = rec.get("finished_at") or time.time()
                    else:
                        rec["status"] = status
                        rec["result"] = result
                        rec["truncated"] = truncated
                        rec["updated_at"] = time.time()
                        rec["finished_at"] = time.time()
                self._processes.pop(command_id, None)
                self._cv.notify_all()

    @staticmethod
    def _coerce_pid(value: Any) -> Optional[int]:
        try:
            pid = int(value)
            return pid if pid > 0 else None
        except (TypeError, ValueError):
            return None

    def _is_pid_alive(self, pid: Any) -> bool:
        normalized = self._coerce_pid(pid)
        if not normalized:
            return False
        if os.name == "nt":
            # Windows 上 os.kill(pid, 0) 不是“存活探测”，非 CTRL 事件信号会直接
            # TerminateProcess 杀掉被检查的进程！必须用 OpenProcess 查询。
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(0x1000, False, normalized)
                if not handle:
                    return False
                try:
                    exit_code = ctypes.c_ulong(0)
                    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return False
                    # STILL_ACTIVE = 259
                    return exit_code.value == 259
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                return False
        try:
            os.kill(normalized, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False
        return True

    def _terminate_pid(self, pid: Any) -> bool:
        normalized = self._coerce_pid(pid)
        if not normalized:
            return False
        if os.name == "nt":
            # Windows：无 killpg/SIGKILL。弃用 CTRL_BREAK_EVENT——实测该事件会
            # 投递到本进程自身（2026-07 WSL 沙箱排查，见 terminal_ops/run.py 注释），
            # 直接 taskkill /F /T 强制终止整棵进程树，避免孙进程残留。
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(normalized)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass
            return not self._is_pid_alive(normalized)
        try:
            os.killpg(normalized, signal.SIGINT)
        except Exception:
            try:
                os.kill(normalized, signal.SIGINT)
            except Exception:
                return False
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not self._is_pid_alive(normalized):
                return True
            time.sleep(0.05)
        try:
            os.killpg(normalized, signal.SIGKILL)
        except Exception:
            try:
                os.kill(normalized, signal.SIGKILL)
            except Exception:
                return not self._is_pid_alive(normalized)
        return not self._is_pid_alive(normalized)

    @staticmethod
    def _is_record_timeout_stale(rec: Dict[str, Any]) -> bool:
        try:
            created_at = float(rec.get("created_at") or 0)
            timeout = float(rec.get("timeout") or 0)
        except (TypeError, ValueError):
            return False
        if created_at <= 0 or timeout <= 0:
            return False
        return (time.time() - created_at) > (timeout + 120)

    def reconcile_stale_records(self, conversation_id: Optional[str] = None) -> int:
        """兜底修正后台命令卡死的 running 状态。"""
        changed = 0
        with self._lock:
            for rec in self._records.values():
                if not isinstance(rec, dict):
                    continue
                if conversation_id and rec.get("conversation_id") != conversation_id:
                    continue
                if rec.get("status") != "running":
                    continue
                command_id = rec.get("command_id")
                process = self._processes.get(command_id) if command_id else None
                pid = rec.get("pid")
                stale_timeout = self._is_record_timeout_stale(rec)
                if process and process.poll() is None and not stale_timeout:
                    continue
                if (not process) and self._is_pid_alive(pid) and not stale_timeout:
                    continue
                if stale_timeout and self._is_pid_alive(pid):
                    self._terminate_pid(pid)
                output = self._build_current_output(rec)
                message = (
                    tr("terminal.bg_stale_timeout_cleaned")
                    if stale_timeout
                    else tr("terminal.bg_stale_exited_cleaned")
                )
                rec["status"] = "failed"
                rec["result"] = {
                    "success": False,
                    "status": "failed",
                    "command": rec.get("command"),
                    "output": output,
                    "return_code": rec.get("result", {}).get("return_code")
                    if isinstance(rec.get("result"), dict)
                    else -1,
                    "truncated": bool(rec.get("truncated")),
                    "timeout": rec.get("timeout"),
                    "elapsed_ms": int(max(0.0, (time.time() - float(rec.get("created_at") or time.time())) * 1000)),
                    "command_id": command_id,
                    "run_in_background": True,
                    "message": message,
                }
                rec["updated_at"] = time.time()
                rec["finished_at"] = rec.get("finished_at") or time.time()
                changed += 1
                if command_id:
                    self._processes.pop(command_id, None)
            if changed:
                self._cv.notify_all()
        return changed

    def cancel_command(self, command_id: str) -> Dict[str, Any]:
        if not command_id:
            return {"success": False, "status": "error", "error": tr("terminal.command_id_required")}

        with self._lock:
            rec = self._records.get(command_id)
            if not rec:
                return {"success": False, "status": "error", "error": tr("terminal.background_command_not_found", command_id=command_id)}
            status = str(rec.get("status") or "")
            if status in TERMINAL_STATUSES:
                payload = dict(rec.get("result") or {})
                if payload:
                    return payload
                return {
                    "success": status == "completed",
                    "status": status,
                    "command_id": command_id,
                    "message": tr("terminal.background_command_finished"),
                }
            process = self._processes.get(command_id)
            pid = rec.get("pid")

        stopped = False
        if process and process.poll() is None:
            stopped = self._terminate_pid(process.pid)
        elif self._is_pid_alive(pid):
            stopped = self._terminate_pid(pid)
        else:
            stopped = True

        with self._cv:
            rec = self._records.get(command_id)
            if not rec:
                return {"success": False, "status": "error", "error": tr("terminal.background_command_not_found", command_id=command_id)}

            output = self._build_current_output(rec)
            now = time.time()
            rec["status"] = "cancelled"
            rec["updated_at"] = now
            rec["finished_at"] = now
            rec["notified"] = True
            result = {
                "success": False,
                "status": "cancelled",
                "command": rec.get("command"),
                "output": output,
                "return_code": None,
                "truncated": bool(rec.get("truncated")),
                "timeout": rec.get("timeout"),
                "elapsed_ms": int(max(0.0, (now - float(rec.get("created_at") or now)) * 1000)),
                "command_id": command_id,
                "run_in_background": True,
                "message": tr("terminal.background_cancelled_manual") if stopped else tr("terminal.background_cancel_requested"),
            }
            rec["result"] = result
            self._processes.pop(command_id, None)
            self._cv.notify_all()
            return dict(result)

    def _build_current_output(self, rec: Dict[str, Any]) -> str:
        output = "".join((rec.get("stdout_chunks") or []) + (rec.get("stderr_chunks") or []))
        if MAX_RUN_COMMAND_CHARS and len(output) > MAX_RUN_COMMAND_CHARS:
            return output[-MAX_RUN_COMMAND_CHARS:]
        return output

    def wait_for_completion(self, command_id: str, timeout_seconds: Optional[float] = None, claim: bool = False) -> Dict[str, Any]:
        """阻塞等待后台命令完成。"""
        self.reconcile_stale_records()
        with self._cv:
            rec = self._records.get(command_id)
            if not rec:
                return {"success": False, "error": tr("terminal.background_command_not_found", command_id=command_id), "status": "error"}

            status = rec.get("status")
            if status in TERMINAL_STATUSES and isinstance(rec.get("result"), dict):
                if claim:
                    rec["claimed_by_sleep"] = True
                return dict(rec["result"])

            wait_limit = timeout_seconds
            if wait_limit is None:
                created = float(rec.get("created_at") or time.time())
                timeout_val = float(rec.get("timeout") or 0)
                if timeout_val > 0:
                    wait_limit = max(1.0, (created + timeout_val + 5.0) - time.time())
                else:
                    wait_limit = 3605.0

            deadline = time.time() + max(0.1, float(wait_limit))
            while time.time() < deadline:
                rec = self._records.get(command_id)
                if not rec:
                    return {"success": False, "error": tr("terminal.background_command_not_found", command_id=command_id), "status": "error"}
                status = rec.get("status")
                if status in TERMINAL_STATUSES and isinstance(rec.get("result"), dict):
                    if claim:
                        rec["claimed_by_sleep"] = True
                    return dict(rec["result"])
                self._cv.wait(timeout=min(0.5, deadline - time.time()))

            rec = self._records.get(command_id)
            if not rec:
                return {"success": False, "error": tr("terminal.background_command_not_found", command_id=command_id), "status": "error"}
            return {
                "success": False,
                "status": "timeout",
                "command_id": command_id,
                "message": tr("terminal.wait_bg_timeout"),
                "output": self._build_current_output(rec),
                "return_code": rec.get("result", {}).get("return_code") if isinstance(rec.get("result"), dict) else None,
            }

    def poll_updates(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取未通知且未被 sleep 领取的已完成任务。"""
        updates: List[Dict[str, Any]] = []
        self.reconcile_stale_records(conversation_id=conversation_id)
        with self._lock:
            for rec in self._records.values():
                if conversation_id and rec.get("conversation_id") != conversation_id:
                    continue
                if rec.get("status") not in TERMINAL_STATUSES:
                    continue
                if rec.get("notified") or rec.get("claimed_by_sleep"):
                    continue
                payload = rec.get("result")
                if isinstance(payload, dict):
                    updates.append(dict(payload))

            updates.sort(key=lambda item: self._records.get(item.get("command_id"), {}).get("updated_at", 0))
        return updates

    def mark_notified(self, command_id: str):
        with self._lock:
            rec = self._records.get(command_id)
            if rec:
                rec["notified"] = True
                rec["updated_at"] = time.time()

    def mark_claimed(self, command_id: str):
        with self._lock:
            rec = self._records.get(command_id)
            if rec:
                rec["claimed_by_sleep"] = True
                rec["updated_at"] = time.time()

    def get_record(self, command_id: str) -> Optional[Dict[str, Any]]:
        self.reconcile_stale_records()
        with self._lock:
            rec = self._records.get(command_id)
            if not rec:
                return None
            return dict(rec)

    def get_record_with_output(self, command_id: str) -> Optional[Dict[str, Any]]:
        """获取单条后台命令记录，并附带当前可读输出。"""
        self.reconcile_stale_records()
        with self._lock:
            rec = self._records.get(command_id)
            if not rec:
                return None
            payload = dict(rec)
            payload["output"] = self._build_current_output(rec)
            return payload

    def list_records(
        self,
        *,
        conversation_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """列出后台命令记录（按创建时间倒序）。"""
        self.reconcile_stale_records(conversation_id=conversation_id)
        with self._lock:
            items: List[Dict[str, Any]] = []
            for rec in self._records.values():
                if conversation_id and rec.get("conversation_id") != conversation_id:
                    continue
                item = dict(rec)
                item["output"] = self._build_current_output(rec)
                items.append(item)
            items.sort(key=lambda x: float(x.get("created_at") or 0), reverse=True)
            max_limit = max(1, min(int(limit or 200), 1000))
            return items[:max_limit]

    def has_pending_for_conversation(self, conversation_id: Optional[str]) -> bool:
        if not conversation_id:
            return False
        self.reconcile_stale_records(conversation_id=conversation_id)
        with self._lock:
            for rec in self._records.values():
                if rec.get("conversation_id") != conversation_id:
                    continue
                if rec.get("status") == "running":
                    return True
                if rec.get("status") in TERMINAL_STATUSES and (not rec.get("notified")) and (not rec.get("claimed_by_sleep")):
                    return True
        return False

    def list_waiting_items(self, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if not conversation_id:
            return items
        self.reconcile_stale_records(conversation_id=conversation_id)
        with self._lock:
            for rec in self._records.values():
                if rec.get("conversation_id") != conversation_id:
                    continue
                if rec.get("status") == "running" or (
                    rec.get("status") in TERMINAL_STATUSES
                    and not rec.get("notified")
                    and not rec.get("claimed_by_sleep")
                ):
                    items.append({
                        "command_id": rec.get("command_id"),
                        "command": rec.get("command"),
                        "status": rec.get("status"),
                    })
        items.sort(key=lambda x: x.get("command_id") or "")
        return items

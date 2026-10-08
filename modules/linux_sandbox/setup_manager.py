"""Web and headless status contract, sharing the standalone administrator CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import threading
import time

from .setup import status


class LinuxSandboxSetupManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._progress = self._fresh_progress()
        self._cache = None
        self._cached_at = 0.0

    @staticmethod
    def _fresh_progress():
        return {"active": False, "phase": "idle", "step_index": 0, "step_total": 6, "step_title": "",
                "log_tail": [], "download_bytes": None, "download_total": None, "error": None,
                "error_kind": None, "updated_at": time.time()}

    def _arguments(self):
        script = str(Path(__file__).with_name("setup.py").resolve())
        user = pwd.getpwuid(os.getuid()).pw_name
        return ["/usr/bin/python3", "-I", script, "install", "--user", user, "--install-dependencies"]

    def _elevation(self):
        if os.getuid() == 0:
            return None
        if shutil.which("sudo"):
            try:
                check = subprocess.run(["sudo", "-n", "-l", *self._arguments()],
                                       capture_output=True, timeout=4)
                if check.returncode == 0:
                    return ["sudo", "-n"]
            except (OSError, subprocess.TimeoutExpired):
                pass
        if shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return ["pkexec", "--disable-internal-agent"]
        return None

    def get_sandbox_status(self, force=False):
        with self._lock:
            if not force and self._cache is not None and time.monotonic() - self._cached_at < 5:
                result = dict(self._cache)
                result["setup_running"] = self._progress["active"]
                return result
        from config import TERMINAL_SANDBOX_MODE
        if TERMINAL_SANDBOX_MODE != "host":
            result = {"applicable": False, "platform": "linux", "state": "not_applicable", "detail": ""}
        else:
            result = status()
            result["install_command"] = "" if os.getuid() == 0 else "sudo " + shlex.join(self._arguments())
            result["can_install"] = result["state"] not in {"unsupported", "ordinary_user_required"} and self._elevation() is not None
        with self._lock:
            self._cache, self._cached_at = dict(result), time.monotonic()
            result["setup_running"] = self._progress["active"]
        return result

    def invalidate_status_cache(self):
        with self._lock:
            self._cache = None

    def get_setup_progress(self):
        with self._lock:
            return {**self._progress, "log_tail": list(self._progress["log_tail"])}

    def _update(self, **fields):
        with self._lock:
            self._progress.update(fields)
            self._progress["updated_at"] = time.time()

    def start_setup(self, enable_wsl_if_needed=False):
        current = self.get_sandbox_status(force=True)
        elevation = self._elevation()
        if not current["applicable"] or not current.get("can_install") or elevation is None:
            return {"started": False, "error": "无法取得系统管理员授权，请在服务器终端执行向导中的安装命令。"}
        with self._lock:
            if self._progress["active"]:
                return {"started": False, "error": "安装已经在运行。"}
            self._progress = self._fresh_progress()
            self._progress.update(active=True, phase="installing")
        threading.Thread(target=self._install, args=(elevation,), name="linux-sandbox-setup", daemon=True).start()
        return {"started": True, "error": None}

    def _install(self, elevation):
        try:
            # Shell-free, no password input/storage. The OS authorizer owns any
            # password prompt. A real terminal CLI is the headless install path.
            process = subprocess.Popen([*elevation, *self._arguments()], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1)
            lines = []
            for line in process.stdout:
                lines.append(line.rstrip()[:2048])
                lines = lines[-80:]
                self._update(log_tail=list(lines))
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and "step_index" in event:
                    self._update(**{key: event[key] for key in ("step_index", "step_total", "step_title") if key in event})
            returncode = process.wait(timeout=10)
            process.stdout.close()
            self.invalidate_status_cache()
            checked = self.get_sandbox_status(force=True)
            if returncode or checked["state"] != "ready":
                self._update(active=False, phase="error", error="管理员安装或安装验收失败，详情见安装日志。", error_kind="linux_setup_failed")
            else:
                self._update(active=False, phase="done", step_index=6)
        except (OSError, subprocess.TimeoutExpired) as error:
            self._update(active=False, phase="error", error=str(error), error_kind="linux_setup_failed")
        finally:
            self.invalidate_status_cache()

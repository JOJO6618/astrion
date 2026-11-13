"""Windows Electron 原生有限回归（真实进程 / 真实 ctypes，不是单元桩）。

覆盖两件事：
  1) desktop-electron/.cache/astrion-windows-helper.exe 的 `permission` 与
     `windows EXCLUDED_SHELL_PID` 两个只读命令：只记录 granted、窗口数量与字段名，
     不把任何真实窗口标题写进结果。
  2) 生产脚本 desktop-electron/src/windows/backend-guardian.py：受控假 shell 父进程 +
     假 backend（自带孙进程），验证两条收尸路径——backend 正常退出后整棵树被回收、
     假 shell 被终止后 KILL_ON_JOB_CLOSE 回收整棵树；并从 stdin 一次性管道验证 token
     真的到达 backend（只比对长度与 SHA-256，不落盘、不输出 token）。

合成窗口 PNG 截图不在本脚本范围（由主侧 Electron fixture 覆盖），避免为了截图拖慢
或依赖交互桌面会话。

运行（Windows，系统 Python 即可，无第三方依赖）：
    python test/2026-11-13_WindowsElectron/native_smoke.py
结果写入 output/windows-electron/native-smoke.json；退出码 0=通过 / 1=有失败。

安全边界：
  - 只操作本测试自己创建并持有句柄的进程，绝不按 pid 重开去杀（规避 pid 复用）；
  - 不访问正式 Astrion 进程、不读取真实凭据、不把 token 写入任何文件；
  - Windows 之外 import 本文件是安全的（main 直接记 skipped），不会加载 kernel32。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "desktop-electron" / ".cache" / "astrion-windows-helper.exe"
GUARDIAN = REPO_ROOT / "desktop-electron" / "src" / "windows" / "backend-guardian.py"
OUTPUT = REPO_ROOT / "output" / "windows-electron" / "native-smoke.json"

CREATE_NO_WINDOW = 0x08000000
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if kernel32 is not None:
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def open_process(pid: int, access: int):
    """打开进程句柄；进程已退出时返回 None。句柄由调用方 CloseHandle。"""
    handle = kernel32.OpenProcess(access, False, int(pid))
    return handle if handle else None


def is_alive(handle) -> bool:
    result = kernel32.WaitForSingleObject(handle, 0)
    if result == WAIT_TIMEOUT:
        return True
    if result == WAIT_OBJECT_0:
        return False
    raise OSError(f"WaitForSingleObject failed: {ctypes.get_last_error()}")


def wait_until_dead(handle, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not is_alive(handle):
            return True
        time.sleep(0.05)
    return not is_alive(handle)


def terminate_handle(handle) -> bool:
    if not handle:
        return False
    return bool(kernel32.TerminateProcess(handle, 1))


def run_helper(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(HELPER), *args],
        capture_output=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


# ───────────────────────── helper: permission / windows ─────────────────────────


def helper_smoke(result: dict) -> None:
    entry = {"path": str(HELPER), "exists": HELPER.is_file()}
    result["helper"] = entry
    if not entry["exists"]:
        result["failures"].append("helper_missing")
        return
    entry["size"] = HELPER.stat().st_size

    permission = run_helper(["permission"], timeout=20)
    entry["permission"] = {
        "exit": permission.returncode,
        "granted": None,
        "stderr": permission.stderr.decode("utf-8", "replace")[:400] if permission.returncode else "",
    }
    if permission.returncode == 0:
        try:
            payload = json.loads(permission.stdout.decode("utf-8") or "{}")
        except ValueError:
            entry["permission"]["stderr"] = "invalid_json"
            result["failures"].append("helper_permission_bad_json")
        else:
            entry["permission"]["granted"] = payload.get("granted")
    else:
        result["failures"].append("helper_permission_failed")

    # windows 需要 EXCLUDED_SHELL_PID；传 0 表示不排除任何 pid。结果里只保留数量与
    # 字段名，绝不保存 app/title 的取值（那会是用户真实窗口标题）。
    listing = run_helper(["windows", "0"], timeout=20)
    entry["windows"] = {"exit": listing.returncode, "count": None, "fields": [], "candidates": None}
    if listing.returncode != 0:
        entry["windows"]["stderr"] = listing.stderr.decode("utf-8", "replace")[:400]
        result["failures"].append("helper_windows_failed")
        return
    try:
        rows = json.loads(listing.stdout.decode("utf-8") or "[]")
    except ValueError:
        result["failures"].append("helper_windows_bad_json")
        return
    if not isinstance(rows, list):
        result["failures"].append("helper_windows_not_array")
        return
    entry["windows"]["count"] = len(rows)
    entry["windows"]["fields"] = sorted(rows[0].keys()) if rows else []
    entry["windows"]["candidates"] = sum(1 for row in rows if row.get("candidate") is True)
    for field in ("id", "pid", "width", "height"):
        if rows and field not in rows[0]:
            result["failures"].append(f"helper_windows_missing_{field}")

    entry["capture"] = {"status": "deferred_to_electron_fixture"}
    result["notes"].append("合成窗口 PNG 由主侧 Electron fixture 覆盖；本脚本只验证 permission/windows")


# ─────────────────────── backend-guardian.py 真实进程树回收 ───────────────────────

BACKEND_SOURCE = r'''
import hashlib, json, os, subprocess, sys, time

here = os.path.dirname(os.path.abspath(__file__))
line = sys.stdin.readline()
token = line.rstrip("\n")
with open(os.path.join(here, "token.json"), "w", encoding="utf-8") as handle:
    json.dump({"length": len(token), "sha256": hashlib.sha256(token.encode()).hexdigest()}, handle)
grand = subprocess.Popen([sys.executable, "-u", "-c", "import time; time.sleep(120)"])
with open(os.path.join(here, "backend.pid"), "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
with open(os.path.join(here, "grandchild.pid"), "w", encoding="utf-8") as handle:
    handle.write(str(grand.pid))
open(os.path.join(here, "ready"), "w", encoding="utf-8").close()
while not os.path.exists(os.path.join(here, "stop")):
    time.sleep(0.05)
'''


def wait_for(path: Path, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


class GuardianRun:
    """一次 guardian 运行：假 shell + guardian + 假 backend + 孙进程。"""

    def __init__(self, token: str):
        self.token = token
        self.dir = Path(tempfile.mkdtemp(prefix="astrion-guardian-smoke-"))
        self.shell = None
        self.guardian = None
        self.backend_pid = None
        self.grandchild_pid = None
        self.shell_handle = None
        self.backend_handle = None
        self.grandchild_handle = None

    def start(self) -> bool:
        (self.dir / "backend.py").write_text(BACKEND_SOURCE, encoding="utf-8")
        self.shell = subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(120)"],
            creationflags=CREATE_NO_WINDOW,
        )
        self.shell_handle = open_process(self.shell.pid, PROCESS_TERMINATE | SYNCHRONIZE)
        self.guardian = subprocess.Popen(
            [
                sys.executable, "-u", "-B", str(GUARDIAN), str(self.shell.pid), "--",
                sys.executable, "-u", "-B", str(self.dir / "backend.py"),
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            self.guardian.stdin.write((self.token + "\n").encode())
            self.guardian.stdin.flush()
        except Exception:  # noqa: BLE001 - 管道断开时下面的 ready 等待会失败并记录
            pass
        if not wait_for(self.dir / "ready", 20):
            return False
        self.backend_pid = read_pid(self.dir / "backend.pid")
        self.grandchild_pid = read_pid(self.dir / "grandchild.pid")
        if self.backend_pid:
            self.backend_handle = open_process(self.backend_pid, PROCESS_TERMINATE | SYNCHRONIZE)
        if self.grandchild_pid:
            self.grandchild_handle = open_process(self.grandchild_pid, PROCESS_TERMINATE | SYNCHRONIZE)
        return bool(
            self.backend_pid and self.grandchild_pid
            and self.backend_handle and self.grandchild_handle
        )

    def token_matches(self) -> dict:
        try:
            seen = json.loads((self.dir / "token.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"delivered": False, "match": False, "length": 0}
        expected = hashlib.sha256(self.token.encode()).hexdigest()
        return {
            "delivered": True,
            "match": seen.get("sha256") == expected and seen.get("length") == len(self.token),
            "length": seen.get("length", 0),
        }

    def guardian_exit_within(self, timeout_s: float) -> dict:
        try:
            return {"exit": self.guardian.wait(timeout=timeout_s), "timed_out": False}
        except subprocess.TimeoutExpired:
            return {"exit": None, "timed_out": True}

    def tree_state(self) -> dict:
        def state(handle):
            if handle is None:
                return "no_handle"
            return "alive" if is_alive(handle) else "dead"

        return {"backend": state(self.backend_handle), "grandchild": state(self.grandchild_handle)}

    def close(self) -> list[str]:
        actions = []
        for name, proc in (("guardian", self.guardian), ("shell", self.shell)):
            if proc is not None and proc.poll() is None:
                proc.kill()
                actions.append(f"killed_{name}")
        # 只用自己持有的句柄，绝不按 pid 重开去杀（pid 可能已被系统复用）。
        for name, handle in (("backend", self.backend_handle), ("grandchild", self.grandchild_handle)):
            if handle:
                try:
                    if is_alive(handle) and terminate_handle(handle):
                        actions.append(f"terminated_{name}")
                except OSError:
                    pass
        for proc in (self.guardian, self.shell):
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
        for handle in (self.shell_handle, self.backend_handle, self.grandchild_handle):
            if handle:
                kernel32.CloseHandle(handle)
        if self.guardian is not None:
            for stream in (self.guardian.stdin, self.guardian.stdout, self.guardian.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass
        return actions


def guardian_smoke(result: dict) -> None:
    entry = {"script": str(GUARDIAN), "exists": GUARDIAN.is_file()}
    result["guardian"] = entry
    if not entry["exists"]:
        result["failures"].append("guardian_missing")
        return

    # 路径 A：backend 正常退出（写 stop 文件）→ guardian 的 child.wait() 返回，
    # TerminateJobObject 应连带回收孙进程，guardian 自身也随 Job 结束。
    run = GuardianRun(secrets.token_hex(32))
    try:
        if not run.start():
            entry["backend_exit"] = {"status": "backend_not_started"}
            result["failures"].append("guardian_backend_not_started")
        else:
            entry["token"] = run.token_matches()
            if not entry["token"]["match"]:
                result["failures"].append("guardian_token_mismatch")
            (run.dir / "stop").write_text("1", encoding="utf-8")
            exit_info = run.guardian_exit_within(20)
            backend_dead = wait_until_dead(run.backend_handle, 10)
            grandchild_dead = wait_until_dead(run.grandchild_handle, 10)
            entry["backend_exit"] = {
                "guardian": exit_info,
                "backend_dead": backend_dead,
                "grandchild_dead": grandchild_dead,
                "tree": run.tree_state(),
            }
            if exit_info["timed_out"] or not backend_dead or not grandchild_dead:
                result["failures"].append("guardian_backend_exit_leak")
    except Exception as error:  # noqa: BLE001
        entry["backend_exit"] = {"status": "error", "error": f"{type(error).__name__}: {error}"}
        result["failures"].append("guardian_backend_exit_error")
    finally:
        actions = run.close()
        if actions:
            entry.setdefault("cleanup", []).extend(actions)

    # 路径 B：假 shell 被终止 → guardian 的 parent 句柄 signaled → TerminateJobObject
    # 回收 guardian + backend + 孙进程（KILL_ON_JOB_CLOSE 的实际效果）。
    run2 = GuardianRun(secrets.token_hex(32))
    try:
        if not run2.start():
            entry["shell_killed"] = {"status": "backend_not_started"}
            result["failures"].append("guardian_shell_run_not_started")
        else:
            entry["shell_killed"] = {"token": run2.token_matches()}
            if not entry["shell_killed"]["token"]["match"]:
                result["failures"].append("guardian_shell_token_mismatch")
            killed = terminate_handle(run2.shell_handle)
            entry["shell_killed"]["shell_terminated"] = killed
            exit_info = run2.guardian_exit_within(20)
            backend_dead = wait_until_dead(run2.backend_handle, 10)
            grandchild_dead = wait_until_dead(run2.grandchild_handle, 10)
            entry["shell_killed"].update({
                "guardian": exit_info,
                "backend_dead": backend_dead,
                "grandchild_dead": grandchild_dead,
                "tree": run2.tree_state(),
            })
            if not killed or exit_info["timed_out"] or not backend_dead or not grandchild_dead:
                result["failures"].append("guardian_shell_kill_leak")
    except Exception as error:  # noqa: BLE001
        entry["shell_killed"] = {"status": "error", "error": f"{type(error).__name__}: {error}"}
        result["failures"].append("guardian_shell_kill_error")
    finally:
        actions = run2.close()
        if actions:
            entry.setdefault("cleanup", []).extend(actions)


def main() -> int:
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "repo": str(REPO_ROOT),
        "status": "unknown",
        "failures": [],
        "notes": [],
    }
    exit_code = 0
    if os.name != "nt":
        result["status"] = "skipped_non_windows"
        result["notes"].append("仅在 Windows 上执行；未运行 helper / guardian")
    else:
        try:
            helper_smoke(result)
        except Exception as error:  # noqa: BLE001
            result["failures"].append("helper_smoke_exception")
            result["helper"] = {"error": f"{type(error).__name__}: {error}", "trace": traceback.format_exc(limit=4)}
        try:
            guardian_smoke(result)
        except Exception as error:  # noqa: BLE001
            result["failures"].append("guardian_smoke_exception")
            result["guardian"] = {"error": f"{type(error).__name__}: {error}", "trace": traceback.format_exc(limit=4)}
        result["status"] = "ok" if not result["failures"] else "failed"
        exit_code = 0 if not result["failures"] else 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "failures": result["failures"],
        "notes": result["notes"],
        "output": str(OUTPUT),
    }, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

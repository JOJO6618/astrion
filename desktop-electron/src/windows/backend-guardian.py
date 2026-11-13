"""Windows process-tree guardian, executed by the bundled Python interpreter.

Join a non-inheritable KILL_ON_JOB_CLOSE job before spawning any backend code.
The Electron process is opened first; its death kills this complete job. No
credential is read here: the backend alone consumes the inherited stdin pipe.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from ctypes import wintypes


def run(shell_pid: int, command: list[str]) -> int:
    if os.name != "nt":
        raise RuntimeError("Windows backend guardian requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimits(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                    ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("ReadOperations", "WriteOperations", "OtherOperations",
                     "ReadBytes", "WriteBytes", "OtherBytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("Basic", BasicLimits), ("Io", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    parent = kernel.OpenProcess(0x00100000, False, shell_pid)  # SYNCHRONIZE
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    job = kernel.CreateJobObjectW(None, None)  # non-inheritable; never exposed to backend
    if not job:
        kernel.CloseHandle(parent)
        raise ctypes.WinError(ctypes.get_last_error())
    joined = False
    try:
        limits = ExtendedLimits()
        limits.Basic.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
        joined = True
        if kernel.WaitForSingleObject(parent, 0) != 0x00000102:  # WAIT_TIMEOUT
            return 1

        def watch_shell() -> None:
            # WAIT_FAILED is terminal as well: never leave an unowned backend.
            kernel.WaitForSingleObject(parent, 0xFFFFFFFF)
            kernel.TerminateJobObject(job, 1)

        threading.Thread(target=watch_shell, name="electron-parent", daemon=True).start()
        child = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout,
                                 stderr=sys.stderr, close_fds=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        code = child.wait()
        # Includes this guardian and any grandchildren surviving the backend.
        kernel.TerminateJobObject(job, code & 0xFFFFFFFF)
        return code
    finally:
        kernel.CloseHandle(parent)
        # After joining, closing the last job handle terminates this process too.
        # This is intentional on both startup failure and normal shell shutdown.
        kernel.CloseHandle(job)


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[2] != "--":
        raise ValueError("usage: backend-guardian.py SHELL_PID -- PYTHON [ARGS...]")
    return run(int(sys.argv[1]), sys.argv[3:])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        sys.stderr.write(f"[astrion-guardian] {error}\n")
        sys.exit(1)

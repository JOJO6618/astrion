#!/usr/bin/env python3.11
"""列出本机 Python 3.11 后端进程（pid / 启动时间 / RSS / argv），用于定位孤儿进程。

背景：沙箱禁止执行 `ps`，也不允许向子进程发信号（EPERM）。这里只用只读的
libproc / sysctl 枚举，不修改任何进程状态，不改产品代码。

用法：python3.11 -B find_orphan.py
"""
from __future__ import annotations

import ctypes
import ctypes.util
import struct
import sys

PROC_ALL_PIDS = 1
PROC_PIDTASKINFO = 4
PROC_PIDTBSDINFO = 3
CTL_KERN = 1
KERN_PROCARGS2 = 49


def load_libproc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    lib.proc_listpids.restype = ctypes.c_int
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.proc_pidpath.restype = ctypes.c_int
    return lib


def list_pids(lib: ctypes.CDLL) -> list[int]:
    size = lib.proc_listpids(PROC_ALL_PIDS, 0, None, 0)
    if size <= 0:
        return []
    buf = ctypes.create_string_buffer(size)
    got = lib.proc_listpids(PROC_ALL_PIDS, 0, buf, size)
    count = got // ctypes.sizeof(ctypes.c_int)
    return [ctypes.c_int.from_buffer(buf, i * 4).value for i in range(count)]


def exe_path(lib: ctypes.CDLL, pid: int) -> str:
    buf = ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(pid, buf, ctypes.sizeof(buf)) <= 0:
        return ""
    return buf.value.decode("utf-8", "replace")


def rss_kb(lib: ctypes.CDLL, pid: int) -> int | None:
    buf = ctypes.create_string_buffer(256)
    if not lib.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, buf, ctypes.sizeof(buf)):
        return None
    return int(ctypes.c_uint64.from_buffer(buf, 8).value // 1024)


def start_time(lib: ctypes.CDLL, pid: int) -> float | None:
    # struct proc_bsdinfo: pbi_start_tvsec 偏移 136, pbi_start_tvusec 偏移 144（darwin arm64）
    buf = ctypes.create_string_buffer(256)
    if not lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, buf, ctypes.sizeof(buf)):
        return None
    sec = ctypes.c_uint64.from_buffer(buf, 120).value
    usec = ctypes.c_uint64.from_buffer(buf, 128).value
    return sec + usec / 1e6 if sec else None


def argv(pid: int) -> list[str]:
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libc.dylib", use_errno=True)
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return []
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return []
    raw = buf.raw[: size.value]
    if len(raw) < 4:
        return []
    (argc,) = struct.unpack_from("<i", raw, 0)
    rest = raw[4:]
    nul = rest.find(b"\x00")
    rest = rest[nul + 1 :] if nul >= 0 else rest
    parts = [p.decode("utf-8", "replace") for p in rest.split(b"\x00") if p]
    return parts[: argc]


def main() -> int:
    lib = load_libproc()
    rows = []
    for pid in list_pids(lib):
        if pid <= 0:
            continue
        path = exe_path(lib, pid)
        args = argv(pid)
        joined = " ".join(args)
        # Homebrew 的解释器真实路径是 .../Python.framework/.../Python，不含 "python3.11"
        looks_python = (
            "python3.11" in path
            or "python@3.11" in path
            or "Python.framework" in path
        )
        looks_backend = "server.headless_app" in joined or "py_bootstrap.py" in joined or "server.app" in joined
        if not (looks_python and looks_backend):
            continue
        rows.append(
            {
                "pid": pid,
                "exe": path,
                "args": args,
                "rss_kb": rss_kb(lib, pid),
                "start": start_time(lib, pid),
            }
        )
    if not rows:
        print("没有发现 python3.11 进程")
        return 0
    for row in sorted(rows, key=lambda r: r["start"] or 0):
        print(
            f"pid={row['pid']:>7} start={row['start']} rss={row['rss_kb']} KB\n"
            f"  exe={row['exe']}\n  argv={' '.join(row['args'])[:900]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

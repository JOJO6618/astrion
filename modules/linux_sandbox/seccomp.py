"""Generate an architecture-specific filter during administrator installation."""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
from pathlib import Path

ALLOW = 0x7FFF0000
DENY = 0x00050000 | errno.EPERM
ENOSYS = 0x00050000 | errno.ENOSYS


class Comparison(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


def export_policy(target: Path, network: str = "full") -> dict:
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("libseccomp is required")
    lib = ctypes.CDLL(library, use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint, ctypes.POINTER(Comparison)]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_export_bpf.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(ALLOW)
    if not context:
        raise RuntimeError("seccomp_init failed")
    installed = []
    def add(name, action=DENY, comparison=None):
        number = lib.seccomp_syscall_resolve_name(name.encode())
        if number < 0:
            return
        comparisons = (Comparison * 1)(comparison) if comparison else None
        result = lib.seccomp_rule_add_array(context, action, number, 1 if comparison else 0, comparisons)
        if result < 0:
            raise RuntimeError(f"seccomp rule {name}: {result}")
        installed.append(name)
    try:
        for name in ("mount", "umount2", "pivot_root", "move_mount", "open_tree", "fsopen", "fsconfig",
                     "fsmount", "mount_setattr", "setns", "unshare", "ptrace", "process_vm_readv",
                     "process_vm_writev", "pidfd_getfd", "bpf", "keyctl", "add_key", "request_key",
                     "open_by_handle_at", "init_module", "finit_module", "delete_module", "reboot",
                     "kexec_load", "kexec_file_load", "swapon", "swapoff", "iopl", "ioperm",
                     "userfaultfd", "perf_event_open", "io_uring_setup", "io_uring_enter", "io_uring_register"):
            add(name)
        add("clone3", ENOSYS)
        for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000, 0x10000000,
                     0x20000000, 0x40000000, 0x80):
            add("clone", comparison=Comparison(0, 7, flag, flag))
        for family in (16, 17, 40, 44):
            add("socket", comparison=Comparison(0, 4, family, 0))
            add("socketpair", comparison=Comparison(0, 4, family, 0))
        for operation in (0x5412, 0x541D):  # TIOCSTI / TIOCLINUX terminal injection.
            add("ioctl", comparison=Comparison(1, 4, operation, 0))
        if network == "none":
            add("socket", comparison=Comparison(0, 4, 1, 0))  # AF_UNIX
            # Datagram socketpairs can redirect sendmsg to an external Unix
            # peer. Stream/seqpacket pairs remain available for internal IPC.
            add("socketpair", comparison=Comparison(1, 7, 0xF, 2))
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            if lib.seccomp_export_bpf(context, fd) < 0:
                raise RuntimeError("seccomp_export_bpf failed")
        finally:
            os.close(fd)
        return {"bytes": target.stat().st_size, "rule_count": len(installed)}
    finally:
        lib.seccomp_release(context)

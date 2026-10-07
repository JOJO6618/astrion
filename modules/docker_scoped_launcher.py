"""Mandatory workspace-only launcher, executed inside the toolbox container.

Unlike the legacy readonly launcher this never falls back when enforcement is
unavailable. Landlock constrains data IO; capabilities and seccomp close the
metadata and process escape paths that Landlock does not handle.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import stat
import sys


class Ruleset(ctypes.Structure):
    _fields_ = [("handled", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _fields_ = [("allowed", ctypes.c_uint64), ("fd", ctypes.c_int32), ("pad", ctypes.c_int32)]


def _check(result, label):
    if result < 0:
        raise OSError(ctypes.get_errno(), label)
    return result


def _temporary(root, actor):
    # Create only beneath the pinned workspace; reject substituted ancestors.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    try:
        for name in (".astrion", "sub_agent_runtime", actor, "tmp"):
            try:
                os.mkdir(name, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)
    return os.path.join(root, ".astrion", "sub_agent_runtime", actor, "tmp")


def _drop_capabilities(libc):
    # Clear ambient, bounding, permitted, effective and inheritable sets.
    _check(libc.prctl(47, 4, 0, 0, 0), "clear ambient capabilities")
    for cap in range(64):
        result = libc.prctl(24, cap, 0, 0, 0)
        if result < 0 and ctypes.get_errno() not in {errno.EINVAL, errno.EPERM}:
            _check(result, "drop bounding capability")
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]
    header = Header(0x20080522, 0)
    data = (Data * 2)()
    _check(libc.capset(ctypes.byref(header), ctypes.byref(data)), "clear capabilities")
    _check(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")


def _install_seccomp():
    name = ctypes.util.find_library("seccomp")
    if not name:
        raise RuntimeError("libseccomp is required for workspace-only execution")
    lib = ctypes.CDLL(name, use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7fff0000)  # allow by default
    if not context:
        raise RuntimeError("seccomp initialization failed")
    try:
        denied = (
            "chmod", "fchmod", "fchmodat", "fchmodat2", "chown", "fchown", "lchown", "fchownat",
            "setxattr", "lsetxattr", "fsetxattr", "removexattr", "lremovexattr", "fremovexattr",
            "utime", "utimes", "futimesat", "utimensat", "mount", "umount2", "pivot_root",
            "move_mount", "open_tree", "fsopen", "fsconfig", "fsmount", "mount_setattr",
            "unshare", "setns", "ptrace", "process_vm_readv", "process_vm_writev", "pidfd_getfd",
            "io_uring_setup", "io_uring_enter", "io_uring_register", "bpf",
            "capset", "keyctl", "add_key", "request_key", "open_by_handle_at",
            "name_to_handle_at", "init_module", "finit_module", "delete_module",
            "kill", "tkill", "tgkill", "pidfd_send_signal", "reboot", "swapon", "swapoff",
        )
        for syscall in denied:
            number = lib.seccomp_syscall_resolve_name(syscall.encode())
            if number >= 0 and lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) < 0:
                raise RuntimeError("seccomp rule failed: " + syscall)
        if lib.seccomp_load(context) < 0:
            raise RuntimeError("seccomp installation failed")
    finally:
        lib.seccomp_release(context)


def install(root):
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, None, 0, 1, 0, 0, 0)
    if abi < 3:
        raise RuntimeError("Landlock ABI >=3 is required for workspace-only execution")
    # Read/execute and every write operation through ABI3, including truncate.
    handled = (1 << 15) - 1
    ruleset = Ruleset(handled)
    fd = _check(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0, 0, 0, 0), "create Landlock ruleset")
    read = 1 | (1 << 2) | (1 << 3)
    try:
        for path, access in [(root, handled)] + [(p, read) for p in (
            "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt", "/proc", "/dev", "/sys",
        )]:
            if not os.path.exists(path):
                continue
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                if not stat.S_ISDIR(os.fstat(parent).st_mode):
                    access &= 1 | (1 << 1) | (1 << 2) | (1 << 14)
                rule = PathRule(access, parent, 0)
                _check(libc.syscall(445, fd, 1, ctypes.byref(rule), 0, 0, 0), "add Landlock rule")
            finally:
                os.close(parent)
        # /dev/null is the only writable device outside the workspace.
        parent = os.open("/dev/null", os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathRule((1 << 1) | (1 << 2), parent, 0)
            _check(libc.syscall(445, fd, 1, ctypes.byref(rule), 0, 0, 0), "allow null device")
        finally:
            os.close(parent)
        _drop_capabilities(libc)
        _check(libc.syscall(446, fd, 0, 0, 0, 0, 0), "restrict Landlock domain")
    finally:
        os.close(fd)
    _install_seccomp()


def main():
    root, actor = sys.argv[1:3]
    command = sys.argv[3:]
    if not os.path.isabs(root) or os.path.realpath(root) != root or not command:
        raise ValueError("Invalid fixed workspace or command")
    if len(actor) != 24 or any(c not in "0123456789abcdef" for c in actor):
        raise ValueError("Invalid execution identity")
    temporary = _temporary(root, actor)
    os.environ.update({"HOME": root, "TMPDIR": temporary, "TMP": temporary, "TEMP": temporary})
    install(root)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write("Fixed workspace sandbox unavailable: " + str(exc) + "\n")
        sys.exit(126)

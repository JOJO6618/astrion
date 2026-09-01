#!/usr/bin/env python3
"""Astrion Landlock 只读域 launcher（容器内运行，非后端模块）。

由后端 modules/docker_readonly_exec.py 部署到容器内（docker cp）并调用：

    python3 launcher.py --ro /workspace -- <cmd> [args...]
    python3 launcher.py --selftest --ro /workspace

语义（2026-09 云端实测，kernel 6.8 / Landlock ABI V4 / Docker 28 默认 seccomp 放行）：

- handled 集合只含写类操作（写文件/建删文件目录/创建设备节点/符号链接/
  rename-link 跨越/截断），不授权的路径一律被内核拒绝；
- ro 路径（通常即工作区挂载点）不加任何规则 → 写类操作全拒；
- /tmp、/var/tmp、/dev/shm 显式授予写类权限 → 与纯 DAC 只读的历史行为对齐
  （只读身份 HOME=/tmp，常见临时写入不受影响）；其余路径写权限收紧，这正是
  要修复的 world-writable（777/o+w）绕 DAC 漏洞本身；
- 读/执行不进入 handled 集合，完全交给 DAC（600 权限敏感文件仍不可读）；
- 域随 fork/exec 继承且不可自行解除（配合 no_new_privs），子进程同受限。

注：不能采用「/ 授全量 + ro 路径授空」的交集写法——landlock_add_rule 对
allowed_access=0 的规则返回 ENOMSG（errno 42），内核拒绝空授权规则。

仅依赖 python3 标准库（ctypes 直调 syscall），x86_64/aarch64 通用。
"""
import ctypes
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)

PR_SET_NO_NEW_PRIVS = 38

# x86_64 与 aarch64 编号一致
SYS_CREATE_RULESET = 444
SYS_ADD_RULE = 445
SYS_RESTRICT_SELF = 446

LANDLOCK_RULE_PATH_BENEATH = 1
LANDLOCK_CREATE_RULESET_VERSION = 1 << 0

# access_fs 位（ABI V1 全集 + V2/V3 增量）
FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13      # ABI V2：跨目录 rename/link
FS_TRUNCATE = 1 << 14   # ABI V3：truncate(2)

# 只读域要管的写类操作（读/执行刻意排除，留给 DAC 决定）
WRITE_OPS_V1 = (FS_WRITE_FILE | FS_REMOVE_DIR | FS_REMOVE_FILE |
                FS_MAKE_CHAR | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK |
                FS_MAKE_FIFO | FS_MAKE_BLOCK | FS_MAKE_SYM)


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32),
                ("_pad", ctypes.c_int32)]


def probe_abi() -> int:
    """返回内核 Landlock ABI 版本（>=1），不支持/被拦返回 <1。"""
    ctypes.set_errno(0)
    ret = libc.syscall(SYS_CREATE_RULESET, None, 0,
                       LANDLOCK_CREATE_RULESET_VERSION, 0, 0, 0)
    return ret


def handled_for_abi(abi: int) -> int:
    handled = WRITE_OPS_V1
    if abi >= 2:
        handled |= FS_REFER
    if abi >= 3:
        handled |= FS_TRUNCATE
    return handled


def install_readonly_domain(ro_paths, rw_paths=("/tmp", "/var/tmp", "/dev/shm")):
    """安装只读域。成功返回 None，失败返回错误描述字符串。

    ro_paths：写类操作全拒的路径（不加规则，靠「无覆盖即拒绝」生效）。
    rw_paths：显式授予写类权限的路径（对齐纯 DAC 只读下的常用可写区）。
    """
    abi = probe_abi()
    if abi < 1:
        return f"kernel-unsupported(errno={ctypes.get_errno()})"
    handled = handled_for_abi(abi)

    attr = RulesetAttr(handled)
    ctypes.set_errno(0)
    ruleset_fd = libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr),
                              ctypes.sizeof(attr), 0, 0, 0)
    if ruleset_fd < 0:
        e = ctypes.get_errno()
        return f"create_ruleset(errno={e}:{os.strerror(e)})"

    # 只给存在的 rw 路径加授权规则；ro 路径刻意不加规则（无覆盖 → 拒绝）
    for path in rw_paths:
        if not os.path.exists(path):
            continue
        try:
            fd = os.open(path, os.O_PATH)
        except OSError as e:
            return f"open({path})(errno={e.errno}:{e.strerror})"
        pba = PathBeneathAttr(handled, fd, 0)
        ctypes.set_errno(0)
        ret = libc.syscall(SYS_ADD_RULE, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH,
                           ctypes.byref(pba), 0, 0, 0)
        os.close(fd)
        if ret < 0:
            e = ctypes.get_errno()
            return f"add_rule({path})(errno={e}:{os.strerror(e)})"

    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0:
        return "prctl(PR_SET_NO_NEW_PRIVS) failed"

    ctypes.set_errno(0)
    ret = libc.syscall(SYS_RESTRICT_SELF, ruleset_fd, 0, 0, 0, 0, 0)
    if ret < 0:
        e = ctypes.get_errno()
        return f"restrict_self(errno={e}:{os.strerror(e)})"
    return None


def selftest(ro_paths) -> int:
    """自检：安装域后本进程与子进程写 ro 路径都必须被拒。"""
    err = install_readonly_domain(ro_paths)
    if err:
        print(f"SELFTEST_FAIL install: {err}", file=sys.stderr)
        return 2
    probe = os.path.join(ro_paths[0], ".landlock_selftest_probe")
    try:
        with open(probe, "w") as f:
            f.write("x")
        print("SELFTEST_FAIL write-not-denied", file=sys.stderr)
        return 3  # 残留文件由部署方以容器 root 清理
    except OSError as e:
        if e.errno != 13:  # EACCES
            print(f"SELFTEST_FAIL unexpected errno={e.errno} ({e.strerror})",
                  file=sys.stderr)
            return 4
    import subprocess
    code = f"open({probe!r},'w').write('x')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True)
    if r.returncode == 0:
        print("SELFTEST_FAIL child-not-denied", file=sys.stderr)
        return 5
    # 白名单区（/tmp）必须仍可写，否则说明规则误配，会造成行为回归
    try:
        tmp_probe = f"/tmp/.landlock_selftest_rw.{os.getpid()}"
        with open(tmp_probe, "w") as f:
            f.write("x")
        os.remove(tmp_probe)
    except OSError as e:
        print(f"SELFTEST_FAIL tmp-not-writable: errno={e.errno} ({e.strerror})",
              file=sys.stderr)
        return 6
    print("LANDLOCK_SELFTEST_OK")
    return 0


def main() -> None:
    args = sys.argv[1:]
    ro_paths = []
    selftest_mode = False
    cmd = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--ro" and i + 1 < len(args):
            ro_paths.append(os.path.normpath(args[i + 1]))
            i += 2
        elif a == "--selftest":
            selftest_mode = True
            i += 1
        elif a == "--":
            cmd = args[i + 1:]
            break
        else:
            i += 1
    if not ro_paths:
        print("launcher: missing --ro <path>", file=sys.stderr)
        sys.exit(2)
    if selftest_mode:
        sys.exit(selftest(ro_paths))
    if not cmd:
        print("launcher: missing command after --", file=sys.stderr)
        sys.exit(2)
    err = install_readonly_domain(ro_paths)
    if err:
        print(f"launcher: install readonly domain failed: {err}", file=sys.stderr)
        sys.exit(2)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()

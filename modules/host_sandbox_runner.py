from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from modules.host_sandbox_policy import (
    get_macos_writable_paths,
    get_macos_readable_paths,
    get_macos_deny_read_paths,
    get_macos_deny_read_regexes,
)
from modules.i18n import tr


@dataclass
class SandboxPlan:
    command: List[str]
    env: Dict[str, str]
    cwd: Optional[str] = None
    seccomp_bpf_path: Optional[str] = None
    # 执行器需从 stderr 中过滤的行模式（如 wsl.exe 的 localhost 代理警告）
    stderr_ignore_regexes: List[str] = field(default_factory=list)


class HostSandboxError(RuntimeError):
    pass


# macOS 只读沙箱的系统路径白名单（deny-default + allow-list，Codex 风格）。
# 2026-08-30 起正式启用：只读沙箱 = 默认拒绝全部读取，仅本列表 + 路径授权
# （macos_writable_paths / macos_readable_extra_paths）+ 工作区可读。
# 列表经真机 PoC 校准：/System 含 dyld 共享缓存（进程启动必需）、
# /Library/Developer/CommandLineTools 是 Apple git 等开发工具的真身、
# /opt/homebrew 为 arm64 工具链（Intel 的 /usr/local 已由 /usr 覆盖）、
# /private/var 覆盖 $TMPDIR（/var/folders/...）。
MACOS_MINIMAL_READABLE_PATHS = [
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/System",
    "/Library/Apple",
    "/Library/Developer/CommandLineTools",
    "/Applications",
    "/etc",
    "/private/etc",
    "/dev",
    "/tmp",
    "/private/tmp",
    "/var",
    "/private/var",
    "/opt/homebrew",
]


def _expand_path(raw: str) -> Optional[str]:
    """展开路径中的 ~ 并返回绝对路径；无法展开时返回 None。"""
    if not raw:
        return None
    try:
        expanded = str(Path(raw).expanduser().resolve())
    except Exception:
        return None
    return expanded


def _build_macos_read_rules(paths: List[str]) -> str:
    """把路径列表转成 (allow file-read* (subpath ...)) 规则段。"""
    rules: List[str] = []
    seen: set[str] = set()
    for raw in paths:
        expanded = _expand_path(raw)
        if expanded and expanded not in seen:
            seen.add(expanded)
            rules.append(f'(allow file-read* (subpath "{expanded}"))')
    return "\n".join(rules)


def _build_macos_deny_rules(paths: List[str]) -> str:
    """把路径列表转成 (deny file-read* (subpath ...)) 规则段。"""
    rules: List[str] = []
    seen: set[str] = set()
    for raw in paths:
        expanded = _expand_path(raw)
        if expanded and expanded not in seen:
            seen.add(expanded)
            rules.append(f'(deny file-read* (subpath "{expanded}"))')
    return "\n".join(rules)


def _build_macos_deny_regex_rules(patterns: List[str]) -> str:
    """把正则列表转成 (deny file-read* (regex #"...")) 规则段。"""
    rules: List[str] = []
    for pattern in patterns:
        rules.append(f'(deny file-read* (regex #"{pattern}"))')
    return "\n".join(rules)


def _build_macos_whitelist_read_rules(paths: List[str]) -> str:
    """白名单读规则：每个允许路径的 subpath allow + 其全部祖先目录的 literal allow。

    两个实测要点（2026-08-30 真机 PoC）：
    1. Seatbelt 路径解析需要对每个祖先目录的读权限（file-read*），缺一个祖先
       进程 exec 会直接 Abort trap: 6（file-read-metadata 不够，必须 file-read*）。
       代价：祖先目录的顶层文件名可列出（读文件内容、列子目录仍被拒）。
    2. 符号链接路径必须「原始形式 + 解析形式」双写：/etc→/private/etc 这类
       链接，只写任一种都会 Operation not permitted（链接遍历与目标各查一次）。
    """
    literals: set[str] = set()
    subpaths: List[str] = []
    seen: set[str] = set()

    def _add(path_str: str) -> None:
        if not path_str or path_str in seen:
            return
        seen.add(path_str)
        subpaths.append(path_str)
        for ancestor in Path(path_str).parents:
            ancestor_str = str(ancestor)
            if ancestor_str and ancestor_str != ".":
                literals.add(ancestor_str)

    for raw in paths:
        if not raw:
            continue
        try:
            expanded = str(Path(raw).expanduser())
        except Exception:
            continue
        _add(expanded)
        resolved = _expand_path(raw)
        if resolved:
            _add(resolved)

    rules: List[str] = []
    for literal in sorted(literals):
        rules.append(f'(allow file-read* (literal "{literal}"))')
    for subpath in subpaths:
        rules.append(f'(allow file-read* (subpath "{subpath}"))')
    return "\n".join(rules)


# 宿主机网络权限档位
NETWORK_PERMISSION_RESTRICTED = "restricted"   # macOS: 仅本地回环；Linux/Windows: 暂不隔离
NETWORK_PERMISSION_FULL = "full"               # 完全开放
NETWORK_PERMISSION_NONE = "none"               # 完全禁止网络（后端保留）
_NETWORK_PERMISSION_VALUES = {
    NETWORK_PERMISSION_RESTRICTED,
    NETWORK_PERMISSION_FULL,
    NETWORK_PERMISSION_NONE,
}


def _truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def host_sandbox_enabled() -> bool:
    return _truthy("HOST_SANDBOX_ENABLED", "1")


def _normalize_network_permission(value: Optional[str]) -> str:
    """归一化网络权限值，非法值回退为 restricted。"""
    normalized = str(value or "").strip().lower()
    if normalized in _NETWORK_PERMISSION_VALUES:
        return normalized
    return NETWORK_PERMISSION_RESTRICTED


def _build_macos_network_policy(network_permission: str) -> str:
    """根据网络权限档位生成 macOS sandbox-exec 网络规则片段。"""
    permission = _normalize_network_permission(network_permission)
    if permission == NETWORK_PERMISSION_NONE:
        return ""
    if permission == NETWORK_PERMISSION_FULL:
        return "(allow network-outbound)\n(allow network-inbound)\n"
    # restricted: 仅允许本地回环出站（涵盖 127.0.0.1 / ::1 的实际效果）
    return '(allow network-outbound (remote ip "localhost:*"))\n'


def build_host_sandbox_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    system = platform.system()
    if system == "Darwin":
        return _build_macos_plan(command, work_path, env, network_permission)
    if system == "Linux":
        return _build_linux_plan(command, work_path, env, network_permission)
    if system == "Windows":
        return _build_windows_plan(command, work_path, env, network_permission)
    raise HostSandboxError(tr("sandbox.unsupported_system", system=system))


def build_host_sandbox_readonly_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    system = platform.system()
    if system == "Darwin":
        return _build_macos_readonly_plan(command, work_path, env, network_permission)
    if system == "Linux":
        return _build_linux_readonly_plan(command, work_path, env, network_permission)
    if system == "Windows":
        return _build_windows_readonly_plan(command, work_path, env, network_permission)
    raise HostSandboxError(tr("sandbox.unsupported_system", system=system))


def build_host_sandbox_shell_plan(
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
    readonly: bool = False,
) -> SandboxPlan:
    """持久终端（交互式 shell）沙箱计划。

    readonly=True 时使用只读身份（macOS 只读 profile / Linux bwrap ro-bind /
    Windows WSL 只读挂载）：受限权限档（只读/批准/自动审核）的终端以此创建，
    写入由系统直接拒绝（EPERM）；unrestricted 档传 False 保持可写。
    """
    system = platform.system()
    if system == "Darwin":
        return _build_macos_shell_plan(work_path, env, network_permission, readonly=readonly)
    if system == "Linux":
        return _build_linux_shell_plan(work_path, env, network_permission, readonly=readonly)
    if system == "Windows":
        return _build_windows_shell_plan(work_path, env, network_permission, readonly=readonly)
    raise HostSandboxError(tr("sandbox.unsupported_system", system=system))


def _build_macos_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        raise HostSandboxError(tr("sandbox.macos_no_sandbox_exec"))
    profile = _macos_profile_for_workspace(work_path, network_permission)
    # 白名单读模型下 ~/.gitconfig 不可读会使 git fatal（PoC 实测），指向 /dev/null 跳过
    plan_env = dict(env)
    plan_env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    cmd = [sandbox_exec, "-p", profile, "/bin/bash", "-lc", command]
    return SandboxPlan(command=cmd, env=plan_env, cwd=str(work_path))


def _build_macos_readonly_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        raise HostSandboxError(tr("sandbox.macos_no_sandbox_exec"))
    profile = _macos_readonly_profile_for_workspace(work_path, network_permission)
    # git 在 ~/.gitconfig 不可读时会 fatal（PoC 实测），指向 /dev/null 跳过全局配置
    plan_env = dict(env)
    plan_env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    cmd = [sandbox_exec, "-p", profile, "/bin/bash", "-lc", command]
    return SandboxPlan(command=cmd, env=plan_env, cwd=str(work_path))


def _macos_readonly_profile_for_workspace(
    work_path: Path,
    network_permission: Optional[str] = None,
) -> str:
    """macOS 只读沙箱 profile（deny-default 白名单读模型，2026-08-30 起）。

    默认全部不可读，仅系统路径白名单 + 路径授权（可写+仅可读）+ 工作区可读，
    写权限仅 /dev/null；deny 规则在白名单内做最后排除（如工作区内的 .env）。
    历史模型为「全局可读 + 敏感路径黑名单」，且 deny 顺序在 workspace allow
    之前导致工作区内 .env 实际可读（Seatbelt 后规则覆盖先规则），本次一并修复。
    只读 run_command 与受限档持久终端（shell plan readonly=True）共用本函数。
    """
    network_policy = _build_macos_network_policy(network_permission)
    workspace = str(work_path.resolve())

    readable_paths = list(MACOS_MINIMAL_READABLE_PATHS)
    readable_paths.extend(get_macos_readable_paths())
    readable_paths.append(str(work_path))  # 原始形式（可能含符号链接）
    readable_paths.append(workspace)       # 解析形式
    allow_rules = _build_macos_whitelist_read_rules(readable_paths)
    deny_rules = _build_macos_deny_rules(get_macos_deny_read_paths())
    regex_rules = _build_macos_deny_regex_rules(get_macos_deny_read_regexes())
    if regex_rules:
        deny_rules += "\n" + regex_rules

    return (
        '(version 1)\n'
        '(deny default)\n'
        '(allow sysctl-read)\n'
        '(allow process*)\n'
        f'{network_policy}'
        f'{allow_rules}\n'
        # deny 必须位于所有 allow 之后（后规则覆盖先规则）
        f'{deny_rules}\n'
        '(allow file-write* (literal "/dev/null"))'
    )


def _build_macos_shell_plan(
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
    readonly: bool = False,
) -> SandboxPlan:
    sandbox_exec = shutil.which("sandbox-exec")
    if not sandbox_exec:
        raise HostSandboxError(tr("sandbox.macos_no_sandbox_exec_shell"))
    # 受限档终端以只读身份创建（与只读 run_command 同一 profile），写入 EPERM；
    # unrestricted 保持可写 profile（白名单读 + 工作区/授权路径可写）。
    if readonly:
        profile = _macos_readonly_profile_for_workspace(work_path, network_permission)
    else:
        profile = _macos_profile_for_workspace(work_path, network_permission)
    # 同 _build_macos_plan：白名单读下 git 需要 GIT_CONFIG_GLOBAL 兜底
    plan_env = dict(env)
    plan_env.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    cmd = [sandbox_exec, "-p", profile, "/bin/bash", "-i"]
    return SandboxPlan(command=cmd, env=plan_env, cwd=str(work_path))


def _macos_profile_for_workspace(
    work_path: Path,
    network_permission: Optional[str] = None,
) -> str:
    workspace = str(work_path.resolve())
    writable_paths = [workspace, "/tmp", "/private/tmp", "/dev/null"]
    for raw in get_macos_writable_paths():
        try:
            expanded = str(Path(raw).expanduser().resolve())
        except Exception:
            continue
        if expanded not in writable_paths:
            writable_paths.append(expanded)
    write_rules: list[str] = []
    for entry in writable_paths:
        if entry == "/dev/null":
            write_rules.append('(literal "/dev/null")')
        else:
            write_rules.append(f'(subpath "{entry}")')
    write_expr = " ".join(write_rules)
    network_policy = _build_macos_network_policy(network_permission)
    # 可写沙箱（2026-08-30 起）与只读沙箱共用同一白名单读模型：
    # 默认拒绝全部读取，仅系统路径白名单 + 路径授权（可写+仅可读）+ 工作区可读。
    # 权限模式只管工作区内读写——unrestricted 也不例外；工作区外读取的唯一途径
    # 是「路径授权」。历史模型为「全局可读 + 黑名单」，导致无限制模式/审批批准后
    # 能读授权范围外文件（读放大），本次按方案一修复：审批不放大读取。
    readable_paths = list(MACOS_MINIMAL_READABLE_PATHS)
    readable_paths.extend(get_macos_readable_paths())
    readable_paths.append(str(work_path))  # 原始形式（可能含符号链接）
    readable_paths.append(workspace)       # 解析形式
    allow_rules = _build_macos_whitelist_read_rules(readable_paths)
    deny_rules = _build_macos_deny_rules(get_macos_deny_read_paths())
    regex_rules = _build_macos_deny_regex_rules(get_macos_deny_read_regexes())
    if regex_rules:
        deny_rules += "\n" + regex_rules
    return (
        '(version 1)\n'
        '(deny default)\n'
        '(allow sysctl-read)\n'
        '(allow process*)\n'
        f'{network_policy}'
        f'{allow_rules}\n'
        # deny 必须位于所有 allow 之后（Seatbelt 后规则覆盖先规则），
        # 否则工作区内的 .env 会被 workspace allow 覆盖成可读（旧顺序漏洞）
        f'{deny_rules}\n'
        f'(allow file-write* {write_expr})'
    )


def _build_linux_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise HostSandboxError(tr("sandbox.linux_no_bwrap_exec"))

    seccomp_bpf = os.environ.get("HOST_SANDBOX_LINUX_SECCOMP_BPF", "").strip()
    if not seccomp_bpf:
        raise HostSandboxError(tr("sandbox.linux_no_seccomp_exec"))
    seccomp_path = Path(seccomp_bpf).expanduser().resolve()
    if not seccomp_path.exists():
        raise HostSandboxError(tr("sandbox.seccomp_bpf_not_found", path=seccomp_path))
    shell_cmd = ["/bin/bash", "-lc", command]
    # network_permission 暂不参与 Linux 构建，保持现有 --share-net 行为
    return _build_linux_common_plan(work_path, env, shell_cmd, seccomp_path)


def _build_linux_readonly_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise HostSandboxError(tr("sandbox.linux_no_bwrap_exec"))
    seccomp_bpf = os.environ.get("HOST_SANDBOX_LINUX_SECCOMP_BPF", "").strip()
    if not seccomp_bpf:
        raise HostSandboxError(tr("sandbox.linux_no_seccomp_exec"))
    seccomp_path = Path(seccomp_bpf).expanduser().resolve()
    if not seccomp_path.exists():
        raise HostSandboxError(tr("sandbox.seccomp_bpf_not_found", path=seccomp_path))
    shell_cmd = ["/bin/bash", "-lc", command]
    return _build_linux_common_plan(work_path, env, shell_cmd, seccomp_path, readonly=True)


def _build_linux_shell_plan(
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
    readonly: bool = False,
) -> SandboxPlan:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise HostSandboxError(tr("sandbox.linux_no_bwrap_shell"))
    seccomp_bpf = os.environ.get("HOST_SANDBOX_LINUX_SECCOMP_BPF", "").strip()
    if not seccomp_bpf:
        raise HostSandboxError(tr("sandbox.linux_no_seccomp_shell"))
    seccomp_path = Path(seccomp_bpf).expanduser().resolve()
    if not seccomp_path.exists():
        raise HostSandboxError(tr("sandbox.seccomp_bpf_not_found", path=seccomp_path))
    shell_cmd = ["/bin/bash", "-i"]
    return _build_linux_common_plan(work_path, env, shell_cmd, seccomp_path, readonly=readonly)


def _build_linux_common_plan(
    work_path: Path,
    env: Dict[str, str],
    shell_cmd: List[str],
    seccomp_path: Path,
    readonly: bool = False,
) -> SandboxPlan:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise HostSandboxError(tr("sandbox.linux_no_bwrap_brief"))
    sandbox_root = str(work_path.resolve())
    cmd: List[str] = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--share-net",
        "--ro-bind",
        "/",
        "/",
    ]
    if readonly:
        cmd.extend(["--ro-bind", sandbox_root, sandbox_root])
    else:
        cmd.extend(["--bind", sandbox_root, sandbox_root])
    cmd.extend([
        "--chdir",
        sandbox_root,
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--seccomp",
        "__SECCOMP_FD__",
        *shell_cmd,
    ])
    return SandboxPlan(command=cmd, env=env, cwd=sandbox_root, seccomp_bpf_path=str(seccomp_path))


# ──────────────────────────────────────────────────────────────
# Windows：WSL2 + bubblewrap 沙箱
#
# 设计要点（依据 .wsl-poc 与 .wsl-exp 两轮实验，见 wsl2-sandbox-poc-report.md
# 与项目记忆 wsl_sandbox_minimal_root）：
# - 使用专用沙箱发行版（默认 astrion-sandbox），必须关闭 interop，
#   否则沙箱内可经 cmd.exe 逃逸到 Windows 宿主机；
# - 最小根文件系统：只挂载发行版的 Linux 系统目录（/bin /sbin /usr /lib /etc，
#   纯工具链、无用户数据）+ 工作区 bind；不挂载 / 本身与任何 /mnt/<盘>，
#   工作区外的数据在命名空间内根本不存在（报 No such file or directory）。
#   bwrap 是挂载命名空间构造器而非访问过滤器，因此可以实现 macOS Seatbelt
#   做不到的“指令正常运行 + 默认拒绝的完美读限制”；
# - bwrap 为挂载点自动创建的中间父目录是会话内可写 tmpfs（不落盘、无安全
#   问题但缺报错语义），启动后先 mount -o remount,ro / 恢复只读报错；
# - 网络档位：full → --share-net；restricted（仅回环）/ none → 不 share-net
#   （unshare-net 下 lo 自动可用，语义对齐 macOS 的 restricted）；
# - 敏感路径掩蔽清单（windows_deny_read_paths）不再需要：数据根本不进命名
#   空间；该清单仍由 server/chat/permission.py 用于原生读工具的禁读判断；
# - wsl.exe 的 localhost 代理警告经 stderr_ignore_regexes 由执行器过滤。
# ──────────────────────────────────────────────────────────────

WSL_DEFAULT_SANDBOX_DISTRO = "astrion-sandbox"
_WSL_STDERR_IGNORE = [r"localhost 代理", r"localhost proxy"]

# 模块级探测缓存：发行版名 -> 是否已验证可用
_wsl_distro_verified: Dict[str, bool] = {}


def _wsl_distro_name() -> str:
    return (os.environ.get("HOST_SANDBOX_WSL_DISTRO", "") or "").strip() or WSL_DEFAULT_SANDBOX_DISTRO


def _win_path_to_wsl(path) -> str:
    """Windows 路径转 WSL 路径：``E:\\a\\b`` → ``/mnt/e/a/b``。"""
    raw = str(path)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    if not m:
        raise HostSandboxError(tr("sandbox.wsl_path_convert_failed", path=raw))
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/").rstrip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def _ensure_wsl_sandbox_distro() -> str:
    """探测专用沙箱发行版与 bwrap 是否可用，结果按发行版名缓存。"""
    name = _wsl_distro_name()
    if _wsl_distro_verified.get(name):
        return name
    wsl = shutil.which("wsl.exe")
    if not wsl:
        raise HostSandboxError(tr("sandbox.windows_no_wsl"))
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"
    setup_hint = tr("sandbox.wsl_setup_hint", distro=name)
    try:
        probe = subprocess.run(
            [wsl, "-d", name, "-e", "true"],
            capture_output=True, timeout=30, env=env,
        )
    except Exception as exc:
        raise HostSandboxError(tr("sandbox.wsl_distro_probe_failed", error=exc, hint=setup_hint))
    if probe.returncode != 0:
        raise HostSandboxError(setup_hint)
    try:
        probe_bwrap = subprocess.run(
            [wsl, "-d", name, "-e", "bwrap", "--version"],
            capture_output=True, timeout=30, env=env,
        )
    except Exception as exc:
        raise HostSandboxError(tr("sandbox.wsl_bwrap_probe_failed", error=exc, hint=setup_hint))
    if probe_bwrap.returncode != 0:
        raise HostSandboxError(tr("sandbox.wsl_distro_no_bwrap", distro=name))
    _wsl_distro_verified[name] = True
    return name


def _build_windows_bwrap_argv(
    ws_wsl: str,
    shell_cmd: List[str],
    readonly: bool,
    network_permission: Optional[str],
) -> List[str]:
    permission = _normalize_network_permission(network_permission)
    argv: List[str] = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
    ]
    # full → 共享网络；restricted（仅回环）与 none → unshare-net（lo 仍可用）
    if permission == NETWORK_PERMISSION_FULL:
        argv.append("--share-net")
    # 最小根文件系统：只挂沙箱发行版的 Linux 系统目录（纯工具链、无用户数据）。
    # 不挂载 / 本身与任何 /mnt/<盘>——工作区外的数据在命名空间内不存在。
    # 注意：若日后改用 glibc 发行版（如 Ubuntu），需补 --ro-bind /lib64 /lib64。
    for sysdir in ("/bin", "/sbin", "/usr", "/lib", "/etc"):
        argv += ["--ro-bind", sysdir, sysdir]
    argv += (["--ro-bind"] if readonly else ["--bind"]) + [ws_wsl, ws_wsl]
    argv += [
        "--chdir", ws_wsl,
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
        "--dir", "/root",
        "--",
        # bwrap 为挂载点自动创建的中间父目录（如 /mnt/e）是会话内可写 tmpfs
        # （写入不落盘、退出即消失，无安全问题），但写入不报错、与 mac 的审批
        # 关键词语义不一致；启动后先把根 remount 为只读，再 exec 真正的命令。
        # $0="bwrap-sh" 仅作占位，$@ 从 shell_cmd 开始，exec "$@" 按 argv 原样
        # 透传，避免对用户命令做字符串拼接（切勿加 shift，否则会丢掉 argv[0]）。
        "bash", "-c", 'mount -o remount,ro / 2>/dev/null; exec "$@"', "bwrap-sh",
        *shell_cmd,
    ]
    return argv


def _build_windows_wsl_plan(
    work_path: Path,
    env: Dict[str, str],
    shell_cmd: List[str],
    readonly: bool,
    network_permission: Optional[str],
) -> SandboxPlan:
    wsl = shutil.which("wsl.exe")
    if not wsl:
        raise HostSandboxError(tr("sandbox.windows_no_wsl"))
    distro = _ensure_wsl_sandbox_distro()
    ws_wsl = _win_path_to_wsl(work_path.resolve())
    argv = _build_windows_bwrap_argv(ws_wsl, shell_cmd, readonly, network_permission)
    plan_env = dict(env or {})
    plan_env["WSL_UTF8"] = "1"
    # 必须用 -e（exec，不经默认 shell）而非 --：-- 形式会把尾部交给 /bin/sh 重新解析，
    # 带空格/引号的参数会被拆散（bash -c 后的位置参数全部丢失），-e 原样传递 argv
    # （.wsl-exp/test_argprobe2.py 实测：P4/P6(--) 参数丢失，P5/P8(-e) 完整）。
    return SandboxPlan(
        command=[wsl, "-d", distro, "-e", *argv],
        env=plan_env,
        cwd=str(work_path),
        stderr_ignore_regexes=list(_WSL_STDERR_IGNORE),
    )


def _build_windows_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    return _build_windows_wsl_plan(
        work_path, env, ["bash", "-lc", command], readonly=False,
        network_permission=network_permission,
    )


def _build_windows_readonly_plan(
    command: str,
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
) -> SandboxPlan:
    return _build_windows_wsl_plan(
        work_path, env, ["bash", "-lc", command], readonly=True,
        network_permission=network_permission,
    )


def _build_windows_shell_plan(
    work_path: Path,
    env: Dict[str, str],
    network_permission: Optional[str] = None,
    readonly: bool = False,
) -> SandboxPlan:
    return _build_windows_wsl_plan(
        work_path, env, ["bash", "-i"], readonly=readonly,
        network_permission=network_permission,
    )

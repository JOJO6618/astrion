"""Build only fixed bwrap options; user argv runs after the C identity drop."""
from __future__ import annotations

import os
from pathlib import PurePosixPath, Path
import stat

try:
    from .constants import RUNTIME_ROOT, SYSTEM_DIRECTORIES
except ImportError:
    from constants import RUNTIME_ROOT, SYSTEM_DIRECTORIES


def build_command(config: dict, request: dict, principal: dict, sources: list[dict], lease: str,
                  policy_fd: int) -> list[str]:
    command = [config["bwrap"], "--die-with-parent", "--new-session", "--unshare-pid",
               "--unshare-ipc", "--unshare-uts", "--unshare-cgroup", "--hostname", "astrion-sandbox"]
    # Network namespace stays shared: both directions of host localhost work.
    command += ["--proc", "/proc", "--dev", "/dev", "--perms", "1777", "--tmpfs", "/tmp",
                "--perms", "1777", "--tmpfs", "/var/tmp"]
    entries = sorted(sources[:-1], key=lambda item: len(PurePosixPath(item["target"]).parts))
    for item in entries:
        command += ["--bind-fd" if item["write"] else "--ro-bind-fd", str(item["fd"]), item["target"]]
    # Merged-/usr aliases are administrator-owned, not requester-provided paths.
    for raw in SYSTEM_DIRECTORIES:
        path = Path(raw)
        if path.is_symlink() and str(path.resolve()).startswith("/usr/"):
            command += ["--symlink", os.readlink(raw), raw]
    mounted = {PurePosixPath(item["target"]) for item in entries}
    parents = {parent for mount in mounted for parent in mount.parents
               if str(parent) not in {"/", "/tmp", "/var/tmp"}}
    virtual = [parent for parent in parents if not any(parent == mount or mount in parent.parents
                                                       for mount in mounted)]
    for parent in sorted(virtual, key=lambda path: len(path.parts)):
        command += ["--chmod", "0755", str(parent)]
    runtime = RUNTIME_ROOT
    command += ["--perms", "0755", "--tmpfs", runtime, "--perms", "0700", "--tmpfs", runtime + "/home",
                "--ro-bind", config["install_root"] + "/uid-exec", runtime + "/uid-exec",
                "--ro-bind", lease, runtime + "/lease", "--remount-ro", runtime,
                "--remount-ro", "/", "--cap-drop", "ALL"]
    for capability in ("CAP_SETUID", "CAP_SETGID", "CAP_SETPCAP", "CAP_CHOWN"):
        command += ["--cap-add", capability]
    # bwrap's environment applies only to bwrap/C, never root Python imports.
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", **request["env"],
                   "HOME": runtime + "/home", "TMPDIR": "/tmp", "GIT_CONFIG_GLOBAL": "/dev/null"}
    command += ["--clearenv"]
    for name, value in environment.items():
        command += ["--setenv", name, value]
    cwd = sources[-1]
    information = os.fstat(cwd["fd"])
    if not stat.S_ISDIR(information.st_mode):
        raise PermissionError("Invalid fixed working directory")
    # C reopens no-follow inside the namespace and compares the pinned inode.
    command += ["--seccomp", str(policy_fd), runtime + "/uid-exec",
                str(principal["uid"]), str(principal["gid"]), str(request["umask"]),
                ",".join(str(group) for group in principal["groups"]), request["cwd"],
                str(information.st_dev), str(information.st_ino), *request["argv"]]
    return command

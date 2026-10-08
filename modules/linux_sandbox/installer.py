"""Root-only installation with immutable staging and bounded service operations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

try:
    from .constants import INSTALL_ROOT
    from .helper_config import defaults, trusted_path
    from .profiles import profile_text
    from .seccomp import export_policy
except ImportError:
    from constants import INSTALL_ROOT
    from helper_config import defaults, trusted_path
    from profiles import profile_text
    from seccomp import export_policy

CONFIG = Path("/etc/astrion-sandbox/helper.json")
PROFILE = Path("/etc/apparmor.d/astrion-linux-sandbox")
SERVICE = Path("/etc/systemd/system/astrion-sandbox.service")


def run(argv, timeout=40):
    return subprocess.run(argv, check=True, timeout=timeout, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})


def step(index, title):
    print(json.dumps({"step_index": index, "step_total": 6, "step_title": title}), flush=True)


def atomic_write(path: Path, text: str, mode: int) -> None:
    trusted_path(str(path.parent), directory=True)
    if path.exists() or path.is_symlink():
        trusted_path(str(path))
    temporary = path.with_name(path.name + ".new-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def service_text(config: dict) -> str:
    return f'''[Unit]
Description=Astrion restricted Linux sandbox helper
After=apparmor.service
Requires=apparmor.service

[Service]
Type=simple
ExecStart={config['python']} -I {INSTALL_ROOT}/server.py --config={CONFIG}
Restart=on-failure
RestartSec=2
KillMode=mixed
TimeoutStopSec=20
UMask=0077
RuntimeDirectory=astrion-sandbox
RuntimeDirectoryMode=0755
ExecStartPre=/usr/bin/mkdir -p /run/astrion-sandbox/jobs

[Install]
WantedBy=multi-user.target
'''


def require_installation_runtime() -> None:
    if not Path("/run/systemd/system").is_dir() or not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise RuntimeError("A booted systemd system with cgroup v2 is required")
    if not Path("/sys/kernel/security/apparmor/profiles").is_file():
        raise RuntimeError("An enabled AppArmor kernel is required; do not disable it to bypass this check")


def install_helper(account, install_dependencies=False):
    if __package__:
        from .setup import dependencies
    else:
        from setup import dependencies
    if os.geteuid() != 0:
        raise PermissionError("Administrator installation required")
    step(1, "检查系统与管理员安装条件")
    require_installation_runtime()
    step(2, "检查并安装依赖")
    if install_dependencies:
        # The kernel-specific tools package contains a working bpftool on
        # Ubuntu; the /usr/sbin wrapper alone is insufficient.
        packages = ["bubblewrap", "apparmor-utils", "libseccomp2", "gcc", "python3",
                    "linux-tools-common", "linux-tools-" + os.uname().release]
        run(["apt-get", "update"], timeout=600)
        run(["apt-get", "install", "-y", *packages], timeout=1200)
    executables, missing = dependencies()
    if missing:
        raise RuntimeError("Missing dependencies: " + ", ".join(missing) + "; retry with --install-dependencies")
    for path in executables.values():
        trusted_path(path)
    bwrap_help = subprocess.run([executables["bwrap"], "--help"], capture_output=True, text=True, check=True, timeout=5).stdout
    if "--bind-fd" not in bwrap_help or "--ro-bind-fd" not in bwrap_help:
        raise RuntimeError("bubblewrap with FD-based bind mounts is required")
    parent = Path(INSTALL_ROOT).parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    trusted_path(str(parent), directory=True)
    CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".astrion-sandbox-stage-", dir=parent))
    old = Path(INSTALL_ROOT + ".backup-" + uuid.uuid4().hex)
    previous = {}
    for file in (CONFIG, PROFILE, SERVICE):
        if file.exists():
            trusted_path(str(file))
            previous[file] = (file.read_text(), file.stat().st_mode & 0o777)
    target = Path(INSTALL_ROOT)
    swapped = False
    had_previous = False
    old_enabled = subprocess.run([executables["systemctl"], "is-enabled", "astrion-sandbox.service"],
                                 capture_output=True, timeout=10).returncode == 0
    old_active = subprocess.run([executables["systemctl"], "is-active", "astrion-sandbox.service"],
                                capture_output=True, timeout=10).returncode == 0
    try:
        step(3, "编译并固定特权助手")
        source = Path(__file__).resolve().parent
        names = ("constants.py", "helper_config.py", "principal.py", "schema.py", "transport.py", "sources.py",
                 "mounts.py", "runner.py", "supervisor.py", "server.py", "client.py", "uid_exec.c")
        for name in names:
            # These are administrator-approved source inputs. Runtime imports
            # never point back to this potentially user-writable checkout.
            shutil.copyfile(source / name, stage / name)
            (stage / name).chmod(0o644)
        run(["cc", "-Wall", "-Wextra", "-Werror", "-O2", str(stage / "uid_exec.c"), "-o", str(stage / "uid-exec")])
        (stage / "uid-exec").chmod(0o755)
        export_policy(stage / "policy.bpf")
        export_policy(stage / "policy-none.bpf", network="none")
        stage.chmod(0o755)
        config = defaults(account.pw_uid, executables)
        if CONFIG in previous:
            previous_config = json.loads(previous[CONFIG][0])
            config["allowed_uids"] = sorted(set(previous_config.get("allowed_uids", [])) | {account.pw_uid})
        step(4, "安装强制策略与助手服务")
        # Stop only our own service, whose workers revoke their own leases.
        subprocess.run([executables["systemctl"], "stop", "astrion-sandbox.service"], capture_output=True, timeout=25)
        if target.exists():
            trusted_path(str(target), directory=True)
            target.rename(old)
            had_previous = True
        stage.rename(target)
        swapped = True
        atomic_write(CONFIG, json.dumps(config, indent=2) + "\n", 0o600)
        atomic_write(PROFILE, profile_text(), 0o644)
        atomic_write(SERVICE, service_text(config), 0o644)
        run(["apparmor_parser", "--skip-cache", "-r", str(PROFILE)])
        step(5, "启动并授权普通系统账户")
        run([executables["systemctl"], "daemon-reload"])
        run([executables["systemctl"], "enable", "--now", "astrion-sandbox.service"])
        step(6, "以普通账户验收执行与隔离")
        if __package__:
            from .install_check import verify_install
        else:
            from install_check import verify_install
        verify_install(config, account)
        print(json.dumps({"phase": "done", "user": account.pw_name, "install_root": INSTALL_ROOT}), flush=True)
    except BaseException:
        subprocess.run([executables["systemctl"], "stop", "astrion-sandbox.service"], capture_output=True, timeout=25)
        if not old_enabled:
            subprocess.run([executables["systemctl"], "disable", "astrion-sandbox.service"], capture_output=True, timeout=15)
        if swapped:
            failed = Path(INSTALL_ROOT + ".failed-" + uuid.uuid4().hex)
            target.rename(failed)  # Retain diagnosis; never destroy prior install.
            if had_previous:
                old.rename(target)
        for file in (CONFIG, PROFILE, SERVICE):
            if file in previous:
                atomic_write(file, *previous[file])
            elif file.exists():
                file.unlink()
        if PROFILE in previous:
            run(["apparmor_parser", "--skip-cache", "-r", str(PROFILE)])
        elif swapped:
            unload = parent / (".astrion-profile-unload-" + uuid.uuid4().hex)
            unload.write_text(profile_text())
            try:
                subprocess.run(["apparmor_parser", "--skip-cache", "-R", str(unload)], capture_output=True, timeout=15)
            finally:
                unload.unlink()
        run([executables["systemctl"], "daemon-reload"])
        if had_previous and old_active:
            run([executables["systemctl"], "start", "astrion-sandbox.service"])
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    # Successful upgrades retain a root-owned backup for manual rollback.

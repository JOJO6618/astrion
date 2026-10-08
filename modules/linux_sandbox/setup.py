"""Administrator installation CLI; no Flask/config/runtime data imports."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

# Also runnable by absolute path from a headless shell or pkexec. Importing
# status from the Web backend must not modify its global module search path.
if __package__:
    from .client import probe
    from .constants import INSTALL_ROOT, DEFAULT_SOCKET
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from client import probe
    from constants import INSTALL_ROOT, DEFAULT_SOCKET


def supported_platform() -> bool:
    try:
        release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
        return release.get("ID", "").strip('"') == "ubuntu" and release.get("VERSION_ID", "").strip('"') == "24.04"
    except OSError:
        return False


def resolve_user(raw: str):
    account = pwd.getpwuid(int(raw)) if raw.isdecimal() else pwd.getpwnam(raw)
    if account.pw_uid == 0:
        raise ValueError("Linux host sandbox requires an ordinary OS account; do not run Astrion as root")
    return account


def dependencies() -> tuple[dict, list[str]]:
    executables, missing = {}, []
    for name, executable in (("python", "python3"), ("bwrap", "bwrap"), ("systemd_run", "systemd-run"),
                             ("systemctl", "systemctl"), ("bpftool", "bpftool")):
        found = shutil.which(executable, path="/usr/sbin:/usr/bin:/sbin:/bin")
        if not found:
            missing.append(executable)
        else:
            executables[name] = str(Path(found).resolve())
    for executable in ("cc", "apparmor_parser", "setpriv"):
        if not shutil.which(executable):
            missing.append(executable)
    if "bpftool" in executables:
        try:
            subprocess.run([executables["bpftool"], "version"], capture_output=True, check=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            missing.append("bpftool for current kernel")
    return executables, missing


def status() -> dict:
    result = {"applicable": True, "platform": "linux", "state": "helper_missing", "detail": "",
              "setup_running": False, "distro_name": "", "install_path": INSTALL_ROOT}
    if not supported_platform():
        result.update(state="unsupported", detail="首批支持 Ubuntu 24.04；其它发行版尚未验收。")
        return result
    if os.getuid() == 0:
        result.update(state="ordinary_user_required", detail="请使用普通系统账户运行 Astrion，管理员仅负责安装助手。")
        return result
    try:
        probe()
        result["state"] = "ready"
    except (OSError, ValueError, RuntimeError) as error:
        result["detail"] = str(error)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Astrion Linux host sandbox installer (Ubuntu 24.04)")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("status", help="Check helper status as the ordinary Astrion user")
    install = sub.add_parser("install", help="Install the root-owned helper and verify the selected ordinary user")
    install.add_argument("--user", required=True, help="OS username or numeric UID running Astrion")
    install.add_argument("--install-dependencies", action="store_true", help="Install Ubuntu packages through apt")
    install.add_argument("--dry-run", action="store_true", help="Show targets without changing the system")
    args = parser.parse_args()
    if args.operation == "status":
        report = status()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["state"] == "ready" else 1
    account = resolve_user(args.user)
    executables, missing = dependencies()
    if args.dry_run:
        print(json.dumps({"supported": supported_platform(), "user": account.pw_name, "uid": account.pw_uid,
                          "install_root": INSTALL_ROOT, "socket": DEFAULT_SOCKET,
                          "config": "/etc/astrion-sandbox/helper.json",
                          "service": "/etc/systemd/system/astrion-sandbox.service",
                          "profile": "/etc/apparmor.d/astrion-linux-sandbox", "missing": missing,
                          "install_dependencies": args.install_dependencies}, ensure_ascii=False, indent=2))
        return 0
    if os.geteuid() != 0:
        raise PermissionError("Run install through sudo/pkexec; status needs no administrator permission")
    if not supported_platform():
        raise RuntimeError("The installer currently supports Ubuntu 24.04 only")
    if __package__:
        from .installer import install_helper
    else:
        from installer import install_helper
    install_helper(account, args.install_dependencies)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Linux sandbox setup failed: {error}", file=sys.stderr)
        sys.exit(1)

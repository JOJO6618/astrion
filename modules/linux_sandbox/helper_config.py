"""Configuration is read only from the immutable administrator installation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

try:
    from .constants import DEFAULT_SOCKET, INSTALL_ROOT, PROFILE_PREFIX, PROTOCOL_VERSION
except ImportError:
    from constants import DEFAULT_SOCKET, INSTALL_ROOT, PROFILE_PREFIX, PROTOCOL_VERSION


def trusted_path(raw: str, directory: bool = False) -> Path:
    path = Path(raw)
    if not path.is_absolute() or str(path) != raw:
        raise PermissionError("Privileged installation paths must be absolute and normalized")
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        # A root-owned sticky ancestor such as /tmp cannot be used by another
        # user to replace its root-owned children. The final path stays strict.
        sticky_ancestor = component != path and stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022 and not sticky_ancestor):
            raise PermissionError(f"Untrusted privileged installation path: {component}")
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise PermissionError(f"Wrong privileged installation file type: {path}")
    return path


def load_config(path: str) -> dict:
    info = trusted_path(path).stat()
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError("Helper configuration must be root-owned mode 0600")
    config = json.loads(Path(path).read_text())
    expected = {"protocol", "allowed_uids", "socket", "runtime", "install_root", "bwrap",
                "python", "systemd_run", "systemctl", "bpftool", "max_jobs", "runtime_max_sec"}
    if set(config) != expected or config["protocol"] != PROTOCOL_VERSION:
        raise ValueError("Unsupported helper configuration")
    uids = config["allowed_uids"]
    if not isinstance(uids, list) or not uids or any(type(uid) is not int or not 0 < uid < 2**32 for uid in uids):
        raise ValueError("Authorize explicit ordinary OS users; root is not a sandbox identity")
    if type(config["max_jobs"]) is not int or not 1 <= config["max_jobs"] <= 64:
        raise ValueError("Invalid helper concurrency limit")
    if type(config["runtime_max_sec"]) is not int or not 10 <= config["runtime_max_sec"] <= 86400:
        raise ValueError("Invalid sandbox lifetime")
    trusted_path(config["install_root"], directory=True)
    trusted_path(config["runtime"], directory=True)
    trusted_path(str(Path(config["socket"]).parent), directory=True)
    for name in ("bwrap", "python", "systemd_run", "systemctl", "bpftool"):
        trusted_path(config[name])
    for name in ("uid-exec", "policy.bpf", "policy-none.bpf", "runner.py", "server.py",
                 "constants.py", "helper_config.py", "principal.py", "schema.py", "transport.py",
                 "sources.py", "mounts.py", "supervisor.py"):
        trusted_path(str(Path(config["install_root"]) / name))
    if os.geteuid() != 0:
        raise PermissionError("The sandbox broker must be launched by the administrator service")
    return config


def defaults(uid: int, executables: dict) -> dict:
    return {"protocol": PROTOCOL_VERSION, "allowed_uids": [uid], "socket": DEFAULT_SOCKET,
            "runtime": "/run/astrion-sandbox/jobs", "install_root": INSTALL_ROOT,
            "max_jobs": 16, "runtime_max_sec": 86400, **executables}


def profile_name(network: str) -> str:
    return PROFILE_PREFIX + network

"""Immutable root entry launched only inside its transient systemd service."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys

# Installation invokes Python -I: only this root-owned directory is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helper_config import load_config, profile_name
from mounts import build_command
from transport import receive_packet, send_packet
from constants import MAX_PATHS, SYSTEM_FILES, SYSTEM_DIRECTORIES


def verify_filters(config: dict, network: str) -> None:
    label = Path("/proc/self/attr/current").read_text().strip()
    if label != profile_name(network) + " (enforce)":
        raise PermissionError("Required enforcing AppArmor profile is absent")
    if network == "full":
        return
    cgroup = next(line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines()
                  if line.startswith("0::"))
    if not Path(cgroup).name.startswith("astrion-sandbox-job-"):
        raise PermissionError("Sandbox task is outside its dedicated cgroup")
    result = subprocess.run([config["bpftool"], "cgroup", "show", "/sys/fs/cgroup" + cgroup],
                            capture_output=True, text=True, timeout=5, check=True)
    for attachment in ("cgroup_inet_ingress", "cgroup_inet_egress"):
        if attachment not in result.stdout:
            raise PermissionError("Required cgroup IP filtering is absent; refusing execution")


def main() -> int:
    config = load_config(sys.argv[1])
    rendezvous = Path(sys.argv[2])
    if rendezvous.parent.parent != Path(config["runtime"]):
        raise PermissionError("Invalid sandbox rendezvous path")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    connection.settimeout(12)
    connection.connect(str(rendezvous))
    send_packet(connection, {"runner": True})
    payload, descriptors = receive_packet(connection, MAX_PATHS + len(SYSTEM_FILES) + len(SYSTEM_DIRECTORIES) + 2)
    try:
        request = payload["request"]
        verify_filters(config, request["network"])
        if len(descriptors) != len(payload["sources"]):
            raise ValueError("Incomplete pinned mount transfer")
        sources = [{**item, "fd": fd} for item, fd in zip(payload["sources"], descriptors)]
        policy = "policy-none.bpf" if request["network"] == "none" else "policy.bpf"
        policy_fd = os.open(config["install_root"] + "/" + policy, os.O_RDONLY | os.O_CLOEXEC)
        command = build_command(config, request, payload["principal"], sources, payload["lease"], policy_fd)
        send_packet(connection, {"ready": True})
        permission, extra_fds = receive_packet(connection, 0)
        if permission != {"execute": True} or extra_fds:
            raise PermissionError("Sandbox execution was cancelled before start")
        connection.close()
        for fd in [*descriptors, policy_fd]:
            os.set_inheritable(fd, True)
        os.execve(command[0], command, {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    finally:
        connection.close()
        for fd in descriptors:
            os.close(fd)
    return 125


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Linux sandbox refused execution: {error}", file=sys.stderr)
        sys.exit(125)

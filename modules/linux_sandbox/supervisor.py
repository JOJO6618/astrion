"""One control connection owns one transient unit and its cancellation lease."""
from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import socket
import struct
import subprocess
import time
import uuid

try:
    from .helper_config import profile_name
    from .sources import pin_sources
    from .transport import send_packet, receive_packet
except ImportError:
    from helper_config import profile_name
    from sources import pin_sources
    from transport import send_packet, receive_packet

PRIVILEGED_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def unit_command(config: dict, name: str, network: str, rendezvous: str, config_path: str) -> list[str]:
    command = [config["systemd_run"], "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec",
               "--unit=" + name, "--property=KillMode=control-group", "--property=TimeoutStopSec=2",
               "--property=RuntimeMaxSec=" + str(config["runtime_max_sec"]), "--property=TasksMax=512",
               "--property=MemoryMax=4G", "--property=UMask=0077",
               "--property=AppArmorProfile=" + profile_name(network)]
    # Installed jobs stop even if the broker service is killed or restarted.
    # Isolated experiments have no installed service and use a private socket.
    if config["socket"] == "/run/astrion-sandbox/control.sock":
        command += ["--property=BindsTo=astrion-sandbox.service", "--property=After=astrion-sandbox.service"]
    if network != "full":
        command += ["--property=IPAddressDeny=any"]
        if network == "restricted":
            command += ["--property=IPAddressAllow=localhost"]
    return command + [config["python"], "-I", config["install_root"] + "/runner.py", config_path, rendezvous]


def run_job(config: dict, config_path: str, connection: socket.socket, principal, request: dict,
            stdio: list[int]) -> None:
    sources = []
    process = None
    token = uuid.uuid4().hex[:16]
    name = "astrion-sandbox-job-" + token
    directory = Path(config["runtime"]) / token
    directory.mkdir(mode=0o700)
    lease = directory / "lease"
    lease.touch(mode=0o600)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    runner = None
    try:
        sources = pin_sources(principal, request)
        if not principal.alive() or select.select([connection], [], [], 0)[0]:
            return
        listener.bind(str(directory / "control"))
        listener.listen(1)
        command = unit_command(config, name, request["network"], str(directory / "control"), config_path)
        process = subprocess.Popen(command, stdin=stdio[0], stdout=stdio[1], stderr=stdio[2],
                                   env=PRIVILEGED_ENV, close_fds=True, start_new_session=True)
        deadline = time.monotonic() + 15
        ready = False
        while process.poll() is None:
            readers = [connection, principal.pidfd]
            if runner is None:
                readers.append(listener)
            events = select.select(readers, [], [], 0.1)[0]
            if connection in events or principal.pidfd in events:
                return
            if listener in events:
                runner, _ = listener.accept()
                runner.settimeout(8)
                _, uid, _ = struct.unpack("iII", runner.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != 0:
                    raise PermissionError("Untrusted unit runner")
                hello, fds = receive_packet(runner, 0)
                if hello != {"runner": True} or fds:
                    raise PermissionError("Invalid unit runner handshake")
                send_packet(runner, {"request": request, "principal": {"uid": principal.uid,
                            "gid": principal.gid, "groups": principal.groups},
                            "lease": str(lease), "sources": [{k: v for k, v in item.items() if k != "fd"}
                                                            for item in sources]},
                            [item["fd"] for item in sources])
                response, fds = receive_packet(runner, 0)
                if response != {"ready": True} or fds:
                    raise PermissionError("Unit runner did not confirm its filters")
                if not principal.alive() or select.select([connection], [], [], 0)[0]:
                    return
                send_packet(runner, {"execute": True})
                ready = True
                send_packet(connection, {"started": True, "unit": name})
            if not ready and time.monotonic() > deadline:
                raise TimeoutError("Sandbox unit creation timed out")
        send_packet(connection, {"exitcode": process.returncode})
    finally:
        # Revokes even a not-yet-started runner's FD: uid-exec checks st_nlink.
        lease.unlink(missing_ok=True)
        if process is not None:
            try:
                subprocess.run([config["systemctl"], "stop", name + ".service"],
                               env=PRIVILEGED_ENV, capture_output=True, timeout=6)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    subprocess.run([config["systemctl"], "kill", "--kill-whom=all", "--signal=KILL", name + ".service"],
                                   env=PRIVILEGED_ENV, capture_output=True, timeout=4)
                except (OSError, subprocess.TimeoutExpired):
                    # Lease is already revoked. Installed units are additionally
                    # bound to the broker service and have a finite lifetime.
                    pass
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        if runner is not None:
            runner.close()
        listener.close()
        for item in sources:
            os.close(item["fd"])
        shutil.rmtree(directory)

"""Root service: kernel peer authentication, strict protocol, isolated workers."""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import signal
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import PROTOCOL_VERSION
from helper_config import load_config
from principal import identify_peer
from schema import validate_request
from sources import reap_child
from supervisor import run_job
from transport import receive_packet, send_packet, validate_stdio


def status_payload(config: dict) -> dict:
    profiles = Path("/sys/kernel/security/apparmor/profiles").read_text()
    for network in ("full", "restricted", "none"):
        if f"astrion-linux-sandbox-{network} (enforce)" not in profiles:
            raise PermissionError("An enforcing sandbox AppArmor profile is missing")
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise PermissionError("cgroup v2 is required")
    return {"protocol": PROTOCOL_VERSION, "ready": True, "network": ["full", "restricted", "none"]}


def worker(connection, config, config_path, server_pid):
    principal = None
    descriptors = []
    def stop(_signal, _frame):
        raise SystemExit(125)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != server_pid:
        raise RuntimeError("Cannot bind sandbox worker lifetime to its broker")
    try:
        connection.settimeout(5)
        principal = identify_peer(connection, set(config["allowed_uids"]))
        request, descriptors = receive_packet(connection)
        request = validate_request(request)
        if request["operation"] == "status":
            if descriptors:
                raise ValueError("Status takes no descriptors")
            send_packet(connection, status_payload(config))
        else:
            validate_stdio(descriptors)
            status_payload(config)
            connection.settimeout(None)
            run_job(config, config_path, connection, principal, request, descriptors)
    except Exception as error:
        # The administrator helper has no Flask logger; retain bounded errors
        # in its protected runtime directory, never user-controlled paths.
        log = Path(config["runtime"]) / "helper-errors.log"
        if log.exists() and log.stat().st_size > 65536:
            log.unlink()
        with log.open("a") as stream:
            stream.write(f"{type(error).__name__}: {error}\n")
        try:
            send_packet(connection, {"error": str(error), "exitcode": 125})
        except (OSError, EOFError):
            pass
    finally:
        for fd in descriptors:
            os.close(fd)
        if principal:
            os.close(principal.pidfd)
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/astrion-sandbox/helper.json")
    args = parser.parse_args()
    config = load_config(args.config)
    status_payload(config)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    # Directory is root-owned 0755; socket is DAC-accessible, UID allowlist is
    # authoritative. Never remove an existing broker's live socket on startup.
    path = Path(config["socket"])
    if path.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            probe.connect(str(path))
        except ConnectionRefusedError:
            path.unlink()
        else:
            raise RuntimeError("A sandbox broker is already listening")
        finally:
            probe.close()
    listener.bind(str(path))
    os.chmod(path, 0o666)
    listener.listen(config["max_jobs"])
    listener.settimeout(0.5)
    children = set()
    stopping = False
    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            for pid in tuple(children):
                try:
                    if os.waitpid(pid, os.WNOHANG)[0]:
                        children.remove(pid)
                except ChildProcessError:
                    children.discard(pid)
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            if len(children) >= config["max_jobs"]:
                send_packet(connection, {"error": "Sandbox helper is at its concurrency limit", "exitcode": 125})
                connection.close()
                continue
            parent_pid = os.getpid()
            pid = os.fork()
            if pid == 0:
                listener.close()
                # Workers must not retain sibling control/stdio descriptors.
                try:
                    worker(connection, config, args.config, parent_pid)
                finally:
                    os._exit(0)
            children.add(pid)
            connection.close()
    finally:
        listener.close()
        path.unlink(missing_ok=True)
        for pid in children:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 12
        while children and time.monotonic() < deadline:
            for pid in tuple(children):
                try:
                    if os.waitpid(pid, os.WNOHANG)[0]:
                        children.remove(pid)
                except ChildProcessError:
                    children.discard(pid)
            time.sleep(0.05)
        for pid in children:
            reap_child(pid)


if __name__ == "__main__":
    main()

"""Probe only disposable fixtures supplied by the experiment harness."""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading


def outcome(action):
    try:
        return {"ok": True, "value": action()}
    except OSError as exc:
        return {"ok": False, "errno": exc.errno, "error": type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}


def tcp(address, port, family=socket.AF_INET):
    with socket.socket(family, socket.SOCK_STREAM) as client:
        client.settimeout(0.7)
        client.connect((address, port))
        client.sendall(b"sandbox-experiment\n")
        return client.recv(100).decode()


def udp(address, port, family=socket.AF_INET):
    with socket.socket(family, socket.SOCK_DGRAM) as client:
        client.settimeout(0.7)
        client.sendto(b"sandbox-experiment", (address, port))
        return client.recv(100).decode()


def unix(address):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(0.7)
        client.connect(address)
        client.sendall(b"sandbox-experiment\n")
        return client.recv(100).decode()


def main():
    config = json.loads(sys.argv[1])
    if config.get("serve"):
        with socket.socket() as server:
            server.bind(("127.0.0.1", config.get("serve_port", 0)))
            server.listen(1)
            server.settimeout(4)
            print(json.dumps({"listening_port": server.getsockname()[1]}), flush=True)
            try:
                connection, _ = server.accept()
            except socket.timeout:
                print("no-host-connection", flush=True)
                return
            with connection:
                connection.settimeout(1)
                connection.recv(100)
                connection.sendall(b"sandbox-listener")
        return

    workspace = Path(config["workspace"])
    sibling = Path(config["outside"])
    authorized = Path(config["authorized"])
    results = {}
    results["workspace_read"] = outcome(lambda: (workspace / "marker.txt").read_text())
    results["workspace_write"] = outcome(lambda: (workspace / "written.txt").write_text("probe-write"))
    results["outside_read"] = outcome(lambda: (sibling / "marker.txt").read_text())
    results["outside_write"] = outcome(lambda: (sibling / "written.txt").write_text("escape"))
    results["symlink_outside_read"] = outcome(lambda: (workspace / "escape-link").read_text())
    results["authorized_read"] = outcome(lambda: (authorized / "marker.txt").read_text())
    results["authorized_write"] = outcome(lambda: (authorized / "written.txt").write_text("authorized-write"))
    results["workspace_chmod"] = outcome(lambda: os.chmod(workspace / "mode-test.txt", 0o640))
    results["workspace_unlink"] = outcome(lambda: (workspace / "unlink-test.txt").unlink())
    results["readonly_mount_write"] = outcome(lambda: Path("/readonly-fixture.txt").write_text("experiment"))
    results["anonymous_tmp_write"] = outcome(lambda: Path("/tmp/probe-marker.txt").write_text("temporary"))
    results["root_write"] = outcome(lambda: Path("/escape.txt").write_text("temporary"))
    results["host_data_visible"] = Path("/opt/agent/runtime").exists()
    results["host_home_visible"] = Path("/root/.ssh").exists()
    results["cgroup_filesystem_visible"] = Path("/sys/fs/cgroup").exists()
    results["proc_processes"] = [item for item in os.listdir("/proc") if item.isdigit()]
    results["git"] = outcome(lambda: subprocess.check_output(["git", "--version"], text=True).strip())
    results["python_child"] = outcome(lambda: subprocess.check_output(["python3", "-c", "print('child-ok')"], text=True).strip())
    thread_values = []
    def run_thread():
        thread_values.append("thread-ok")
    def thread_probe():
        thread = threading.Thread(target=run_thread)
        thread.start()
        thread.join(1)
        return thread_values
    results["python_thread"] = outcome(thread_probe)
    results["netlink_socket"] = outcome(lambda: socket.socket(socket.AF_NETLINK).close())
    libc = ctypes.CDLL(None, use_errno=True)
    libc.unshare.argtypes = [ctypes.c_int]
    libc.unshare.restype = ctypes.c_int
    code = libc.unshare(0x40000000)
    results["nested_network_namespace"] = {"returncode": code, "errno": ctypes.get_errno() if code else 0}
    results["ipv4_localhost_tcp"] = outcome(lambda: tcp("127.0.0.1", config["tcp4"]))
    results["ipv4_localhost_udp"] = outcome(lambda: udp("127.0.0.1", config["udp4"]))
    results["ipv6_localhost_tcp"] = outcome(lambda: tcp("::1", config["tcp6"], socket.AF_INET6))
    results["ipv6_localhost_udp"] = outcome(lambda: udp("::1", config["udp6"], socket.AF_INET6))
    results["host_nonloopback_tcp"] = outcome(lambda: tcp(config["host_ip"], config["tcp_nonloopback"]))
    results["host_nonloopback_udp"] = outcome(lambda: udp(config["host_ip"], config["udp_nonloopback"]))
    def external_handshake():
        with socket.socket() as client:
            client.settimeout(1.5)
            client.connect((config.get("external_ip", "104.20.23.154"), 80))
            return "connected-no-data-sent"
    results["external_tcp"] = outcome(external_handshake)
    results["filesystem_unix_socket"] = outcome(lambda: unix(config["unix_path"]))
    results["outside_unix_socket"] = outcome(lambda: unix(config["outside_unix"]))
    results["abstract_unix_socket"] = outcome(lambda: unix("\0" + config["abstract_unix"]))
    def socketpair_probe():
        left, right = socket.socketpair()
        try:
            left.sendall(b"pair-ok")
            return right.recv(20).decode()
        finally:
            left.close()
            right.close()
    results["unix_socketpair"] = outcome(socketpair_probe)
    print(json.dumps(results, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()

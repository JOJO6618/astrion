"""Disposable Linux sandbox experiments. All persistent writes stay in ROOT.

Transient systemd units affect only experimental subprocesses, use resource/time
limits, and are stopped in finally. No package installation or firewall edits.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading
import time

from seccomp_policy import export_policy

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
OUTSIDE = ROOT / "outside"
AUTHORIZED = ROOT / "authorized"
BWRAP = ROOT / "tools/usr/bin/bwrap"
RESULTS = ROOT / "results"
UNITS: list[str] = []


class FixtureServer:
    def __init__(self, family, kind, address):
        self.socket = socket.socket(family, kind)
        self.socket.bind(address)
        if kind == socket.SOCK_STREAM:
            self.socket.listen(8)
        self.kind = kind
        self.socket.settimeout(0.2)
        self.address = self.socket.getsockname()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def _run(self):
        while not self.stop.is_set():
            try:
                if self.kind == socket.SOCK_STREAM:
                    connection, _ = self.socket.accept()
                    with connection:
                        connection.settimeout(1)
                        message = connection.recv(100)
                        if message == b"sandbox-experiment\n":
                            connection.sendall(b"fixture-ok")
                else:
                    message, peer = self.socket.recvfrom(100)
                    if message == b"sandbox-experiment":
                        self.socket.sendto(b"fixture-ok", peer)
            except (socket.timeout, OSError):
                continue

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(2)
        self.socket.close()


def run(args, timeout=15, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, **kwargs)


def fixture_snapshot():
    result = {}
    for path in sorted(OUTSIDE.iterdir()):
        if path.is_file():
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def bwrap_command(config, readonly=False, authorization="none", shared_net=True):
    argv = [str(BWRAP), "--die-with-parent", "--new-session", "--unshare-all"]
    if shared_net:
        argv.append("--share-net")
    argv.extend(["--ro-bind", "/usr", "/usr"])
    # Ubuntu usrmerge layout: preserve aliases without broad host-root binds.
    for directory in ("bin", "sbin", "lib", "lib64"):
        source = Path("/") / directory
        if source.is_symlink():
            argv.extend(["--symlink", os.readlink(source), str(source)])
        elif source.exists():
            argv.extend(["--ro-bind", str(source), str(source)])
    for raw in ("/etc/hosts", "/etc/resolv.conf", "/etc/nsswitch.conf",
                "/etc/passwd", "/etc/group", "/etc/ssl/certs",
                "/etc/gai.conf", "/etc/localtime"):
        source = Path(raw)
        if source.exists():
            argv.extend(["--ro-bind", str(source.resolve()), raw])
    argv.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--tmpfs", "/var/tmp", "--tmpfs", "/home/sandbox"])
    argv.extend(["--ro-bind", str(ROOT / "probe.py"), "/probe.py",
                 "--ro-bind", str(ROOT / "readonly-fixture.txt"), "/readonly-fixture.txt"])
    if authorization != "none":
        argv.extend(["--bind" if authorization == "rw" and not readonly else "--ro-bind",
                     str(AUTHORIZED), str(AUTHORIZED)])
    argv.extend(["--ro-bind" if readonly else "--bind", str(WORKSPACE), str(WORKSPACE)])
    argv.extend(["--remount-ro", "/", "--cap-drop", "ALL", "--chdir", str(WORKSPACE),
                 "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
                 "--setenv", "HOME", "/home/sandbox",
                 "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null",
                 "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                 "--seccomp", "SECCOMP_FD", "--",
                 "/usr/bin/python3", "/probe.py", json.dumps(config)])
    return argv


def in_sandbox(config, *, restricted=False, gate_unix=False, **options):
    command = bwrap_command(config, **options)
    fd = os.open(ROOT / "policy.bpf", os.O_RDONLY)
    command = [str(fd) if token == "SECCOMP_FD" else token for token in command]
    try:
        if restricted:
            # systemd does not forward our seccomp FD. The unit instead invokes
            # a tiny helper which opens the same experiment-local policy itself.
            unit = f"astrion-sandbox-{ROOT.name.rsplit('-', 1)[-1].lower()}-{len(UNITS)}"
            UNITS.append(unit)
            command = [
                "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
                f"--unit={unit}", "--property=RuntimeMaxSec=20",
                "--property=TimeoutStopSec=3", "--property=MemoryMax=192M",
                "--property=TasksMax=32", "--property=CPUQuota=50%",
                "--property=IPAddressDeny=any", "--property=IPAddressAllow=localhost",
                "--property=IPAccounting=yes",
                *([str(ROOT / "unix_gate")] if gate_unix else []),
                "/usr/bin/python3", str(ROOT / "unit_helper.py"),
                json.dumps(bwrap_command(config, **options)), str(ROOT / "policy.bpf"),
            ]
            return run(command, timeout=25)
        return run(command, pass_fds=(fd,), timeout=15)
    finally:
        os.close(fd)


def assert_matrix(report):
    checks = []
    def check(label, value):
        checks.append({"name": label, "passed": bool(value)})
    for variant in report["variants"]:
        name = variant["name"]
        result = variant.get("probe", {})
        check(name + ":process_success", variant["returncode"] == 0)
        for key in ("outside_read", "outside_write", "symlink_outside_read", "outside_unix_socket",
                    "root_write", "readonly_mount_write", "netlink_socket"):
            check(name + ":" + key, result.get(key, {}).get("ok") is False)
        for key in ("host_data_visible", "host_home_visible", "cgroup_filesystem_visible"):
            check(name + ":" + key, result.get(key) is False)
        for key in ("workspace_read", "anonymous_tmp_write", "git", "python_child", "python_thread"):
            check(name + ":" + key, result.get(key, {}).get("ok") is True)
        writable = not variant["readonly"]
        for key in ("workspace_write", "workspace_chmod", "workspace_unlink"):
            check(name + ":" + key, result.get(key, {}).get("ok") == writable)
        check(name + ":authorized_read", result.get("authorized_read", {}).get("ok") == (variant["authorization"] != "none"))
        check(name + ":authorized_write", result.get("authorized_write", {}).get("ok") == (variant["authorization"] == "rw" and writable))
        for key in ("ipv4_localhost_tcp", "ipv4_localhost_udp", "ipv6_localhost_tcp", "ipv6_localhost_udp"):
            check(name + ":" + key, result.get(key, {}).get("ok") == variant["shared_net"])
        for key in ("host_nonloopback_tcp", "host_nonloopback_udp", "external_tcp"):
            expected = variant["shared_net"] and not variant["restricted"]
            check(name + ":" + key, result.get(key, {}).get("ok") == expected)
        check(name + ":filesystem_unix", result.get("filesystem_unix_socket", {}).get("ok") == (not variant["gate_unix"]))
        check(name + ":abstract_unix", result.get("abstract_unix_socket", {}).get("ok") == (variant["shared_net"] and not variant["gate_unix"]))
        check(name + ":unix_socketpair", result.get("unix_socketpair", {}).get("ok") is True)
    report["checks"] = checks
    report["passed_checks"] = sum(item["passed"] for item in checks)
    report["total_checks"] = len(checks)


def main():
    RESULTS.mkdir(exist_ok=True)
    for folder, marker in ((WORKSPACE, "workspace"), (OUTSIDE, "outside"), (AUTHORIZED, "authorized")):
        folder.mkdir(exist_ok=True)
        (folder / "marker.txt").write_text(marker)
    (ROOT / "readonly-fixture.txt").write_text("readonly-fixture")
    (WORKSPACE / "escape-link").symlink_to(OUTSIDE / "marker.txt")
    snapshot = fixture_snapshot()
    report = {"root": str(ROOT), "kernel": os.uname().release, "uid": os.getuid(),
              "seccomp": export_policy(ROOT / "policy.bpf"), "variants": []}
    # Route lookup does not send network packets.
    route = json.loads(run(["ip", "-j", "route", "get", "192.0.2.1"]).stdout)
    host_ip = route[0]["prefsrc"]
    external_ip = socket.getaddrinfo("example.com", 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    report["external_baseline_endpoint"] = {"hostname": "example.com", "address": external_ip, "port": 80, "application_data_sent": False}
    try:
        with ExitStack() as stack:
            fixtures = {}
            for name, family, kind, address in (
                ("tcp4", socket.AF_INET, socket.SOCK_STREAM, ("127.0.0.1", 0)),
                ("udp4", socket.AF_INET, socket.SOCK_DGRAM, ("127.0.0.1", 0)),
                ("tcp6", socket.AF_INET6, socket.SOCK_STREAM, ("::1", 0)),
                ("udp6", socket.AF_INET6, socket.SOCK_DGRAM, ("::1", 0)),
                ("tcp_nonloopback", socket.AF_INET, socket.SOCK_STREAM, (host_ip, 0)),
                ("udp_nonloopback", socket.AF_INET, socket.SOCK_DGRAM, (host_ip, 0)),
            ):
                server = stack.enter_context(FixtureServer(family, kind, address))
                fixtures[name] = server.address[1]
            unix_path = str(WORKSPACE / "fixture.sock")
            outside_unix = str(OUTSIDE / "fixture.sock")
            abstract_unix = "astrion_exp_" + ROOT.name.rsplit("-", 1)[-1]
            for address in (unix_path, outside_unix, "\0" + abstract_unix):
                stack.enter_context(FixtureServer(socket.AF_UNIX, socket.SOCK_STREAM, address))
            config = {"workspace": str(WORKSPACE), "outside": str(OUTSIDE),
                      "authorized": str(AUTHORIZED), "host_ip": host_ip,
                      "unix_path": unix_path, "outside_unix": outside_unix,
                      "abstract_unix": abstract_unix, "external_ip": external_ip, **fixtures}
            variants = [
                ("writable_full", False, "none", True, False, False),
                ("readonly_full", True, "none", True, False, False),
                ("authorized_ro_full", False, "ro", True, False, False),
                ("authorized_rw_full", False, "rw", True, False, False),
                ("readonly_authorized_rw", True, "rw", True, False, False),
                ("restricted_host_localhost", False, "none", True, True, False),
                ("restricted_unix_blocked", False, "none", True, True, True),
                ("separate_network_namespace", False, "none", False, False, False),
            ]
            for name, readonly, authorization, shared_net, restricted, gate_unix in variants:
                for filename in ("mode-test.txt", "unlink-test.txt"):
                    (WORKSPACE / filename).write_text("disposable")
                for path in (WORKSPACE / "written.txt", AUTHORIZED / "written.txt"):
                    path.unlink(missing_ok=True)
                result = in_sandbox(config, readonly=readonly, authorization=authorization,
                                    shared_net=shared_net, restricted=restricted, gate_unix=gate_unix)
                record = {"name": name, "readonly": readonly, "authorization": authorization,
                          "shared_net": shared_net, "restricted": restricted, "gate_unix": gate_unix,
                          "returncode": result.returncode, "stderr": result.stderr[-2500:]}
                try:
                    record["probe"] = json.loads(result.stdout.strip().splitlines()[-1])
                except (IndexError, json.JSONDecodeError):
                    record["stdout"] = result.stdout[-2500:]
                report["variants"].append(record)
                print(json.dumps({"variant": name, "returncode": result.returncode}), flush=True)
            report["outside_fixture_unchanged"] = snapshot == fixture_snapshot()
            assert_matrix(report)
    finally:
        cleanup = []
        for unit in UNITS:
            stopped = run(["systemctl", "stop", unit], timeout=8)
            state = run(["systemctl", "show", unit, "--property=ActiveState", "--value"])
            cleanup.append({"unit": unit, "stop_returncode": stopped.returncode,
                            "active_state": state.stdout.strip()})
        report["transient_unit_cleanup"] = cleanup
        report["fixture_threads_alive"] = [thread.name for thread in threading.enumerate() if thread is not threading.main_thread()]
        (RESULTS / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"passed": report.get("passed_checks"), "total": report.get("total_checks"),
                          "outside_unchanged": report.get("outside_fixture_unchanged"),
                          "fixture_threads_alive": report["fixture_threads_alive"]}), flush=True)
    return 0 if report.get("passed_checks") == report.get("total_checks") else 1


if __name__ == "__main__":
    sys.exit(main())

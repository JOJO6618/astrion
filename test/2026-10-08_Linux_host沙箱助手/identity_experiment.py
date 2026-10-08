"""Run experimental ordinary-user sandboxes and unload our own profile."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LEGACY = ROOT.parent / "batch_02/run_experiment.py"
sys.path.insert(0, str(LEGACY.parent))
spec = importlib.util.spec_from_file_location("legacy", LEGACY)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
legacy.ROOT = ROOT
legacy.WORKSPACE = ROOT / "workspace"
legacy.OUTSIDE = ROOT / "outside"
legacy.AUTHORIZED = ROOT / "authorized"
legacy.BWRAP = ROOT / "tools/usr/bin/bwrap"
PROFILE = "astrion-exp-linux-20261008-b03"
UID = GID = 65534


def build(config, readonly):
    command = legacy.bwrap_command(config, readonly=readonly, authorization="ro")
    index = command.index("--unshare-all")
    command[index:index + 1] = ["--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup"]
    index = command.index("--cap-drop")
    command[index + 2:index + 2] = [item for cap in ("CAP_SETUID", "CAP_SETGID", "CAP_SETPCAP", "CAP_CHOWN")
                                  for item in ("--cap-add", cap)]
    for temporary in ("/tmp", "/var/tmp"):
        index = command.index(temporary)
        command[index - 1:index - 1] = ["--perms", "1777"]
    index = command.index("--remount-ro")
    command[index:index] = [
        "--ro-bind", "/etc/ssl/openssl.cnf", "/etc/ssl/openssl.cnf",
        "--tmpfs", "/.astrion-runtime", "--tmpfs", "/.astrion-runtime/home",
        "--ro-bind", str(ROOT / "uid_exec"), "/.astrion-runtime/uid-exec",
        "--ro-bind", str(ROOT / "lease"), "/.astrion-runtime/lease",
        "--ro-bind", str(ROOT / "identity_probe.py"), "/identity_probe.py",
        "--remount-ro", "/.astrion-runtime",
    ]
    mounted = {Path(command[i + 2]) for i, token in enumerate(command) if token in {"--bind", "--ro-bind"}}
    parents = {parent for path in mounted for parent in path.parents if str(parent) not in {"/", "/tmp", "/var/tmp", "/.astrion-runtime"}}
    pure_parents = [parent for parent in parents if not any(parent == mount or mount in parent.parents for mount in mounted)]
    index = command.index("--remount-ro")
    command[index:index] = [item for parent in sorted(pure_parents, key=lambda value: len(value.parts))
                            for item in ("--chmod", "0755", str(parent))]
    index = command.index("HOME")
    command[index + 1] = "/.astrion-runtime/home"
    command[-3:] = ["/.astrion-runtime/uid-exec", str(UID), str(GID), "18", str(GID),
                    "/usr/bin/python3", "/identity_probe.py", json.dumps(config)]
    return command


def main():
    (ROOT / "results").mkdir(exist_ok=True)
    for folder in (legacy.WORKSPACE, legacy.OUTSIDE, legacy.AUTHORIZED):
        folder.mkdir(exist_ok=True)
        os.chown(folder, UID, GID)
        os.chmod(folder, 0o755)
        (folder / "marker.txt").write_text(folder.name)
        os.chown(folder / "marker.txt", UID, GID)
        os.chmod(folder / "marker.txt", 0o644)
    (legacy.WORKSPACE / "escape-link").symlink_to(legacy.OUTSIDE / "marker.txt")
    private = legacy.WORKSPACE / "root-private.txt"
    private.write_text("synthetic-root-only-fixture")
    private.chmod(0o600)
    (ROOT / "readonly-fixture.txt").write_text("readonly")
    (ROOT / "lease").write_text("active")
    (ROOT / "lease").chmod(0o600)
    policy = legacy.export_policy(ROOT / "policy.bpf")
    external = socket.getaddrinfo("example.com", 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    route = json.loads(legacy.run(["ip", "-j", "route", "get", "192.0.2.1"]).stdout)
    host_ip = route[0]["prefsrc"]
    report = {"policy": policy, "variants": [], "units": []}
    loaded = False
    try:
        parser = legacy.run(["apparmor_parser", "--skip-cache", "-a", str(ROOT / "experiment.apparmor")])
        if parser.returncode:
            raise RuntimeError(parser.stderr)
        loaded = True
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
                service = stack.enter_context(legacy.FixtureServer(family, kind, address))
                fixtures[name] = service.address[1]
            unix_path = str(legacy.WORKSPACE / "fixture.sock")
            outside_unix = str(legacy.OUTSIDE / "fixture.sock")
            abstract = "astrion_exp_b03"
            for address in (unix_path, outside_unix, "\0" + abstract):
                stack.enter_context(legacy.FixtureServer(socket.AF_UNIX, socket.SOCK_STREAM, address))
                if not address.startswith("\0"):
                    os.chmod(address, 0o666)
            config = {"workspace": str(legacy.WORKSPACE), "outside": str(legacy.OUTSIDE),
                      "authorized": str(legacy.AUTHORIZED), "host_ip": host_ip, "external_ip": external,
                      "unix_path": unix_path, "outside_unix": outside_unix, "abstract_unix": abstract, **fixtures}
            for index, (name, readonly, restricted, profile) in enumerate((
                ("ordinary_full", False, False, False),
                ("ordinary_restricted", False, True, True),
                ("ordinary_readonly", True, True, True),
            )):
                for filename in ("mode-test.txt", "unlink-test.txt"):
                    path = legacy.WORKSPACE / filename
                    path.write_text("disposable")
                    os.chown(path, UID, GID)
                    path.chmod(0o644)
                config["readonly"] = readonly
                command = build(config, readonly)
                fd = os.open(ROOT / "policy.bpf", os.O_RDONLY)
                unit = f"astrion-sandbox-b03-{index}"
                report["units"].append(unit)
                prefix = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", f"--unit={unit}",
                          "--property=RuntimeMaxSec=45", "--property=TimeoutStopSec=3",
                          "--property=MemoryMax=256M", "--property=TasksMax=48"]
                if restricted:
                    prefix += ["--property=IPAddressDeny=any", "--property=IPAddressAllow=localhost"]
                if profile:
                    prefix += [f"--property=AppArmorProfile={PROFILE}"]
                # helper opens the FD in the transient process before exec bwrap.
                command = prefix + ["/usr/bin/python3", str(ROOT / "unit_helper.py"), json.dumps(command), str(ROOT / "policy.bpf")]
                try:
                    output = legacy.run(command, timeout=50)
                finally:
                    os.close(fd)
                record = {"name": name, "readonly": readonly, "restricted": restricted, "profile": profile,
                          "returncode": output.returncode, "stderr": output.stderr}
                lines = output.stdout.strip().splitlines()
                try:
                    record["probe"] = json.loads(lines[0])
                    record["identity"] = json.loads(lines[1])["identity_probe"]
                except (IndexError, json.JSONDecodeError):
                    record["stdout"] = output.stdout
                report["variants"].append(record)
                identity = record.get("identity", {})
                print(json.dumps({"name": name, "returncode": output.returncode, "uid": identity.get("uid"),
                                  "workspace_read": record.get("probe", {}).get("workspace_read"),
                                  "unix_path": record.get("probe", {}).get("filesystem_unix_socket"),
                                  "unix_abstract": record.get("probe", {}).get("abstract_unix_socket"),
                                  "node_worker": identity.get("node_worker"), "npm": identity.get("npm"),
                                  "venv": identity.get("venv_create"), "stderr": output.stderr}), flush=True)
    finally:
        for unit in report["units"]:
            legacy.run(["systemctl", "stop", unit], timeout=8)
        if loaded:
            unloaded = legacy.run(["apparmor_parser", "--skip-cache", "-R", str(ROOT / "experiment.apparmor")])
            report["profile_unload_returncode"] = unloaded.returncode
        (ROOT / "results/identity.json").write_text(json.dumps(report, indent=2))
    return 0 if all(item["returncode"] == 0 for item in report["variants"]) else 1


if __name__ == "__main__":
    sys.exit(main())

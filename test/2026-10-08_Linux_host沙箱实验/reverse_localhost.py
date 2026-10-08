"""Verify host -> sandbox localhost without touching existing service ports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from run_experiment import bwrap_command, run


def main():
    records = []
    for index, (name, shared, restricted, gate_unix) in enumerate((
        ("full", True, False, False),
        ("restricted", True, True, False),
        ("restricted_unix_blocked", True, True, True),
        ("separate_namespace", False, False, False),
    )):
        reservation = socket.socket()
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        config = {"serve": True, "serve_port": port}
        command = ["/usr/bin/python3", str(ROOT / "unit_helper.py"),
                   json.dumps(bwrap_command(config, shared_net=shared)), str(ROOT / "policy.bpf")]
        unit = None
        if restricted:
            unit = f"astrion-sandbox-reverse-{ROOT.name}-{index}"
            command = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", f"--unit={unit}",
                       "--property=RuntimeMaxSec=12", "--property=TimeoutStopSec=3",
                       "--property=MemoryMax=192M", "--property=TasksMax=32",
                       "--property=IPAddressDeny=any", "--property=IPAddressAllow=localhost",
                       *([str(ROOT / "unix_gate")] if gate_unix else []), *command]
        # In separate-network mode keep the host port bound (not listening).
        # A failed host connection then cannot accidentally contact a real service.
        if shared:
            reservation.close()
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        record = {"variant": name, "host_connection_expected": shared}
        try:
            readable, _, _ = select.select([process.stdout], [], [], 4)
            if not readable:
                raise RuntimeError("Sandbox listener did not become ready")
            line = process.stdout.readline()
            record["ready"] = json.loads(line)
            with socket.socket() as client:
                client.settimeout(1)
                try:
                    client.connect(("127.0.0.1", port))
                    client.sendall(b"sandbox-experiment\n")
                    record["reply"] = client.recv(100).decode()
                    record["host_connection_ok"] = True
                except OSError as exc:
                    record["host_connection_ok"] = False
                    record["host_connection_error"] = type(exc).__name__
                    record["host_connection_errno"] = exc.errno
            stdout, stderr = process.communicate(timeout=7)
            record.update(returncode=process.returncode, stdout=stdout, stderr=stderr)
        except Exception as exc:
            record["harness_error"] = str(exc)
        finally:
            reservation.close()
            if unit:
                run(["systemctl", "stop", unit], timeout=6)
                state = run(["systemctl", "show", unit, "--property=ActiveState", "--value"])
                record["unit_final_state"] = state.stdout.strip()
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=3)
        record["passed"] = record.get("host_connection_ok") == shared and record.get("returncode") == 0
        records.append(record)
        print(json.dumps(record), flush=True)
    result = {"tests": records, "passed": sum(item["passed"] for item in records), "total": len(records)}
    (ROOT / "results/reverse.json").write_text(json.dumps(result, indent=2))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())

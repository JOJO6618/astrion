#!/usr/bin/env python3
"""Smoke-test the Astrion shell launcher and Bun TUI against an isolated Rust Gateway."""

import argparse
import fcntl
import json
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import tempfile
import termios
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def read_json(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=1) as response:
        return json.load(response)


def parent(pid):
    result = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdecimal() else None


def smoke(runtime):
    binary = (runtime / "astrion-headless").resolve(strict=True)
    assert (runtime / "config/providers_catalog.json").is_file()
    with tempfile.TemporaryDirectory(prefix="astrion-cli-1.0-") as temporary:
        root = Path(temporary).resolve()
        workspace = root / "workspace"
        data = root / "host/data"
        config = root / "config"
        logs = root / "host/logs"
        for path in (data, config, logs, workspace):
            path.mkdir(parents=True, exist_ok=True)
        workspace_id = "cli-acceptance"
        registry = config / "host_workspaces.json"
        registry.write_text(json.dumps({
            "workspaces": [{
                "workspace_id": workspace_id,
                "label": "CLI acceptance",
                "path": str(workspace),
            }],
        }))
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        env = {key: value for key, value in os.environ.items() if not key.startswith("ASTRION_")}
        env.update(
            ASTRION_API_PORT=str(port),
            ASTRION_DATA_ROOT=str(root),
            DATA_DIR=str(data),
            DEPLOY_CONFIG_DIR=str(config),
            HOST_WORKSPACES_FILE=str(registry),
            LOGS_DIR=str(logs),
            ASTRION_RUST_BACKEND_BIN=str(binary),
            ASTRION_RUST_RESOURCE_ROOT=str(runtime),
            TERM="xterm-256color",
        )
        terminal, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))

        command = [str(REPO / "cli/bin/astrion")]
        with subprocess.Popen(
            command, cwd=workspace, env=env, stdin=slave, stdout=slave, stderr=slave,
        ) as cli:
            os.close(slave)
            backend_pid = None
            output = bytearray()
            try:
                for _ in range(150):
                    if cli.poll() is not None:
                        break
                    if select.select([terminal], [], [], 0.15)[0]:
                        chunk = os.read(terminal, 65536)
                        output.extend(chunk)
                    try:
                        host = read_json(f"{origin}/api/host-mode-enabled")
                        if host.get("backend") != "rust" or parent(host["pid"]) != cli.pid:
                            raise AssertionError("The listener is not a Rust child of this CLI")
                        backend_pid = host["pid"]
                        headers = {
                            "Authorization": "Bearer " + (data / "host_api_token").read_text().strip(),
                            "X-Astrion-Workspace-Id": workspace_id,
                        }
                        sessions = read_json(f"{origin}/api/runtime/sessions", headers)
                        if sessions.get("conversations"):
                            break
                    except (OSError, ValueError):
                        continue
                else:
                    raise AssertionError("CLI did not create a session before timeout")
                if not backend_pid or cli.poll() is not None:
                    raise AssertionError(f"CLI failed to start: {output[-3000:]!r}")
                assert sessions["total"] == 1
                assert sessions["conversations"][0]["id"].startswith("conv_")
                print(json.dumps({
                    "cliPid": cli.pid,
                    "backendPid": backend_pid,
                    "backend": host["backend"],
                    "binary": str(binary),
                    "workspace": workspace_id,
                    "sessions": sessions["total"],
                    "sessionCreatedByCli": True,
                }))
            finally:
                os.close(terminal)
                if backend_pid:
                    try:
                        os.kill(backend_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    for _ in range(50):
                        try:
                            read_json(f"{origin}/api/host-mode-enabled")
                        except OSError:
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Isolated Rust Gateway did not stop")
                if cli.poll() is None:
                    cli.kill()
                    cli.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path, help="the staged Rust runtime directory")
    smoke(parser.parse_args().runtime.resolve())

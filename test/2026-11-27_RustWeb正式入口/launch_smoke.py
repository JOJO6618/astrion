#!/usr/bin/env python3
"""Launch normal source commands through the real Rust Host with isolated data."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler

REPO = Path(__file__).resolve().parents[2]
REQUESTS = build_opener(ProxyHandler({}))

COMMANDS = (
    ("main.py", [sys.executable, "-B", "main.py"]),
    ("python -m server.app", [sys.executable, "-B", "-m", "server.app"]),
    ("python server/app.py", [sys.executable, "-B", "server/app.py"]),
    ("web_server.py", [sys.executable, "-B", "web_server.py"]),
    ("start.sh", ["bash", "start.sh"]),
)


def reserve_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def get(origin, path, headers=None):
    return REQUESTS.open(Request(origin + path, headers=headers or {}), timeout=1)


def start(label, command, binary):
    with tempfile.TemporaryDirectory(prefix="astrion-host-web-1.0-") as temporary:
        root = Path(temporary).resolve()
        project = root / "workspace"
        project.mkdir()
        deploy = root / "config"
        deploy.mkdir()
        registry = deploy / "host_workspaces.json"
        registry.write_text(json.dumps({
            "workspaces": [{
                "workspace_id": "isolated-source-web",
                "label": "Source Web acceptance",
                "path": str(project),
            }],
        }))
        port = reserve_port()
        (root / "settings.json").write_text(json.dumps({
            "server": {"port": port, "host": "127.0.0.1"},
        }))
        data = root / "host/data"
        environment = {
            name: value for name, value in os.environ.items()
            if not name.startswith(("ASTRION_", "WEB_", "HOST_WORKSPACES", "HOST_SANDBOX", "AGENT_CFG_"))
            and name not in {"DATA_DIR", "LOGS_DIR", "DEPLOY_CONFIG_DIR", "LINUX_SAFETY", "TERMINAL_SANDBOX_MODE"}
        }
        environment.update(
            ASTRION_IGNORE_DOTENV="1",
            ASTRION_DATA_ROOT=str(root),
            ASTRION_RUST_BACKEND_BIN=str(binary),
            ASTRION_RUST_RESOURCE_ROOT=str(REPO),
            PYTHONDONTWRITEBYTECODE="1",
        )
        origin = f"http://127.0.0.1:{port}"
        log_path = root / "launcher.log"
        with log_path.open("w") as output:
            process = subprocess.Popen(
                [*command, "--path", str(project)], cwd=REPO, env=environment,
                stdout=output, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"{label} exited early\n{log_path.read_text()}")
                    try:
                        with get(origin, "/api/host-mode-enabled") as response:
                            health = json.load(response)
                        if health:
                            break
                    except URLError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError(f"{label} did not listen\n{log_path.read_text()}")
                assert health.get("backend") == "rust", health
                if sys.platform != "win32":
                    assert health.get("pid") == process.pid, health
                with get(origin, "/") as response:
                    html = response.read().decode()
                    assert response.status == 200
                    assert response.headers["Cache-Control"] == "no-store"
                    assert "/assets/" in html or "/static/dist/assets/" in html
                token = (data / "host_api_token").read_text().strip()
                with get(origin, "/api/runtime/sessions", {
                    "Authorization": f"Bearer {token}",
                    "X-Astrion-Workspace-Id": "isolated-source-web",
                }) as response:
                    assert response.status == 200
                process.send_signal(signal.SIGTERM)
                assert process.wait(timeout=10) == 0, log_path.read_text()
                print(json.dumps({"entry": label, "backend": health["backend"], "pid": health.get("pid")}))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    binary = parser.parse_args().binary.resolve(strict=True)
    for label, command in COMMANDS:
        start(label, command, binary)


if __name__ == "__main__":
    main()

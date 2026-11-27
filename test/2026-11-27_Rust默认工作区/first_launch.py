#!/usr/bin/env python3
"""Exercise the production Rust Web bootstrap with synthetic homes and data."""

from contextlib import contextmanager
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[2]
HTTP = build_opener(ProxyHandler({}))


def get_json(base, path, *, cookies=None, csrf=None, payload=None):
    headers = {}
    if cookies is not None:
        headers["Cookie"] = cookies
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    if payload is not None:
        headers["Content-Type"] = "application/json"
    body = None if payload is None else json.dumps(payload).encode()
    with HTTP.open(Request(base + path, data=body, headers=headers), timeout=3) as response:
        return json.load(response), response.headers


def login(base):
    challenge, headers = get_json(base, "/api/csrf-token")
    cookies = headers["Set-Cookie"].split(";", 1)[0]
    status, headers = get_json(
        base, "/host-login", cookies=cookies, csrf=challenge["token"], payload={}
    )
    assert status["success"], status
    cookies = headers["Set-Cookie"].split(";", 1)[0]
    session, _ = get_json(base, "/api/csrf-token", cookies=cookies)
    return status["workspace_required"], cookies, session["token"]


@contextmanager
def host(binary, root, *, web=True):
    args = [str(binary), "--workspace", str(root / "home"), "--data-dir", str(root / "data"),
            "--port", "0"]
    if web:
        args += ["--web", "--static-root", str(ROOT / "static")]
    # Never load dotenv, the usual registry, or the real home.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root / "home"),
        "ASTRION_RESOURCE_ROOT": str(ROOT),
        "HOST_WORKSPACES_FILE": str(root / "workspaces.json"),
        "ASTRION_DESKTOP_STOP_STDIN": "1",
    }
    log = root / "host.log"
    with log.open("wb") as output:
        with subprocess.Popen(
            args, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=output,
            stderr=subprocess.STDOUT,
        ) as process:
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    content = log.read_text(errors="replace")
                    assert process.poll() is None, content
                    address = re.search(r"listening on (127.0.0.1:\d+)", content)
                    if address:
                        yield "http://" + address.group(1)
                        break
                    time.sleep(.025)
                else:
                    raise AssertionError(log.read_text(errors="replace"))
            finally:
                process.stdin.close()
                try:
                    assert process.wait(timeout=10) == 0, log.read_text(errors="replace")
                except subprocess.TimeoutExpired:
                    process.kill()
                    raise


def run(binary, scratch):
    root = scratch / "fresh"
    desktop = root / "home" / "Desktop"
    desktop.mkdir(parents=True)
    registry = root / "workspaces.json"
    with host(binary, root) as base:
        required, _, _ = login(base)
        assert not required
        default = desktop / "Astrion" / "Default"
        assert default.is_dir()
        saved = json.loads(registry.read_bytes())
        assert saved["default_workspace_id"] == "default"
        assert saved["auto_default_workspace"] is True
        assert saved["workspaces"] == [{
            "workspace_id": "default", "label": "Default", "path": str(default),
        }]
        marker = default / "keep.txt"
        marker.write_text("workspace content")
    original = registry.read_bytes()
    with host(binary, root) as base:
        required, cookies, csrf = login(base)
        assert not required and registry.read_bytes() == original
        result, _ = get_json(
            base, "/api/host/workspaces/delete", cookies=cookies, csrf=csrf,
            payload={"workspace_id": "default"}
        )
        assert result["success"], result
        assert marker.read_text() == "workspace content"
    opted_out = registry.read_bytes()
    with host(binary, root) as base:
        assert login(base)[0] and registry.read_bytes() == opted_out
        assert json.loads(opted_out)["workspaces"] == []

    root = scratch / "existing"
    home = root / "home"
    custom = home / "my-project"
    custom.mkdir(parents=True)
    registry = root / "workspaces.json"
    registry.write_text(json.dumps({
        "default_workspace_id": "my-project",
        "workspaces": [{"workspace_id": "my-project", "path": str(custom)}],
        "preserve_unknown_fields": True,
    }))
    original = registry.read_bytes()
    with host(binary, root) as base:
        assert not login(base)[0]
        assert registry.read_bytes() == original and not (home / "Astrion").exists()

    root = scratch / "fallback"
    home = root / "home"
    home.mkdir(parents=True)
    with host(binary, root) as base:
        assert not login(base)[0] and (home / "Astrion" / "Default").is_dir()

    root = scratch / "creation-error"
    home = root / "home"
    home.mkdir(parents=True)
    (home / "Astrion").write_text("a file, not a directory")
    with host(binary, root) as base:
        assert login(base)[0] and not (root / "workspaces.json").exists()

    root = scratch / "headless"
    home = root / "home"
    home.mkdir(parents=True)
    with host(binary, root, web=False) as base:
        health, _ = get_json(base, "/api/host-mode-enabled")
        assert health["backend"] == "rust"
        assert not (home / "Astrion").exists() and not (root / "workspaces.json").exists()


if __name__ == "__main__":
    if os.name != "posix":
        raise SystemExit("This fixture emulates a POSIX home. Verify Windows Desktop/OneDrive on a Windows test account.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    with tempfile.TemporaryDirectory(prefix="astrion-default-") as temporary:
        run(parser.parse_args().binary.resolve(strict=True), Path(temporary).resolve())
    print("Rust Web default workspace lifecycle passed")

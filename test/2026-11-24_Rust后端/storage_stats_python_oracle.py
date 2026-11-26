"""Run the Python storage blueprint against copies of a Rust test fixture."""

import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from types import ModuleType

from flask import Flask


def copy_fixture(source: Path, target: Path):
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Invalid test fixture root {source}")
    for parent, directories, files in os.walk(source, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            entry = path.lstat()
            if not (stat.S_ISDIR(entry.st_mode) or (stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1)):
                raise ValueError(f"Unexpected fixture entry {path}")
    shutil.copytree(source, target, symlinks=True)


def run(fixture: Path):
    data = fixture / "python-data"
    logs = fixture / "python-logs"
    copy_fixture(fixture / "data", data)
    copy_fixture(fixture / "logs", logs)

    # The blueprint and filesystem modules use these injected paths and never
    # import the application's real configuration or resolve its data roots.
    config = ModuleType("config")
    config.DATA_DIR = str(data)
    config.LOGS_DIR = str(logs)
    config.IS_HOST_MODE = True
    sys.modules["config"] = config
    auth = ModuleType("server.auth_helpers")
    auth.api_login_required = lambda handler: handler
    sys.modules["server.auth_helpers"] = auth
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from server import storage_stats

    app = Flask(__name__)
    app.register_blueprint(storage_stats.storage_stats_bp)
    client = app.test_client()

    def fetch_get():
        return client.get("/api/storage-stats").get_json()

    def post(path, **kwargs):
        return client.post(f"/api/storage-stats/{path}", **kwargs)

    def wait_for_scan():
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = fetch_get()
            snapshot = response["data"]
            if not snapshot["scanning"] and snapshot["scanned_at"]:
                return response
            time.sleep(0.01)
        raise AssertionError("Python scan did not finish")

    first = fetch_get()
    stats = wait_for_scan()
    refresh = post("refresh").get_json()
    wait_for_scan()
    with (logs / "main.log").open("ab") as writer:
        clean_logs = post("clean/logs").get_json()
        writer.write(b"same-handle append")
    wait_for_scan()
    clean_backups = post("clean/backups", json={"keep_days": "30"}).get_json()
    wait_for_scan()
    bad_days = post("clean/backups", json={"keep_days": 0})
    storage_stats.IS_HOST_MODE = False
    return {
        "first": first,
        "stats": stats,
        "refresh": refresh,
        "clean_logs": clean_logs,
        "clean_backups": clean_backups,
        "bad_days": [bad_days.status_code, bad_days.get_json()],
        "unsupported": fetch_get(),
        "continued_log": (logs / "main.log").read_text(),
    }


if __name__ == "__main__":
    fixture = Path(sys.argv[1]).resolve(strict=True)
    suite = Path(__file__).resolve().parent
    if fixture.parent != suite or not fixture.name.startswith("storage_stats_tmp_"):
        raise SystemExit("The Python oracle requires a local storage stats fixture")
    print(json.dumps(run(fixture)))

"""Run with python3 -B http_contract.py /path/to/astrion-headless."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TEST_DIR = Path(__file__).resolve().parent
REPO = TEST_DIR.parents[1]
ID = "conv_20261125_143300_000"
UNKNOWN = "conv_20261125_143300_001"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def expected_from_python(data, registry, workspace, children):
    # Load production implementations against only the isolated Host fixture.
    config = ModuleType("config")
    config.DATA_DIR = str(data)
    config.LOGS_DIR = str(data.parent / "logs")
    config.HOST_WORKSPACES_FILE = str(registry)
    sys.modules["config"] = config
    sys.modules["config.paths"] = config
    sys.path.insert(0, str(REPO))
    from modules.conversation_costs import summarize
    from modules.exchange_rates import get_rate
    from utils.conversation_manager import ConversationManager

    manager = ConversationManager(base_dir=data, project_path=workspace, read_only=True)
    stats = manager.get_token_statistics(ID)
    assert stats is not None
    stats["costs"] = summarize(data, ID, main_input=stats["total_input_tokens"], sub_agents=children)
    stats["costs"]["exchange_rate"] = get_rate(refresh=False)
    return {"success": True, "data": stats}


def test(binary, root):
    data, workspace_a, workspace_b, assets = (root / name for name in ("data", "a", "b", "static"))
    for directory in (workspace_a, workspace_b, assets):
        directory.mkdir()
    registry = write(data / "host_workspaces.json", {
        "default_workspace_id": "ws-a",
        "workspaces": [
            {"workspace_id": "ws-a", "path": str(workspace_a)},
            {"workspace_id": "ws-b", "path": str(workspace_b)},
        ],
    })
    conversation = write(data / "conversations/ws-a" / f"{ID}.json", {
        "id": ID,
        "metadata": {"project_path": str(workspace_a), "original": "keep this"},
        "messages": [],
        "token_statistics": {
            "total_input_tokens": " +001_024 ", "total_output_tokens": 88.6,
            "total_cached_input_tokens": "19", "cache_exempt_input_tokens": False,
            "current_context_tokens": 120.9, "updated_at": "2026-11-22T09:30:01.000000",
        },
    })
    index = write(data / "conversations/ws-a/index.json", {ID: {"title": "Legacy", "unknown": 1}})
    children = [
        {"task_id": "sub_legacy", "conversation_id": ID, "summary": "Historical"},
        {"task_id": "sub_tracked", "conversation_id": ID, "display_name": "Tracked", "cost_tracking_version": 1},
    ]
    child_registry = write(data / "sub_agent_runtime.json", {
        "version": 1,
        "tasks": {child["task_id"]: child for child in children},
    })
    actors = {"review_agent:goal_review": {
        "id": "review_agent:goal_review", "name": "goal_review", "kind": "review_agent",
        "cost_usd": "0.03725", "requests": 1, "priced_requests": 1, "unpriced_requests": 0,
    }}
    ledger = write(data / "api_costs" / f"{ID}.json", {"actors": actors, "requests": {}, "version": 1})
    orphan = write(data / "api_costs" / f"{UNKNOWN}.json", {"actors": actors, "requests": {}, "version": 1})
    cache = write(data / "exchange_rates.json", {
        "usd_cny": 7.1, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rate_date": "2026-11-24", "source": "fixture",
    })
    files = (conversation, index, child_registry, ledger, orphan, cache)
    original = [file.read_bytes() for file in files]
    expected = expected_from_python(data, registry, workspace_a, children)
    assert [file.read_bytes() for file in files] == original

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {name: os.environ[name] for name in ("PATH", "SystemRoot") if name in os.environ}
    env.update(
        ASTRION_RESOURCE_ROOT=str(REPO), ASTRION_DATA_ROOT=str(root),
        HOST_WORKSPACES_FILE=str(registry), DEPLOY_CONFIG_DIR=str(root / "config"),
        LOGS_DIR=str(root / "logs"), ASTRION_IGNORE_DOTENV="1",
        ASTRION_DESKTOP_STOP_STDIN="1",
    )
    with subprocess.Popen([
        str(binary), "--web", "--port", str(port), "--workspace", str(workspace_a),
        "--data-dir", str(data), "--static-root", str(assets),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as process:
        def request(path, status, *, headers=None, body=None):
            request_headers = {"Content-Type": "application/json", **(headers or {})}
            payload = json.dumps(body).encode() if body is not None else None
            req = Request(origin + path, data=payload, headers=request_headers)
            try:
                reply = urlopen(req, timeout=3)
            except HTTPError as error:
                reply = error
            with reply:
                raw = reply.read()
                assert reply.code == status, (path, reply.code, status, raw)
                return json.loads(raw), reply.headers

        try:
            for _ in range(150):
                assert process.poll() is None, process.stderr.read().decode() if process.poll() is not None else ""
                try:
                    challenge, headers = request("/api/csrf-token", 200)
                    break
                except URLError:
                    time.sleep(0.02)
            else:
                raise AssertionError("Rust listener did not become ready")
            path = f"/api/conversations/{ID}/token-statistics"
            request(path, 401)
            token = (data / "host_api_token").read_text().strip()
            request(path, 401, headers={"Authorization": f"Bearer {token}"})
            initial_cookie = headers["Set-Cookie"].split(";", 1)[0]
            _, headers = request("/host-login", 200, body={}, headers={
                "Cookie": initial_cookie, "Origin": origin, "X-CSRF-Token": challenge["token"],
            })
            auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
            assert request(path, 200, headers=auth)[0] == expected
            assert request(path.replace(ID, ID.removeprefix("conv_")), 200, headers=auth)[0] == expected
            for refused, selector in [(path, {"X-Astrion-Workspace-Id": "ws-b"}), (path.replace(ID, UNKNOWN), {})]:
                reply, _ = request(refused, 404, headers={**auth, **selector})
                assert reply["success"] is False and "data" not in reply
            assert [file.read_bytes() for file in files] == original
        finally:
            process.stdin.close()
            assert process.wait(timeout=5) == 0, process.stderr.read().decode()


if __name__ == "__main__":
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(dir=TEST_DIR, prefix="http-fixture-") as directory:
        test(binary, Path(directory))
    print("PASS: authenticated Rust token statistics match Python and preserve stored files")

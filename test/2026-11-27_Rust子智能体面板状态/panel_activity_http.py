"""Round-trip authenticated child activity into both production Vue timelines."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-11-27_Rust子智能体环境"))
from child_round_http import (Browser, Model, ROOT, call, child, fixture_environment,
                              model_catalog_host, wait_for)

PROMPT = "Create the panel fixture child."
CHILD_TASK = "Read the fixture and wait for the human."
QUESTION = "This fixture has no automatic answer."


class Provider(Model):
    def do_POST(self):
        try:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = request["messages"]
            user = [m["content"] for m in messages if m["role"] == "user"]
            tools = {m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"}
            if CHILD_TASK in user[0]:
                if "read" not in tools:
                    calls = [call("read_file", "read", {
                        "path": "fixture.txt", "type": "read", "start_line": 1, "end_line": 2,
                    })]
                else:
                    assert "second line" in tools["read"], tools
                    calls = [call("ask_master", "blocking", {"question": QUESTION, "timeout_seconds": 30})]
            elif not tools and user[-1] == PROMPT:
                calls = [child("panel-fixture", CHILD_TASK)]
            else:
                calls = []
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if calls:
                self.delta({"tool_calls": [{**part, "index": i} for i, part in enumerate(calls)]}, "tool_calls")
            else:
                self.delta({"content": "Fixture complete."}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
        except (AssertionError, KeyError, IndexError, BrokenPipeError) as error:
            self.server.errors.append(str(error))
            self.close_connection = True


def run():
    binary = Path(os.environ.get("ASTRION_HEADLESS_BIN", ROOT / "backend-rust/target/debug/astrion-headless"))
    with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(("127.0.0.1", 0), Provider) as provider:
        root = Path(temporary)
        env = fixture_environment(root)
        env["ASTRION_RESOURCE_ROOT"] = str(ROOT)
        data = Path(env["DATA_DIR"])
        data.mkdir()
        (data / "personalization.json").write_text(json.dumps({
            "auto_generate_title": False, "skill_strict_sub_agent_enabled": False,
        }))
        (Path(env["HOST_PROJECT_PATH"]) / "fixture.txt").write_text("first line\nsecond line\n")
        (Path(env["DEPLOY_CONFIG_DIR"]) / "custom_models.json").write_text(json.dumps({"models": [{
            "model_name": "chat-local", "model_id": "chat-local",
            "url": f"http://127.0.0.1:{provider.server_port}/v1", "apikey": "fixture-only",
        }]}))
        provider.errors = []
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary.resolve(strict=True), env, root / "host.log") as address:
                browser = Browser(address)
                settings = {"model_key": "chat-local", "work_mode": "execute", "permission_mode": "approval",
                            "execution_mode": "sandbox", "run_mode": "fast", "network_permission": "restricted",
                            "reasoning_effort": None, "thinking_mode": False}
                conversation = browser.api("POST", "/api/conversations?workspace_id=default",
                                           {"settings": settings}, status=201)["conversation_id"]
                accepted = browser.api("POST", "/api/tasks?workspace_id=default", {
                    "message": PROMPT, "model_key": "chat-local", "conversation_id": conversation,
                }, status=202)
                cid = accepted["data"]["conversation_id"]
                scope = f"?workspace_id=default&conversation_id={cid}"
                base = "/api/sub_agents"
                def agent():
                    rows = browser.api("GET", base + scope)["data"]
                    return next((row for row in rows if row.get("name") == "panel-fixture"), None)
                record = wait_for(agent, "Child was not registered")
                route = f"{base}/{record['task_id']}"
                def snapshot():
                    return browser.api("GET", route + "/activity" + scope)["data"]
                def is_running():
                    rows = snapshot()["entries"]
                    return rows if any(row.get("id") == "blocking" and row.get("status") == "running" for row in rows) else None
                live = wait_for(is_running, "No blocking tool in child activity")
                assert any(row.get("id") == "read" and row.get("args", {}).get("path") == "fixture.txt" for row in live), live
                assert browser.api("POST", route + "/stop" + scope, {})["success"]
                def final():
                    page = snapshot()
                    return page if page["status"] == "idle" and any(row.get("is_final") for row in page["entries"]) else None
                page = wait_for(final, "Stopped round did not persist its final activity")
                rows = page["entries"]
                stopped = [row for row in rows if row.get("id") == "blocking"]
                assert stopped[-1]["status"] == "cancelled", stopped
                assert rows[-1]["is_final"] and "可发送消息继续" in rows[-1]["content"], rows[-1]
                assert not provider.errors, provider.errors
                captured = root / "activity.json"
                captured.write_text(json.dumps(rows))
                subprocess.run(["node", "--test", str(HERE / "panel_timeline.test.mjs")],
                               cwd=ROOT, env={**os.environ, "ASTRION_ACTIVITY_FIXTURE": str(captured)}, check=True)
        finally:
            provider.shutdown()
            worker.join()


if __name__ == "__main__":
    run()

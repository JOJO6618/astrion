"""Check first model prompt, streamed child activity, and natural sibling replies."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-11-27_RustAgent工具对照"))
from knowledge_media_http import Browser, LocalServices, ROOT, call
from knowledge_media_http import fixture_environment, model_catalog_host, stored_document, wait_for_task

PROMPT = "Delegate to two test workers."
ASKER = "Your task is to ask the answerer."
ANSWERER = "You are the answerer of this test."
QUESTION = "What word did the fixture choose?"
ANSWER = "The fixture chose indigo."
GUIDE_ID = "sub-agent-guide"
GUIDE = f".astrion/skills/{GUIDE_ID}/SKILL.md"
MARKER = "This is the end of the installed fixture guide."


def child(name, task):
    return call("create_sub_agent", f"create-{name}", {
        "name": name, "summary": name, "task": task, "working_dir": ".",
        "intelligence": "same", "access_level": "workspace_write",
    })


class Model(LocalServices):
    def do_POST(self):
        try:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = request["messages"]
            user = [m["content"] for m in messages if m["role"] == "user"]
            results = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}

            if ASKER in user[0] or ANSWERER in user[0]:
                system = messages[0]["content"]
                assert "## 执行环境" in system and "- 当前时间: 20" in system, system
                for slot in ("{execution_env_section}", "{system_info}", "{current_time}", "{workspace_path}"):
                    assert slot not in system, system
                assert str(self.server.workspace) in system, system

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if ASKER in user[0]:
                if "ask-streamed" not in results:
                    # Providers can deliver the function name one chunk before
                    # the ID. Do not finish the model response until HTTP has
                    # independently observed the pending tool activity.
                    self.delta({"tool_calls": [{"index": 0, "function": {"name": "ask_other_agent"}}]})
                    self.delta({"tool_calls": [{"index": 0, "id": "ask-streamed", "type": "function",
                                                "function": {"arguments": '{"name":'}}]})
                    self.server.model_streamed.set()
                    assert self.server.check_calling.wait(8), "No live calling event observed"
                    arguments = '"answerer","question":' + json.dumps(QUESTION) + ',"timeout_seconds":4}'
                    self.delta({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}, "tool_calls")
                else:
                    expected = results["ask-streamed"]
                    assert expected["success"] and expected["answer"] == ANSWER, expected
                    self.server.answered.set()
                    self.delta({"content": "I received the answer."}, "stop")
            elif ANSWERER in user[0]:
                # A successfully completed task can naturally answer without
                # an explicit answer_other_agent call.
                self.delta({"content": ANSWER if any(QUESTION in m for m in user) else "Ready."}, "stop")
            elif PROMPT in user[0]:
                self.main(results)
            else:
                assert all(value["success"] for key, value in results.items() if key.startswith("create-")), results
                self.delta({"content": "Delegated."}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
        except (AssertionError, KeyError, BrokenPipeError) as error:
            self.server.errors.append(str(error))
            self.close_connection = True

    def delta(self, body, finish=None):
        payload = {"choices": [{"index": 0, "delta": body, "finish_reason": finish}]}
        self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
        self.wfile.flush()

    def main(self, results):
        if not results:
            calls = [call("get_sub_agent_status", "blocked", {})]
        elif "partial" not in results:
            assert not results["blocked"]["success"] and GUIDE in results["blocked"]["error"], results
            calls = [
                call("read_file", "partial", {"path": GUIDE, "type": "read", "start_line": 1, "end_line": 1}),
                call("get_sub_agent_status", "still-blocked", {}),
            ]
        elif "guide" not in results:
            assert results["partial"]["success"] and not results["still-blocked"]["success"], results
            calls = [call("read_skill", "guide", {"skill_name": GUIDE_ID}),
                     child("answerer", ANSWERER), child("asker", ASKER)]
        else:
            assert MARKER in results["guide"]["content"], results
            assert all(results[key]["success"] for key in ("create-answerer", "create-asker")), results
            self.delta({"content": "Delegated."}, "stop")
            return
        self.delta({"tool_calls": [{**tool, "index":i} for i, tool in enumerate(calls)]}, "tool_calls")


def wait_for(get, why):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = get()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError(why)


def run():
    binary = Path(os.environ.get("ASTRION_HEADLESS_BIN", ROOT / "backend-rust/target/debug/astrion-headless"))
    with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(("127.0.0.1", 0), Model) as model:
        root = Path(temporary)
        env = fixture_environment(root)
        env["ASTRION_RESOURCE_ROOT"] = str(ROOT)
        data = Path(env["DATA_DIR"])
        data.mkdir()
        (data / "personalization.json").write_text(json.dumps({
            "auto_generate_title": False, "skill_strict_sub_agent_enabled": True,
        }))
        model.workspace = Path(env["HOST_PROJECT_PATH"]).resolve()
        model.model_streamed = threading.Event()
        model.check_calling = threading.Event()
        model.answered = threading.Event()
        model.errors = []
        origin = f"http://127.0.0.1:{model.server_port}"
        (Path(env["DEPLOY_CONFIG_DIR"]) / "custom_models.json").write_text(json.dumps({"models": [{
            "model_name": "chat-local", "model_id": "chat-local", "url": origin + "/v1",
            "apikey": "fixture-only",
        }]}))
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        try:
            with model_catalog_host(binary.resolve(strict=True), env, root / "host.log") as address:
                browser = Browser(address)
                guide = model.workspace / GUIDE
                guide.parent.mkdir(parents=True, exist_ok=True)
                guide.write_text(f"---\nname: {GUIDE_ID}\n---\n{MARKER}\n", encoding="utf-8")
                settings = {"model_key":"chat-local", "work_mode":"execute", "permission_mode":"approval",
                            "execution_mode":"sandbox", "run_mode":"fast", "network_permission":"restricted",
                            "reasoning_effort":None, "thinking_mode":False}
                conversation = browser.api("POST", "/api/conversations?workspace_id=default",
                                           {"settings": settings}, status=201)["conversation_id"]
                accepted = browser.api("POST", "/api/tasks?workspace_id=default",
                                       {"message": PROMPT, "model_key": "chat-local",
                                        "conversation_id": conversation},
                                       status=202)
                cid = accepted["data"]["conversation_id"]
                query = f"?workspace_id=default&conversation_id={cid}"
                def asker():
                    return next((row for row in browser.api("GET", "/api/sub_agents" + query)["data"]
                                 if row.get("name") == "asker" or row.get("display_name") == "asker"), None)
                record = wait_for(asker, f"Child was not registered: {model.errors}")
                def activity():
                    reply = browser.api("GET", f"/api/sub_agents/{record['task_id']}/activity" + query)
                    return [row for row in reply["data"]["entries"] if row.get("id") == "ask-streamed"]
                assert model.model_streamed.wait(8), model.errors
                preparing = wait_for(activity, "No child activity before tool execution")
                assert [row["status"] for row in preparing] == ["calling"], preparing
                model.check_calling.set()
                assert model.answered.wait(8), model.errors
                events = wait_for(lambda: rows if len(rows := activity()) >= 3 else None, "Missing execution events")
                assert [row["status"] for row in events] == ["calling", "running", "done"], events
                assert events[1]["args"]["question"] == QUESTION and events[2]["result"]["answer"] == ANSWER, events
                assert wait_for_task(browser, accepted) == cid
                document = stored_document(data / "conversations/default/conversations.sqlite3", cid)
                assert document["metadata"]["skill_read_sub_agent_guide"] is True
                assert not model.errors, model.errors
        finally:
            model.check_calling.set()
            model.shutdown()
            thread.join()
    print("Strict guide, child prompt, late-ID preparing/running/done and sibling answer fallback verified.")


if __name__ == "__main__":
    run()

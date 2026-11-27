"""Exercise authenticated Web compose, persisted history and model replay."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "2026-11-27_RustAgent工具对照"))
from knowledge_media_http import Browser, LocalServices, model_catalog_host
from knowledge_media_http import ROOT, fixture_environment, stored_document, wait_for_task

SKILL = ".astrion/skills/composer-fixture/SKILL.md"
SECOND = ".astrion/skills/second-fixture/SKILL.md"
BODY = "---\nname: Trusted fixture name\ndescription: Smoke test\n---\nFull composer instructions.\n"
NOTICE = "[系统通知|skill]\n" + BODY
SECOND_NOTICE = "[系统通知|skill]\nSecond selected instructions."


class Model(LocalServices):
    def calls(self, request):
        users = [message["content"] for message in request["messages"] if message["role"] == "user"]
        self.server.inputs.append(users)
        return "Local model accepted the turn."


def run():
    binary = Path(os.environ.get(
        "ASTRION_HEADLESS_BIN", ROOT / "backend-rust/target/debug/astrion-headless"
    )).resolve(strict=True)
    with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(("127.0.0.1", 0), Model) as model:
        root = Path(temporary)
        env = fixture_environment(root)
        env["ASTRION_RESOURCE_ROOT"] = str(ROOT)
        data = Path(env["DATA_DIR"])
        workspace = Path(env["HOST_PROJECT_PATH"])
        data.mkdir()
        (data / "personalization.json").write_text(json.dumps({"auto_generate_title": False}))
        model.inputs = []
        model.errors = []
        origin = f"http://127.0.0.1:{model.server_port}"
        (Path(env["DEPLOY_CONFIG_DIR"]) / "custom_models.json").write_text(json.dumps({"models": [{
            "model_name": "chat-local", "model_id": "chat-local", "url": origin + "/v1",
            "apikey": "fixture-only",
        }]}))
        worker = threading.Thread(target=model.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary, env, root / "host.log") as address:
                browser = Browser(address)
                for relative, contents in [(SKILL, BODY), (SECOND, "Second selected instructions.")]:
                    skill = workspace / relative
                    skill.parent.mkdir(parents=True, exist_ok=True)
                    skill.write_text(contents, encoding="utf-8")
                scope = "?workspace_id=default"
                menu = browser.api("GET", "/api/skills" + scope)
                chosen = next(skill for skill in menu["skills"] if skill["path"] == SKILL)
                choices = [chosen, {"path": str(workspace / SKILL), "name": "Not trusted metadata"},
                           {"path": SECOND.replace("/", "\\"), "name": "Also selected"}]
                settings = {"model_key":"chat-local", "work_mode":"execute", "permission_mode":"approval",
                            "execution_mode":"sandbox", "run_mode":"fast", "network_permission":"restricted",
                            "reasoning_effort":None, "thinking_mode":False}
                cid = browser.api("POST", "/api/conversations" + scope, {"settings": settings},
                                  status=201)["conversation_id"]
                first = {"message": "Use my selected skills.", "model_key": "chat-local",
                         "conversation_id": cid}
                for invalid in (
                    "not a reference list", [{"name": "no path"}],
                    [{"path": ".astrion/skills/missing/SKILL.md"}],
                ):
                    failed = browser.api("POST", "/api/tasks" + scope,
                                         {**first, "skill_refs": invalid}, status=400)
                    assert not failed["success"], failed
                path = data / "conversations/default/conversations.sqlite3"
                assert not stored_document(path, cid)["messages"]
                assert not model.inputs

                accepted = browser.api("POST", "/api/tasks" + scope,
                                       {**first, "skill_refs": choices}, status=202)
                assert wait_for_task(browser, accepted) == cid
                assert model.inputs == [[first["message"], NOTICE, SECOND_NOTICE]], model.inputs
                history = stored_document(path, cid)["messages"]
                notices = [m for m in history if (m.get("metadata") or {}).get("source") == "skill"]
                assert [m["content"] for m in notices] == [NOTICE, SECOND_NOTICE], history
                assert notices[0]["metadata"]["skill_name"] == chosen["name"], notices[0]
                assert [m["metadata"]["skill_path"] for m in notices] == [SKILL, SECOND]
                assert all(m["metadata"]["hidden"] and m["metadata"]["visibility"] == "hidden"
                           and not m["metadata"]["starts_work"] for m in notices)

                followup = {**first, "message": "Continue without selecting a new skill.", "skill_refs": []}
                accepted = browser.api("POST", "/api/tasks" + scope, followup, status=202)
                assert wait_for_task(browser, accepted) == cid
                assert model.inputs[1] == [first["message"], NOTICE, SECOND_NOTICE, followup["message"]], model.inputs
                assert not model.errors, model.errors
        finally:
            model.shutdown()
            worker.join()
    print("Composer Skills reach the model and persist as deduplicated, hidden history.")


if __name__ == "__main__":
    run()

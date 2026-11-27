"""Check opt-in recent dialogue, registered project name and upload metadata."""
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
from knowledge_media_http import Browser, LocalServices, ROOT
from knowledge_media_http import fixture_environment, model_catalog_host, stored_document, wait_for_task

LABEL = "Lock fixture"
SECRET_BODY = "Fixture upload contents should not be in the frozen prompt."
FIRST = "先前对话的首条消息 " + ("可" * 104) + " Exclude the remainder."


class Model(LocalServices):
    def calls(self, body):
        request = body["messages"]
        self.server.prompts.append(request[0]["content"])
        return "Fixture responded."


def run():
    binary = Path(os.environ.get("ASTRION_HEADLESS_BIN", ROOT / "backend-rust/target/debug/astrion-headless"))
    with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(("127.0.0.1", 0), Model) as model:
        root = Path(temporary)
        env = fixture_environment(root)
        env["ASTRION_RESOURCE_ROOT"] = str(ROOT)
        data = Path(env["DATA_DIR"])
        data.mkdir()
        prefs = data / "personalization.json"
        prefs.write_text(json.dumps({"auto_generate_title": False, "recent_conversations_prompt_enabled": False}))
        model.prompts = []
        model.errors = []
        origin = f"http://127.0.0.1:{model.server_port}"
        (Path(env["DEPLOY_CONFIG_DIR"]) / "custom_models.json").write_text(json.dumps({"models": [{
            "model_name": "chat-local", "model_id": "chat-local", "url": origin + "/v1", "apikey": "fixture-only",
        }]}))
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        try:
            with model_catalog_host(binary.resolve(strict=True), env, root / "host.log") as address:
                browser = Browser(address)
                upload = browser.upload("visible-upload.txt", SECRET_BODY.encode(), mime="text/plain")
                assert upload["success"], upload
                scope = "?workspace_id=default"
                settings = {"model_key":"chat-local", "work_mode":"execute", "permission_mode":"approval",
                            "execution_mode":"sandbox", "run_mode":"fast", "network_permission":"restricted",
                            "reasoning_effort":None, "thinking_mode":False}

                def create():
                    return browser.api("POST", "/api/conversations" + scope, {"settings": settings},
                                       status=201)["conversation_id"]

                def turn(cid, text):
                    admitted = browser.api("POST", "/api/tasks" + scope,
                                           {"conversation_id":cid,"message":text,"model_key":"chat-local"}, status=202)
                    assert wait_for_task(browser, admitted) == cid

                previous = create()
                turn(previous, FIRST)
                initial = model.prompts[-1]
                assert f"**项目名称**：{LABEL}" in initial, initial
                assert ".astrion/user_upload/visible-upload.txt" in initial, initial
                assert SECRET_BODY not in initial and "首条用户消息：" not in initial
                doc = stored_document(data / "conversations/default/conversations.sqlite3", previous)
                prefs.write_text(json.dumps({"auto_generate_title":False, "recent_conversations_prompt_enabled":True}))
                current = create()
                turn(current, "New conversation fixture.")
                snapshot = model.prompts[-1]
                assert f"[{previous}]" in snapshot and f"[{current}]" not in snapshot, snapshot
                assert f"首条用户消息：{FIRST[:100]}\n   更新时间：{doc['updated_at']}" in snapshot, snapshot
                assert "Exclude the remainder." not in snapshot
                turn(previous, "Previously frozen prompt should not be regenerated.")
                assert model.prompts[-1] == initial
                assert not model.errors, model.errors
        finally:
            model.shutdown()
            thread.join()
    print("Registered label, metadata-only recent upload and opt-in excerpt appear in the frozen prompt.")


if __name__ == "__main__":
    run()

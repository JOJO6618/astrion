"""Serve built production Chat UI and real task routes with an isolated streaming model.

Run with ASTRION_IGNORE_DOTENV=1 and an ASTRION_DATA_ROOT under this workspace's cache.
No user credentials, execution environment or external model calls are used.
"""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
ROOT = Path(os.environ["ASTRION_DATA_ROOT"]).resolve()
if PROJECT / "cache" not in ROOT.parents or os.environ.get("ASTRION_IGNORE_DOTENV") != "1":
    raise RuntimeError("Browser regression service requires an isolated cache data root")

from flask import Flask, jsonify
from server.chat_mode import chat_mode_bp
from server.chat_mode_scope import ChatScope
from server.entry_assets import serve_entry_html
from server.runtime.context import TrustedPrincipal
from modules.chat_mode.runtime import ChatRuntime
from utils.api_client import APIClient

PROFILE = {"key": "synthetic", "name": "Browser streaming fixture", "api_protocol": "chat",
           "supports_thinking": True, "context_window": 128000,
           "fast": {"model_id": "synthetic"}, "thinking": {"model_id": "synthetic"}}


class BrowserAPI:
    def __init__(self, **kwargs):
        self.api_protocol = "chat"
        self.last_responses_reasoning_items = None
        self.thinking_mode = kwargs.get("thinking_mode", True)
        self.call_count = 0

    def apply_profile(self, profile):
        pass

    def get_current_thinking_mode(self):
        return self.thinking_mode

    def update_context_budget(self, *args):
        pass

    def _safe_tool_arguments_parse(self, arguments, function_name):
        return True, json.loads(arguments), None

    async def chat(self, messages, tools, stream=True):
        self.call_count += 1
        user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        user = user if isinstance(user, str) else "Media received"
        await asyncio.sleep(2)
        for text in ["Checking ", "the message ", "and preparing the reply."]:
            yield {"choices": [{"delta": {"reasoning_content": text}, "finish_reason": None}]}
            await asyncio.sleep(0.35)
        if "tool" in user.lower() and self.call_count == 1:
            yield {"choices": [{"delta": {"content": "Saving the requested preference.", "tool_calls": [
                {"index": 0, "id": "browser_memory", "type": "function", "function": {
                    "name": "update_memory", "arguments": json.dumps({"operation": "append", "content": "Browser fixture preference", "intent": "Saving preference"})}}
            ]}, "finish_reason": None}]}
            return
        answer = "Reply received: " + user
        if "slow" in user.lower():
            answer += " This response deliberately continues so the stop button can be tested." * 4
        for index in range(0, len(answer), 4):
            yield {"choices": [{"delta": {"content": answer[index:index + 4]}, "finish_reason": None}]}
            await asyncio.sleep(0.12)


scope = ChatScope("browser_fixture", ROOT / "chat", ROOT / "preferences")
identity = TrustedPrincipal("browser_fixture", None, conversation_kind="chat")
app = Flask(__name__, static_folder=str(PROJECT / "static"))
app.secret_key = "synthetic-local-browser-regression-only"
app.config["SESSION_COOKIE_NAME"] = "astrion_chat_regression"
app.register_blueprint(chat_mode_bp)


@app.get("/chat")
@app.get("/chat/new")
@app.get("/chat/<conversation_id>")
def page(conversation_id=None):
    return serve_entry_html("index.html")


@app.get("/api/session-status")
def session_status():
    return jsonify(success=True, session={"username": identity.username, "logged_in": True})


@app.get("/api/csrf-token")
def csrf_token():
    return jsonify(success=True, csrf_token="isolated-fixture")


if __name__ == "__main__":
    with ExitStack() as patches:
        for target, value in [
            ("server.auth_helpers.is_logged_in", True),
            ("server.chat_mode.context.principal", identity),
            ("server.chat_mode.context.scope_for_principal", scope),
            ("server.tasks.chat_mode.scope_for_principal", scope),
            ("server.chat_mode_scope.scope_for_principal", scope),
            ("server.chat_mode.settings.get_registered_model_profiles", {"synthetic": PROFILE}),
            ("server.chat_mode.settings.get_default_model_key", "synthetic"),
            ("modules.chat_mode.runtime.get_model_profile", PROFILE),
            ("modules.chat_mode.configuration.get_model_profile", PROFILE),
            ("server.chat_flow_task_main.get_model_profile", PROFILE),
            ("server.chat_flow_task_main.get_model_context_window", 128000),
            ("modules.api_pricing.capture_price", None),
            ("modules.exchange_rates.get_rate", None),
        ]:
            patches.enter_context(patch(target, return_value=value))
        patches.enter_context(patch("modules.chat_mode.runtime.APIClient", BrowserAPI))
        patches.enter_context(patch("server.context.get_user_resources", side_effect=AssertionError("Chat created Agent resources")))
        print(f"Isolated production Chat UI: http://127.0.0.1:8097/chat/new", flush=True)
        app.run(host="127.0.0.1", port=8097, threaded=True, use_reloader=False)

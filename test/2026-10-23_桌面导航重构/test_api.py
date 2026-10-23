"""Execute current Flask handlers with synthetic identity/storage, no application boot."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from flask import Blueprint, Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.desktop_navigation import DesktopNavigation, NavigationConflict


def handlers(path, names, bindings):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    code = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(code, path, "exec"), bindings)
    return bindings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.navigation = DesktopNavigation(str(Path(self.temp.name) / "tabs.json"))
        self.app = Flask(__name__)
        bp = Blueprint("tabs_test", __name__)
        self.lookup = Mock(side_effect=lambda ws, conv: {
            "workspace_id": ws or "ws1", "exists": conv != "conv_missing", "label": ws})
        self.bindings = handlers("server/conversation_tabs.py",
            {"get_conversation_tabs", "command_conversation_tabs"}, {
                "conversation_tabs_bp": bp, "api_login_required": lambda fn: fn,
                "get_current_username": lambda: "synthetic", "IS_HOST_MODE": True,
                "jsonify": jsonify, "request": request, "navigation": self.navigation,
                "NavigationConflict": NavigationConflict,
                "_lookup": lambda *args, **kwargs: self.lookup,
                "_public": lambda user, state: state,
            })
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        # Focus observation is unrelated to tab state and must never touch real runtime.
        focus = SimpleNamespace(focus_tab=Mock())
        self.focus_patch = patch.dict(sys.modules, {"server.crosspet": focus})
        self.focus_patch.start()
        self.addCleanup(self.focus_patch.stop)

    def command(self, action, payload=None, identity=None):
        return self.client.post("/api/conversation-tabs/commands", json={
            "command_id": identity or uuid4().hex, "action": action, "payload": payload or {}})

    def test_get_snapshot_and_reject_legacy_overwrite(self):
        response = self.client.get("/api/conversation-tabs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["version"], 2)
        self.assertEqual(response.json["tabs"], [])
        self.assertEqual(self.client.put("/api/conversation-tabs", json={"tabs": []}).status_code, 405)

    def test_command_and_same_identity_retry(self):
        first = self.command("create", {"workspace_id": "ws1", "key": "tab_A"}, "one")
        retry = self.command("create", {"workspace_id": "ws1", "key": "tab_A"}, "one")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json, retry.json)
        self.assertEqual(first.json["result"]["key"], "tab_A")
        self.assertEqual(len(retry.json["state"]["tabs"]), 1)

    def test_stale_commit_returns_conflict(self):
        self.command("create", {"workspace_id": "ws1"})
        response = self.command("commit", {"renderer_id": "old", "navigation_id": "old"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["code"], "navigation_superseded")

    def test_clients_cannot_forge_deletion(self):
        response = self.command("deleted", {"workspace_id": "ws1", "conversation_id": "abc"})
        self.assertEqual(response.status_code, 400)

    def test_malformed_payloads_are_client_errors(self):
        for body in ([1], None, "text", {"action": "create", "payload": [1]}):
            response = self.client.post("/api/conversation-tabs/commands", json=body)
            self.assertEqual(response.status_code, 400)

    def test_missing_conversation_never_registers_tab(self):
        response = self.command("open", {"workspace_id": "ws1", "conversation_id": "missing"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/conversation-tabs").json["tabs"], [])

    def test_storage_failure_is_not_empty_success(self):
        self.navigation.path.write_text("broken")
        response = self.client.get("/api/conversation-tabs")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json["success"])
        self.assertEqual(self.navigation.path.read_text(), "broken")

    def test_lookup_denial_preserves_existing_tab(self):
        self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})
        self.lookup.side_effect = PermissionError("synthetic workspace denial")
        self.navigation.checked.clear()
        response = self.client.get("/api/conversation-tabs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["tabs"]), 1)
        self.assertEqual(response.json["tabs"][0]["error"], "workspace_unavailable")

    def test_delete_handler_removes_matching_tabs_and_reports_real_success(self):
        self.command("open", {"workspace_id": "ws1", "conversation_id": "abc"})
        terminal = SimpleNamespace(context_manager=SimpleNamespace(current_conversation_id="conv_abc"),
                                   delete_conversation=Mock(return_value={"success": True}))
        blueprint = Blueprint("delete_test", __name__)
        bindings = handlers("server/conversation.py", {"delete_conversation"}, {
            "conversation_bp": blueprint, "api_login_required": lambda fn: fn,
            "with_terminal": lambda fn: fn, "request": request, "jsonify": jsonify,
            "IS_HOST_MODE": True, "logger": Mock(), "tr": lambda key: key,
        })
        module = SimpleNamespace(navigation=self.navigation)
        with patch.dict(sys.modules, {"server.conversation_tabs": module}), self.app.test_request_context():
            response = bindings["delete_conversation"]("conv_abc", terminal,
                SimpleNamespace(workspace_id="ws1"), "synthetic")
            self.assertTrue(response.json["success"])
            self.assertFalse(any(tab["conversation_id"] == "conv_abc"
                                 for tab in response.json["desktop_navigation"]["tabs"]))
            module.navigation = SimpleNamespace(deleted=Mock(side_effect=OSError("synthetic disk failure")))
            response = bindings["delete_conversation"]("conv_abc", terminal,
                SimpleNamespace(workspace_id="ws1"), "synthetic")
            self.assertTrue(response.json["success"])
            self.assertIn("desktop_navigation_error", response.json)


if __name__ == "__main__":
    unittest.main()

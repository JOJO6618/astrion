"""Exercise the real metadata handler in an isolated Flask application."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import ast
import unittest

from flask import Blueprint, Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[2]


class MetadataTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((ROOT / "server/conversation_bootstrap.py").read_text())
        definitions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                       and node.name in {"_normalize_conv_id", "conversation_metadata"}]
        self.manager = SimpleNamespace(read_conversation=Mock(return_value={
            "title": "Generated title", "metadata": {"title_locked": True},
            "messages": [{"content": "must not enter metadata response"}],
        }))
        self.context = SimpleNamespace(
            current_conversation_id="conv_other",
            _get_conversation_manager_for_id=Mock(return_value=self.manager),
        )
        self.resources = Mock(return_value=(
            SimpleNamespace(context_manager=self.context), SimpleNamespace(workspace_id="ws-B")
        ))
        blueprint = Blueprint("metadata_fixture", __name__)
        self.protected = []

        def login_required(function):
            self.protected.append(function.__name__)
            return function

        namespace = {
            "conversation_bootstrap_bp": blueprint, "api_login_required": login_required,
            "get_current_username": lambda: "synthetic-user", "get_user_resources": self.resources,
            "request": request, "jsonify": jsonify, "tr": lambda key, **kw: key,
        }
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "metadata_fixture", "exec"), namespace)
        app = Flask(__name__)
        app.register_blueprint(blueprint)
        self.client = app.test_client()

    def test_explicit_workspace_and_id_without_mutating_current_view(self):
        response = self.client.get("/api/conversations/B/metadata?workspace_id=ws-B")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"], {
            "id": "conv_B", "title": "Generated title", "title_locked": True,
        })
        self.resources.assert_called_once_with(
            "synthetic-user", workspace_id="ws-B", update_session=False
        )
        self.context._get_conversation_manager_for_id.assert_called_once_with("conv_B")
        self.manager.read_conversation.assert_called_once_with("conv_B")
        self.assertEqual(self.context.current_conversation_id, "conv_other")
        self.assertEqual(self.protected, ["conversation_metadata"])

    def test_missing_conversation_does_not_fall_back_to_current(self):
        self.manager.read_conversation.return_value = None
        response = self.client.get("/api/conversations/conv_missing/metadata?workspace_id=ws-B")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json["success"])
        self.manager.read_conversation.assert_called_once_with("conv_missing")

    def test_resource_failure_does_not_try_another_workspace(self):
        self.resources.side_effect = RuntimeError("workspace unavailable")
        response = self.client.get("/api/conversations/B/metadata?workspace_id=ws-B")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.resources.call_count, 1)
        self.manager.read_conversation.assert_not_called()

    def test_unlocked_title_is_reported_without_claiming_generation_finished(self):
        self.manager.read_conversation.return_value = {"title": "New Chat", "metadata": {}}
        response = self.client.get("/api/conversations/B/metadata")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["data"]["title_locked"])


if __name__ == "__main__":
    unittest.main()

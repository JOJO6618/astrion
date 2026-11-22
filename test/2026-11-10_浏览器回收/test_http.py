"""Test the retention response with synthetic navigation, tasks and storage."""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("retention_http_fixture", ROOT / "server/browser_retention.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


def module(**values):
    return types.SimpleNamespace(**values)


class ResponseTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.scope = ["agent", "ws", "conv_fixture"]
        self.key = json.dumps(self.scope, separators=(",", ":"))
        self.path = Mock()
        self.manager = Mock()
        self.manager._get_conversation_file_path.return_value = self.path
        self.tasks = Mock()
        self.tasks.list_tasks.return_value = []
        self.navigation = Mock()
        self.navigation.snapshot.return_value = {"tabs": []}
        self.modules = {
            "server.auth_helpers": module(get_current_username=lambda: "synthetic"),
            "server.conversation_tabs": module(navigation=self.navigation,
                _normalized_conversation_ids=lambda value: [value]),
            "server.context.workspace": module(request_identity=lambda: "trusted"),
            "server.context.data": module(get_workspace_data=lambda *_args, **_kw: module(manager=lambda: self.manager)),
            "server.tasks": module(task_manager=self.tasks),
        }

    def set_task(self, status, workspace="ws"):
        self.tasks.list_tasks.return_value = [module(status=status, workspace_id=workspace,
            conversation_id="conv_fixture", principal=module(conversation_kind="agent"),
            has_running_sub_agents=True, has_running_background_commands=True)]

    def request(self, *, raw=None):
        state = {"owners": [{"scope": self.scope, "visit": 1, "closed": True}]}
        with patch.dict(sys.modules, self.modules):
            with self.app.test_request_context(headers={"X-Astrion-Browser-State": raw or json.dumps(state)}):
                return self.app.make_response(handler.retention_response())

    def test_closed_tab_uses_main_task_status_and_ignores_background_work(self):
        self.set_task("running")
        self.assertEqual(self.request().get_json()["release"], [])
        self.navigation.snapshot.assert_called_once_with("synthetic")
        self.manager.read_conversation.assert_not_called()
        self.path.stat.assert_called_once()
        self.set_task("completed")
        self.assertEqual(self.request().get_json()["release"], [self.key])

    def test_different_workspace_task_does_not_protect_this_scope(self):
        self.set_task("pending", workspace="other")
        self.assertEqual(self.request().get_json()["release"], [self.key])

    def test_newly_reopened_scope_does_not_accept_old_close(self):
        self.navigation.snapshot.return_value = {"tabs": [{"kind": "conv", "workspace_id": "ws",
            "conversation_id": "conv_fixture", "key": "new-key"}]}
        self.assertEqual(self.request().get_json()["release"], [])

    def test_confirmed_missing_file_deletes_restoration(self):
        self.path.stat.side_effect = FileNotFoundError()
        self.assertEqual(self.request().get_json()["deleted"], [self.key])

    def test_permission_failure_does_not_mean_deletion_or_eviction(self):
        self.path.stat.side_effect = PermissionError()
        result = self.request().get_json()
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["release"], [])

    def test_invalid_state_is_rejected(self):
        self.assertEqual(self.request(raw='{"owners":["invalid"]}').status_code, 400)


if __name__ == "__main__":
    unittest.main()

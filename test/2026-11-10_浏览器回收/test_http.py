"""Test the real response builder with synthetic navigation/storage dependencies."""
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
from modules.native_browser.retention import BrowserRetention

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
        self.running = Mock(return_value=True)
        self.navigation = Mock()
        self.navigation.snapshot.return_value = {"tabs": [], "active_key": "", "navigation": {}}
        self.modules = {
            "server.auth_helpers": module(get_current_username=lambda: "synthetic"),
            "server.conversation_tabs": module(navigation=self.navigation,
                _active_run_conversation_ids=lambda *_: set(), _conversation_is_running=self.running),
            "server.context.workspace": module(request_identity=lambda: "trusted"),
            "server.context.data": module(get_workspace_data=lambda *_args, **_kw: module(manager=lambda: self.manager)),
        }

    def request(self, *, raw=None):
        state = {"owners": [self.scope], "tabs": [{"id": "page", "owner": self.key,
            "selected": True, "lastActive": 0, "lastAi": 0}]}
        with patch.dict(sys.modules, self.modules), patch.object(handler, "browser_retention", BrowserRetention()):
            with self.app.test_request_context(headers={"X-Astrion-Browser-State": raw or json.dumps(state)}):
                result = handler.retention_response()
                if isinstance(result, tuple): return result[0].get_json(), result[1]
                return result.get_json(), 200

    def test_closed_desktop_tab_still_protected_by_running_task(self):
        result, status = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["retained"], [self.key])
        self.assertEqual(result["release"], [])
        self.navigation.snapshot.assert_called_once_with("synthetic")
        self.manager.read_conversation.assert_not_called()
        self.path.stat.assert_called_once()

    def test_confirmed_missing_file_deletes_restoration(self):
        self.path.stat.side_effect = FileNotFoundError()
        result, _ = self.request()
        self.assertEqual(result["deleted"], [self.key])
        self.assertEqual(result["release"], ["page"])

    def test_permission_failure_does_not_mean_deletion(self):
        self.path.stat.side_effect = PermissionError()
        result, _ = self.request()
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["release"], [])

    def test_invalid_state_is_rejected(self):
        _, status = self.request(raw='{"owners":["invalid"],"tabs":[]}')
        self.assertEqual(status, 400)


if __name__ == "__main__": unittest.main()

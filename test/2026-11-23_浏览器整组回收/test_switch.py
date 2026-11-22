"""Exercise the browser switch route and its main-task execution boundary."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Blueprint, Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.native_browser.state import BROWSER_ENABLED_META_KEY, BROWSER_PENDING_META_KEY, browser_calls_allowed


class BrowserSwitchTest(unittest.TestCase):
    def setUp(self):
        blueprint = Blueprint("fixture_browser", __name__)
        terminal = SimpleNamespace(conversation_kind="agent", context_manager=SimpleNamespace(
            current_conversation_id="conv_fixture", conversation_metadata={BROWSER_ENABLED_META_KEY: True}
        ))
        terminal._persist_runtime_mode_metadata = terminal.context_manager.conversation_metadata.update
        self.terminal = terminal
        self.app = Flask(__name__)
        spec = importlib.util.spec_from_file_location("browser_switch_fixture", Path(__file__).resolve().parents[2] / "server/chat/browser.py")
        self.handler = importlib.util.module_from_spec(spec)
        stub = {
            "server.chat": SimpleNamespace(chat_bp=blueprint),
            "server.auth_helpers": SimpleNamespace(api_login_required=lambda fn: fn),
            "server.context": SimpleNamespace(with_terminal=lambda fn: fn),
            "server.context.data_decorators": SimpleNamespace(with_workspace_data=lambda fn: fn),
            "server.context.resources": SimpleNamespace(find_runtime=Mock()),
            "server.security": SimpleNamespace(rate_limited=lambda *a, **k: (lambda fn: fn)),
        }
        with patch.dict(sys.modules, stub):
            spec.loader.exec_module(self.handler)
        self.workspace = SimpleNamespace(workspace_id="ws")

    def toggle(self, enabled, running, release):
        with self.app.test_request_context(json={"enabled": enabled}):
            with patch.object(self.handler, "native_browser_supported", return_value=True):
                with patch.object(self.handler, "_conversation_running", return_value=running):
                    with patch.object(self.handler.client, "release_owner", release):
                        return self.handler.update_browser_tools(self.terminal, self.workspace, "owner").get_json()

    def test_running_switch_off_blocks_calls_before_the_bridge_releases_pages(self):
        release = Mock(side_effect=lambda *_: self.assertFalse(browser_calls_allowed(self.terminal)))
        response = self.toggle(False, True, release)
        self.assertEqual(response["pending"], "off")
        self.assertEqual(self.terminal.context_manager.conversation_metadata[BROWSER_PENDING_META_KEY], "off")
        release.assert_called_once_with("owner", "ws", "conv_fixture")

    def test_idle_switch_off_releases_and_switch_on_does_not(self):
        release = Mock()
        self.assertIsNone(self.toggle(False, False, release)["pending"])
        self.assertFalse(browser_calls_allowed(self.terminal))
        self.toggle(True, False, release)
        self.assertTrue(browser_calls_allowed(self.terminal))
        release.assert_called_once_with("owner", "ws", "conv_fixture")


if __name__ == "__main__":
    unittest.main()

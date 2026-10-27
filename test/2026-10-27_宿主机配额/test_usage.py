"""Account quota API checks using synthetic sessions and temporary storage."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from modules.account_usage import account_tracker
from modules.usage_tracker import QUOTA_DEFAULTS
from server.usage import usage_bp


class UsageTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.personal = Path(temporary.name) / "personal"
        self.app = Flask(__name__)
        self.app.secret_key = "synthetic-quota-regression"
        self.app.register_blueprint(usage_bp)
        self.client = self.app.test_client()
        self.manager = Mock()
        self.manager.get_user.return_value = SimpleNamespace(username="synthetic", role="user")
        self.manager._personal_root.return_value = self.personal
        for patcher in (
            patch("server.state.user_manager", self.manager),
            patch.dict("server.state.usage_trackers", {}, clear=True),
            patch.dict("modules.account_usage._TRACKERS", {}, clear=True),
            patch.dict("server.state.active_login_nonces", {"synthetic": {"test-nonce"}}, clear=True),
            patch("server.host_password.browser_session_valid", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.login()

    def login(self, host=False):
        with self.client.session_transaction() as session:
            session.update(username="synthetic", login_nonce="test-nonce", host_mode=host)

    def test_host_returns_not_applicable_without_tracker_or_workspace(self):
        self.login(host=True)
        with patch("server.usage.get_or_create_usage_tracker") as tracker:
            response = self.client.get("/api/usage")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "applicable": False, "data": None})
        tracker.assert_not_called()
        self.manager.assert_not_called()
        self.assertEqual(self.manager.mock_calls, [])
        self.assertFalse(self.personal.exists())

    def test_host_flag_does_not_bypass_authentication(self):
        self.login(host=True)
        with self.client.session_transaction() as session:
            session.pop("login_nonce")
        response = self.client.get("/api/usage")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("applicable", response.json)

    def test_account_returns_actual_shared_usage_without_workspace_provisioning(self):
        tracker = account_tracker(self.personal)
        tracker.check_and_increment("fast")
        tracker.check_and_increment("search")
        for workspace in ("workspace-a", "workspace-b"):
            response = self.client.get(f"/api/usage?workspace_id={workspace}")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["applicable"])
            quotas = response.json["data"]["quotas"]
            self.assertEqual(quotas["fast"]["count"], 1)
            self.assertEqual(quotas["search"]["count"], 1)
            self.assertEqual(quotas["thinking"]["count"], 0)
            self.assertEqual(quotas["fast"]["limit"], QUOTA_DEFAULTS["default"]["fast"]["limit"])
        self.manager.ensure_user_workspace.assert_not_called()
        self.manager.ensure_workspace.assert_not_called()

    def test_missing_normal_account_still_returns_404(self):
        self.manager.get_user.return_value = None
        response = self.client.get("/api/usage")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json["success"])
        self.manager.ensure_user_workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()

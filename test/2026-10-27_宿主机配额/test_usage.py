"""The legacy quota endpoint remains available to authenticated Host clients."""
import unittest
from unittest.mock import patch

from flask import Flask

from server.usage import usage_bp


class UsageTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "synthetic-quota-regression"
        app.register_blueprint(usage_bp)
        self.client = app.test_client()
        self.enterContext(patch.dict("server.state.active_login_nonces", {"host": {"test-nonce"}}, clear=True))
        self.enterContext(patch("server.host_password.browser_session_valid", return_value=True))

    def login(self, **overrides):
        identity = {"username": "host", "host_mode": True, "login_nonce": "test-nonce"}
        with self.client.session_transaction() as session:
            session.update(identity | overrides)

    def test_host_returns_not_applicable_without_provisioning_a_workspace(self):
        self.login()
        with patch("server.context.workspace.resolve_host_workspace", side_effect=AssertionError("workspace lookup")):
            response = self.client.get("/api/usage?workspace_id=missing")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "applicable": False, "data": None})

    def test_host_flag_does_not_bypass_authentication(self):
        self.login(login_nonce=None)
        response = self.client.get("/api/usage")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("applicable", response.json)

    def test_legacy_accounts_are_rejected(self):
        for identity in ({"username": "legacy-user"}, {"is_api_user": True}, {"host_mode": False}):
            with self.subTest(identity=identity):
                self.login(is_api_user=False)
                self.login(**identity)
                self.assertEqual(self.client.get("/api/usage").status_code, 401)


if __name__ == "__main__":
    unittest.main()

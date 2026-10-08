"""Shared blueprint checks; no server startup, tokens, or actual installation."""
from contextlib import ExitStack
from unittest.mock import patch
import unittest

from flask import Flask, session


class SetupApiTests(unittest.TestCase):
    def setUp(self):
        import config
        import server.gateway_auth as auth
        import server.status.sandbox as routes
        from server.status import status_bp
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(config, "TERMINAL_SANDBOX_MODE", "host"))
        self.stack.enter_context(patch.object(auth, "is_logged_in", return_value=False))
        self.stack.enter_context(patch.object(auth, "_verify_host_bearer", side_effect=lambda token: token == "synthetic-token"))
        self.stack.enter_context(patch.object(auth, "_inject_host_identity", side_effect=lambda: session.update(host_mode=True)))
        self.manager = self.stack.enter_context(patch.object(routes, "sandbox_setup_manager"))
        self.manager.get_sandbox_status.return_value = {"applicable": True, "platform": "linux", "state": "helper_missing"}
        self.manager.start_setup.return_value = {"started": True, "error": None}
        self.manager.get_setup_progress.return_value = {"active": True, "phase": "installing"}
        app = Flask(__name__)
        app.secret_key = "synthetic-test-only"
        app.register_blueprint(status_bp)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer synthetic-token"}

    def test_host_bearer_without_cookie_can_detect_and_start_installation(self):
        response = self.client.get('/api/sandbox/status', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data']['platform'], 'linux')
        response = self.client.post('/api/sandbox/setup', headers=self.headers, json={})
        self.assertEqual(response.status_code, 200)
        self.manager.start_setup.assert_called_once_with(False)
        response = self.client.get('/api/sandbox/setup/status', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data']['phase'], 'installing')

    def test_unauthenticated_requests_never_start_installer(self):
        for url in ('/api/sandbox/status', '/api/sandbox/setup/status'):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post('/api/sandbox/setup', json={}).status_code, 401)
        self.manager.start_setup.assert_not_called()

    def test_authenticated_non_host_session_cannot_install_or_read_progress(self):
        import server.gateway_auth as auth
        with patch.object(auth, '_inject_host_identity', side_effect=lambda: session.update(host_mode=False)):
            self.assertEqual(self.client.post('/api/sandbox/setup', headers=self.headers, json={}).status_code, 400)
            self.assertEqual(self.client.get('/api/sandbox/setup/status', headers=self.headers).status_code, 400)
        self.manager.start_setup.assert_not_called()


if __name__ == '__main__':
    unittest.main()

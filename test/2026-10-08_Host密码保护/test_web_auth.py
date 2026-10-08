from contextlib import ExitStack
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, jsonify, session
from werkzeug.routing import BaseConverter

from modules.host_auth import host_auth_path, load_host_auth, set_host_password

PASSWORD = "Synthetic test 123!"


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        import server.auth as routes
        import server.host_password as policy
        from server import state
        from server.auth_helpers import api_login_required
        from server.security import attach_security_hooks

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.data = Path(directory)
        self.policy = policy
        self.stack.enter_context(patch.object(policy, "DATA_DIR", directory))
        self.stack.enter_context(patch.object(policy, "TERMINAL_SANDBOX_MODE", "host"))
        self.stack.enter_context(patch.object(policy, "LINUX_SAFETY", False))
        self.stack.enter_context(patch.dict(os.environ, {"ASTRION_DESKTOP_VERSION": ""}))
        self.stack.enter_context(patch.object(routes, "DATA_DIR", directory))
        self.stack.enter_context(patch.object(routes, "LOGS_DIR", directory))
        self.stack.enter_context(patch.object(routes, "TERMINAL_SANDBOX_MODE", "host"))
        self.stack.enter_context(patch.object(routes, "LINUX_SAFETY", False))
        self.stack.enter_context(patch.object(routes, "resolve_host_workspace", return_value=(None, None)))
        self.stack.enter_context(patch.object(routes, "auth_debug_log"))
        self.stack.enter_context(patch.object(state, "container_manager"))
        self.stack.enter_context(patch.dict(state.active_login_nonces, clear=True))
        self.stack.enter_context(patch.dict(state.RATE_LIMIT_BUCKETS, clear=True))
        self.stack.enter_context(patch.dict(state.FAILURE_TRACKERS, clear=True))
        app = Flask(__name__)
        app.secret_key = "synthetic-test-only"
        app.url_map.converters["conv"] = BaseConverter
        app.register_blueprint(routes.auth_bp)
        app.register_blueprint(policy.host_password_bp)
        app.send_static_file = lambda name: name
        attach_security_hooks(app)

        @app.get('/api/protected-test')
        @api_login_required
        def protected():
            return jsonify(success=True, host=bool(session.get('host_mode')))

        self.app = app
        self.client = app.test_client()

    def post(self, path, payload=None, *, client=None, ip="127.0.0.1", headers=None):
        client = client or self.client
        token = client.get('/api/csrf-token').json['token']
        return client.post(path, json=payload or {},
                           headers={"X-CSRF-Token": token, **(headers or {})},
                           environ_overrides={"REMOTE_ADDR": ip})

    def login(self, *, client=None, ip="127.0.0.1", password=PASSWORD):
        return self.post('/host-login', {'password': password}, client=client, ip=ip)

    def test_default_loopback_entry_unchanged_and_remote_free_entry_rejected(self):
        status = self.client.get('/api/host-mode-enabled').json
        self.assertTrue(status['enabled'])
        self.assertFalse(status['password_required'])
        self.assertEqual(self.post('/host-login').status_code, 200)
        self.assertEqual(self.client.get('/api/protected-test').status_code, 200)
        other = self.app.test_client()
        self.assertEqual(self.post('/host-login', client=other, ip="203.0.113.1").status_code, 403)
        self.assertFalse(host_auth_path(self.data).exists())

    def test_password_required_even_on_loopback_and_remote_correct_password_works(self):
        set_host_password(self.data, PASSWORD)
        self.assertTrue(self.client.get('/api/host-mode-enabled').json['password_required'])
        for value in (None, "incorrect", 123, {"password": PASSWORD}):
            self.assertEqual(self.login(password=value).status_code, 401)
        self.assertEqual(self.login(ip="203.0.113.1").status_code, 200)
        self.assertTrue(self.client.get('/api/protected-test').json['host'])

    def test_enable_and_reset_revoke_existing_sessions_without_clearing_running_tasks(self):
        self.assertEqual(self.post('/host-login').status_code, 200)
        set_host_password(self.data, PASSWORD)
        self.assertEqual(self.client.get('/api/protected-test').status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        second = self.app.test_client()
        self.assertEqual(self.login(client=second).status_code, 200)
        set_host_password(self.data, "New synthetic 123")
        for client in (self.client, second):
            self.assertEqual(client.get('/api/protected-test').status_code, 401)
            self.assertFalse(client.get('/api/session-status').json['session']['logged_in'])
        self.assertEqual(self.login(password="New synthetic 123").status_code, 200)

    def test_disable_rechecks_password_and_returns_to_free_entry(self):
        set_host_password(self.data, PASSWORD)
        self.login()
        second = self.app.test_client()
        self.login(client=second)
        self.assertTrue(self.client.get('/api/host-password').json['enabled'])
        self.assertEqual(self.post('/api/host-password/disable', {'password': 'incorrect'}).status_code, 401)
        self.assertTrue(load_host_auth(self.data).enabled)
        self.assertEqual(self.post('/api/host-password/disable', {'password': PASSWORD}).status_code, 200)
        self.assertFalse(load_host_auth(self.data).enabled)
        self.assertEqual(second.get('/api/protected-test').status_code, 401)
        self.assertEqual(self.post('/host-login').status_code, 200)
        self.assertFalse(self.client.get('/api/host-password').json['enabled'])
        set_host_password(self.data, PASSWORD)
        self.assertEqual(self.post('/host-login').status_code, 401)

    def test_same_password_reset_during_disable_cannot_disable_new_generation(self):
        from modules.host_auth import disable_host_password
        from server import state
        set_host_password(self.data, PASSWORD)
        self.login()
        marker = object()
        with patch.dict(state.user_terminals, {'synthetic-running-task': marker}):
            def reset_before_commit(data_dir, password, **options):
                set_host_password(data_dir, PASSWORD)
                return disable_host_password(data_dir, password, **options)
            with patch.object(self.policy, 'disable_host_password', side_effect=reset_before_commit):
                response = self.post('/api/host-password/disable', {'password': PASSWORD})
            self.assertEqual(response.status_code, 401)
            self.assertTrue(load_host_auth(self.data).enabled)
            self.assertIs(state.user_terminals['synthetic-running-task'], marker)
            state.container_manager.release_container.assert_not_called()

    def test_previous_ordinary_account_cookie_cannot_bypass_host_password(self):
        from server.auth import _issue_login_nonce
        with self.client.session_transaction() as browser_session:
            browser_session.update(username='synthetic-account', host_mode=False)
        with self.app.test_request_context():
            nonce = _issue_login_nonce('synthetic-account')
        with self.client.session_transaction() as browser_session:
            browser_session['login_nonce'] = nonce
        self.assertEqual(self.client.get('/api/protected-test').status_code, 200)
        set_host_password(self.data, PASSWORD)
        self.assertEqual(self.client.get('/api/protected-test').status_code, 401)

    def test_anonymous_cannot_disable_and_there_is_no_web_enable_endpoint(self):
        set_host_password(self.data, PASSWORD)
        self.assertEqual(self.post('/api/host-password/disable', {'password': PASSWORD}).status_code, 401)
        self.assertEqual(self.post('/api/host-password', {'enabled': True, 'password': PASSWORD}).status_code, 405)
        self.assertTrue(load_host_auth(self.data).enabled)

    def test_csrf_cannot_be_bypassed_by_arbitrary_bearer_header(self):
        set_host_password(self.data, PASSWORD)
        self.assertEqual(self.client.post('/host-login', json={'password': PASSWORD},
                         headers={'Authorization': 'Bearer synthetic-invalid'}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post('/api/host-password/disable', json={'password': PASSWORD},
                         headers={'Authorization': 'Bearer synthetic-invalid'}).status_code, 403)
        self.assertTrue(load_host_auth(self.data).enabled)

    def test_password_host_hides_and_rejects_account_login_and_registration(self):
        set_host_password(self.data, PASSWORD)
        self.assertEqual(self.post('/login', {'email': 'synthetic@example.invalid', 'password': PASSWORD}).status_code, 403)
        self.assertEqual(self.client.get('/register').status_code, 302)
        self.assertEqual(self.post('/register').status_code, 403)

    def test_desktop_backend_is_exempt_even_with_damaged_password_file(self):
        host_auth_path(self.data).write_text('broken')
        with patch.dict(os.environ, {'ASTRION_DESKTOP_VERSION': 'synthetic-desktop'}):
            status = self.client.get('/api/host-mode-enabled').json
            self.assertFalse(status['password_required'])
            self.assertTrue(status['config_valid'])
            self.assertEqual(self.post('/host-login').status_code, 200)
            self.assertFalse(self.client.get('/api/host-password').json['applicable'])
            self.assertEqual(self.post('/api/host-password/disable', {'password': PASSWORD}).status_code, 403)

    def test_request_desktop_markers_do_not_exempt_source_backend(self):
        set_host_password(self.data, PASSWORD)
        for header in ({'X-Astrion-Desktop': 'true'}, {'X-Forwarded-For': '127.0.0.1'},
                       {'ASTRION_DESKTOP_VERSION': 'synthetic'}):
            response = self.post('/host-login?desktop=1', headers=header)
            self.assertEqual(response.status_code, 401)

    def test_damaged_config_blocks_login_and_existing_sessions(self):
        self.post('/host-login')
        host_auth_path(self.data).write_text('broken')
        status = self.client.get('/api/host-mode-enabled').json
        self.assertTrue(status['password_required'])
        self.assertFalse(status['config_valid'])
        self.assertEqual(self.login().status_code, 503)
        self.assertEqual(self.client.get('/api/protected-test').status_code, 401)
        self.assertEqual(self.post('/login').status_code, 403)

    def test_failed_password_attempts_are_rate_limited(self):
        set_host_password(self.data, PASSWORD)
        for _ in range(5):
            self.assertEqual(self.login(password='incorrect 123').status_code, 401)
        self.assertEqual(self.login().status_code, 429)

    def test_web_mode_ignores_host_password_file_and_keeps_account_entry(self):
        host_auth_path(self.data).write_text('broken')
        with patch.object(self.policy, 'TERMINAL_SANDBOX_MODE', 'docker'):
            status = self.client.get('/api/host-mode-enabled').json
            self.assertFalse(status['enabled'])
            self.assertFalse(status['password_required'])
            self.assertEqual(self.client.get('/register').status_code, 200)

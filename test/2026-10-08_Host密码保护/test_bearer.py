from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, jsonify, session

from modules.host_auth import set_host_password


class BearerTests(unittest.TestCase):
    def setUp(self):
        import server.gateway_auth as gateway
        import server.host_password as policy
        from server import state
        from server.auth import _issue_login_nonce
        from server.auth_helpers import api_login_required, is_logged_in
        from server.security import attach_security_hooks, get_csrf_token

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(policy, 'DATA_DIR', self.directory))
        self.stack.enter_context(patch.object(policy, 'TERMINAL_SANDBOX_MODE', 'host'))
        self.stack.enter_context(patch.object(policy, 'LINUX_SAFETY', False))
        self.stack.enter_context(patch.object(gateway, 'TERMINAL_SANDBOX_MODE', 'host'))
        self.stack.enter_context(patch.object(gateway, 'LINUX_SAFETY', False))
        self.stack.enter_context(patch.dict(os.environ, {'ASTRION_DESKTOP_VERSION': ''}))
        self.stack.enter_context(patch.object(gateway, 'get_or_create_host_api_token', return_value='synthetic-token'))
        self.stack.enter_context(patch.object(gateway, 'resolve_host_workspace', return_value=(None, None)))
        self.stack.enter_context(patch.dict(state.active_login_nonces, clear=True))
        self.state = state
        app = Flask(__name__)
        app.secret_key = 'synthetic-test-only'
        attach_security_hooks(app)

        @app.route('/api/test-gateway', methods=['GET', 'POST'])
        @gateway.api_login_or_host_token_required
        def endpoint():
            # Downstream views can mutate the session, but must not issue a cookie.
            session['view_modified_session'] = True
            return jsonify(success=is_logged_in(), username=session.get('username'))

        @app.get('/api/browser-only')
        @api_login_required
        def browser_only():
            return jsonify(success=True)

        @app.get('/api/synthetic-cookie')
        def cookie():
            session.update(username='host', role='admin', host_mode=True)
            _issue_login_nonce('host')
            return jsonify(token=get_csrf_token())

        self.app = app
        self.client = app.test_client()
        self.headers = {'Authorization': 'Bearer synthetic-token'}

    def test_cli_remains_available_when_password_is_enabled_or_corrupt(self):
        set_host_password(self.directory, 'Synthetic test 123!')
        for method in ('get', 'post'):
            response = getattr(self.client, method)('/api/test-gateway', headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['success'])
            self.assertEqual(response.json['username'], 'host')
            self.assertNotIn('Set-Cookie', response.headers)
        (Path(self.directory) / 'host_auth.json').write_text('broken')
        self.assertEqual(self.client.get('/api/test-gateway', headers=self.headers).status_code, 200)
        self.assertFalse(self.state.active_login_nonces.get('host'))
        self.assertEqual(self.client.get('/api/browser-only').status_code, 401)

    def test_invalid_bearer_cannot_fall_back_to_valid_cookie(self):
        csrf = self.client.get('/api/synthetic-cookie').json['token']
        self.assertEqual(self.client.get('/api/test-gateway').status_code, 200)
        invalid = {'Authorization': 'Bearer wrong', 'X-CSRF-Token': csrf}
        self.assertEqual(self.client.get('/api/test-gateway', headers=invalid).status_code, 401)
        self.assertEqual(self.client.post('/api/test-gateway', headers=invalid).status_code, 401)
        self.assertEqual(self.client.post('/api/test-gateway', headers={'Authorization': 'Bearer wrong'}).status_code, 403)

    def test_valid_bearer_does_not_replace_an_existing_browser_cookie(self):
        self.client.get('/api/synthetic-cookie')
        before = self.client.get_cookie('session').value
        response = self.client.post('/api/test-gateway', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertEqual(self.client.get_cookie('session').value, before)
        self.assertEqual(self.client.get('/api/browser-only').status_code, 200)

    def test_remote_bearer_rejected(self):
        response = self.client.get('/api/test-gateway', headers=self.headers,
                                   environ_overrides={'REMOTE_ADDR': '203.0.113.1'})
        self.assertEqual(response.status_code, 401)

    def test_headless_uses_same_bearer_policy(self):
        from server.headless_app import create_headless_app
        from server.gateway_auth import api_login_or_host_token_required
        from server.auth_helpers import is_logged_in
        app = create_headless_app()
        app.add_url_rule('/api/synthetic-headless', view_func=api_login_or_host_token_required(
            lambda: jsonify(success=is_logged_in())), methods=['POST'])
        set_host_password(self.directory, 'Synthetic test 123!')
        response = app.test_client().post('/api/synthetic-headless', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['success'])
        self.assertNotIn('Set-Cookie', response.headers)

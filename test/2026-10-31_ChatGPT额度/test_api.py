"""Account routing, cache refresh and error handling at the Flask boundary."""
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from flask import Flask
import httpx

from server.codex_auth import codex_auth_bp
from utils.api_client.codex.accounts import CodexAccountStore
from utils.api_client.codex.usage import SubscriptionQueries


class UsageApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = CodexAccountStore(
            legacy_auth_path=str(root / 'auth.json'), accounts_dir=str(root / 'accounts'),
            index_path=str(root / 'index.json'),
        )
        self.accounts = []
        for name in ['A', 'B']:
            payload = {'https://api.openai.com/auth': {'chatgpt_account_id': name}, 'sub': name}
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
            self.accounts.append(self.store.upsert_tokens(
                access_token=f'fake-{name}', refresh_token='fake-refresh',
                id_token=f'header.{encoded}.signature', account_id=name,
            ))
        service = SubscriptionQueries()
        self.addCleanup(service.close)
        self.service = service
        for patcher in [
            patch('server.codex_auth.get_codex_account_store', return_value=self.store),
            patch('server.auth_helpers.is_logged_in', return_value=True),
            patch('server.auth_helpers.is_admin_user', return_value=True),
            patch('utils.api_client.codex.usage.subscription_queries', service),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.secret_key = 'synthetic'
        app.register_blueprint(codex_auth_bp)
        self.client = app.test_client()

    def test_account_hash_and_cache_and_force(self):
        response = Mock()
        response.json.return_value = {'rate_limit': {'primary_window': {'used_percent': 13}}}
        with patch('server.codex_auth._wham_request', return_value=(response, None)) as query:
            for account in self.accounts:
                result = self.client.get(f"/api/codex/usage?account_id={account['id']}")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json['account_hash'], account['hash'])
                self.assertEqual(result.json['account_id'], account['id'])
            a = self.accounts[0]['id']
            self.client.get(f'/api/codex/usage?account_id={a}')
            self.assertEqual(query.call_count, 2)
            self.client.get(f'/api/codex/usage?account_id={a}&refresh=1')
            self.assertEqual(query.call_count, 3)

    def test_unknown_account_never_falls_back(self):
        with patch('server.codex_auth._wham_request') as query:
            result = self.client.get('/api/codex/usage?account_id=missing')
            self.assertEqual(result.status_code, 404)
            query.assert_not_called()

    def test_timeout_is_structured(self):
        account = self.accounts[0]['id']
        with patch.object(self.service, 'request', side_effect=httpx.ConnectTimeout('TLS')):
            result = self.client.get(f'/api/codex/usage?account_id={account}')
        self.assertEqual(result.status_code, 502)
        self.assertFalse(result.json['success'])
        self.assertIn('ChatGPT', result.json['error'])

    def test_consume_invalidates_usage_cache(self):
        account = self.accounts[0]['id']
        response = Mock()
        response.json.return_value = {'rate_limit': {}}
        with patch('server.codex_auth._wham_request', return_value=(response, None)) as query:
            self.client.get(f'/api/codex/usage?account_id={account}')
            response.json.return_value = {'redeemed': True}
            result = self.client.post('/api/codex/reset-credits/consume', json={
                'account_id': account, 'credit_id': 'synthetic-credit',
            })
            self.assertEqual(result.status_code, 200)
            response.json.return_value = {'rate_limit': {}}
            self.client.get(f'/api/codex/usage?account_id={account}')
            self.assertEqual(query.call_count, 3)

"""Snapshot the Python Codex account-lifecycle contracts with synthetic data.

This test imports the production Flask blueprint and CodexAccountStore, but uses
only temporary credentials, synthetic JWT payloads, fake model summaries and a
fake in-process subscription query service. It never reads a real account,
starts an OAuth listener, or performs external network I/O.

Run:
    python3.11 -B test/2026-11-26_Rust订阅生命周期/contract_snapshot.py
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from server.codex_auth import codex_auth_bp  # noqa: E402
from utils.api_client.codex import oauth  # noqa: E402
from utils.api_client.codex.accounts import CodexAccountStore  # noqa: E402


def jwt(claims: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b'=').decode()
    return f'header.{raw}.signature'


def id_token(account_id: str) -> str:
    return jwt({
        'https://api.openai.com/auth': {'chatgpt_account_id': account_id},
        'sub': f'sub-{account_id}',
    })


def credential(account_id: str, access: str, refresh: str, *, expires_at: int) -> dict:
    return {
        'auth_mode': 'chatgpt',
        'OPENAI_API_KEY': None,
        'tokens': {
            'id_token': id_token(account_id),
            'access_token': jwt({'exp': expires_at}),
            'refresh_token': refresh,
            'account_id': account_id,
        },
        'last_refresh': '2026-11-26T00:00:00.000000Z',
    }


class LifecycleBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy_path = self.root / 'codex_auth.json'
        self.accounts_dir = self.root / 'codex_accounts'
        self.index_path = self.root / 'codex_accounts.json'
        self.models_path = self.root / 'codex_models.json'
        self.models_dir = self.root / 'codex_models'
        self.settings_patch = patch.multiple(
            'utils.api_client.codex.settings',
            CODEX_DATA_PATH=str(self.models_path),
            CODEX_MODELS_DIR=str(self.models_dir),
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

        self.store = CodexAccountStore(
            legacy_auth_path=str(self.legacy_path),
            accounts_dir=str(self.accounts_dir),
            index_path=str(self.index_path),
        )
        self.app = Flask(__name__)
        self.app.secret_key = 'codex-lifecycle-contract'
        self.app.register_blueprint(codex_auth_bp)
        self.client = self.app.test_client()
        self.model_summary = lambda account_id: {
            'source': 'online',
            'fetched_at': '2026-11-26T00:00:00.000000Z',
            'model_count': 1,
            'total_models': 1,
            'visible_models': [f'{account_id}/gpt-fixture'],
        }
        self.patchers = [
            patch('server.codex_auth.get_codex_account_store', return_value=self.store),
            patch('server.codex_auth.get_models_manager',
                  side_effect=lambda account_id: SimpleNamespace(
                      status_summary=lambda: self.model_summary(account_id))),
            patch('server.auth_helpers.is_logged_in', return_value=True),
            patch('server.auth_helpers.is_admin_user', return_value=True),
            patch('utils.api_client.codex.models.reset_models_manager', return_value=None),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_legacy(self, account_id: str = 'acct-legacy') -> dict:
        self.legacy_path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_path.write_text(json.dumps(
            credential(account_id, 'legacy-access', 'legacy-refresh',
                       expires_at=4_102_444_800)), encoding='utf-8')
        accounts = self.store.list_accounts()
        self.assertEqual(accounts[0]['id'], 'openai-codex')
        return accounts[0]

    def add_account(self, account_id: str = 'acct-B') -> dict:
        return self.store.upsert_tokens(
            access_token=jwt({'exp': 4_102_444_800}), refresh_token='refresh-B',
            id_token=id_token(account_id), account_id=account_id,
        )

    def status(self, account_id: str):
        response = self.client.get(f'/api/codex/status?account_id={account_id}')
        return response.status_code, response.get_json()


class StatusContractTests(LifecycleBase):
    def setUp(self) -> None:
        super().setUp()
        self.poll_patch = patch('server.codex_auth.poll_login_flow',
                                return_value={'status': 'idle'})
        self.poll_patch.start()
        self.addCleanup(self.poll_patch.stop)

    def test_status_binds_explicit_account_and_returns_auth_projection(self):
        self.add_legacy()
        second = self.add_account()
        status, data = self.status(second['id'])
        self.assertEqual(status, 200)
        self.assertIs(data['success'], True)
        self.assertIs(data['connected'], True)
        self.assertEqual(data['account_id'], second['id'])
        self.assertEqual(data['account_hash'], second['hash'])
        self.assertEqual(data['account_name'], second['display_name'])
        self.assertIs(data['is_primary'], False)
        self.assertEqual(data['auth_mode'], 'chatgpt')
        self.assertEqual(data['expires_at'], 4_102_444_800)
        self.assertGreaterEqual(data['expires_in_seconds'], 0)
        self.assertTrue(data['last_refresh'].endswith('Z'))
        self.assertEqual(data['models'], self.model_summary(second['id']))
        self.assertEqual(data['login_flow'], {'status': 'idle'})
        self.assertNotIn('access_token', json.dumps(data))
        self.assertNotIn('refresh_token', json.dumps(data))

    def test_unknown_account_is_404_and_never_falls_back(self):
        self.add_legacy()
        status, data = self.status('openai-codex-nope')
        self.assertEqual(status, 404)
        self.assertEqual(data, {'success': False, 'error': 'account_not_found'})

    def test_status_without_any_account_is_non_error_empty_state(self):
        empty = CodexAccountStore(
            legacy_auth_path=str(self.root / 'empty' / 'codex_auth.json'),
            accounts_dir=str(self.root / 'empty' / 'codex_accounts'),
            index_path=str(self.root / 'empty' / 'codex_accounts.json'),
        )
        with patch('server.codex_auth.get_codex_account_store', return_value=empty):
            response = self.client.get('/api/codex/status')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIs(data['success'], True)
        self.assertIs(data['connected'], False)
        self.assertIsNone(data['account_id'])
        self.assertIsNone(data['account_hash'])
        self.assertIsNone(data['account_name'])
        self.assertIs(data['is_primary'], True)
        self.assertEqual(data['models'], {'model_count': 0, 'total_models': 0})
        self.assertEqual(data['login_flow'], {'status': 'idle'})
        self.assertEqual(data['error'], 'no_codex_account')

    def test_status_missing_credential_stays_bound_and_reports_disconnected(self):
        self.add_legacy()
        second = self.add_account()
        (self.accounts_dir / f"{second['id']}.json").unlink()
        status, data = self.status(second['id'])
        self.assertEqual(status, 200)
        self.assertIs(data['connected'], False)
        self.assertEqual(data['account_id'], second['id'])
        self.assertEqual(data['account_hash'], second['hash'])
        self.assertIn('未找到 Codex 凭证', data['error'])
        self.assertEqual(data['models'], self.model_summary(second['id']))


class LogoutContractTests(LifecycleBase):
    def test_logout_removes_only_selected_account_and_returns_new_primary(self):
        self.add_legacy()
        second = self.add_account()
        response = self.client.post('/api/codex/logout', json={'account_id': second['id']})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIs(data['success'], True)
        self.assertEqual(data['account_id'], second['id'])
        self.assertEqual(data['id'], second['id'])
        self.assertIs(data['removed'], True)
        self.assertIs(data['models_cache_cleared'], True)
        self.assertEqual(data['primary_account_id'], 'openai-codex')
        self.assertFalse((self.accounts_dir / f"{second['id']}.json").exists())
        self.assertEqual([item['id'] for item in self.store.list_accounts()], ['openai-codex'])

    def test_legacy_logout_clears_models_but_preserves_global_settings(self):
        self.add_legacy()
        self.models_path.write_text(json.dumps({
            'proxy': 'http://127.0.0.1:7890',
            'client_version': '0.153.4',
            'models': [{'slug': 'gpt-fixture'}],
            'fetched_at': '2026-11-26T00:00:00.000000Z',
            'etag': 'fixture-etag',
            'future': {'keep': True},
        }), encoding='utf-8')
        response = self.client.post('/api/codex/logout', json={'account_id': 'openai-codex'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertIs(data['models_cache_cleared'], True)
        self.assertIsNone(data['primary_account_id'])
        stored = json.loads(self.models_path.read_text(encoding='utf-8'))
        for key in ('models', 'fetched_at', 'source', 'etag'):
            self.assertNotIn(key, stored)
        self.assertEqual(stored['proxy'], 'http://127.0.0.1:7890')
        self.assertEqual(stored['client_version'], '0.153.4')
        self.assertEqual(stored['future'], {'keep': True})
        self.assertEqual(self.store.list_accounts(), [])

    def test_logout_missing_credential_removes_registration(self):
        second = self.add_account()
        (self.accounts_dir / f"{second['id']}.json").unlink()
        response = self.client.post('/api/codex/logout', json={'account_id': second['id']})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIs(data['success'], True)
        self.assertIs(data['removed'], True)
        self.assertEqual(self.store.list_accounts(), [])

    def test_logout_oserror_is_structured_and_keeps_registration(self):
        self.add_legacy()
        second = self.add_account()
        with patch.object(self.store, 'logout', side_effect=OSError('denied')):
            response = self.client.post('/api/codex/logout', json={'account_id': second['id']})
        self.assertEqual(response.status_code, 500)
        data = response.get_json()
        self.assertEqual(data['success'], False)
        self.assertEqual(data['error'], 'logout_failed')
        self.assertEqual(data['account_id'], second['id'])
        self.assertIn('denied', data['detail'])
        self.assertEqual(
            sorted(item['id'] for item in self.store.list_accounts()),
            sorted(['openai-codex', second['id']]),
        )


class FakeFlow:
    def __init__(self, status: str = 'pending'):
        self.state = {'status': status, 'flow_mode': 'browser', 'phase': 'waiting'}
        self.cancelled = False

    def poll(self):
        return dict(self.state)

    def cancel(self):
        self.cancelled = True
        self.state = {'status': 'cancelled'}


class LoginContractTests(LifecycleBase):
    def test_pending_flow_returns_409_for_both_start_routes_without_cancelling(self):
        fake = FakeFlow()
        with patch.object(oauth, '_current_flow', fake):
            browser = self.client.post('/api/codex/login/start')
            device = self.client.post('/api/codex/login/device/start')
        for response in (browser, device):
            self.assertEqual(response.status_code, 409)
            data = response.get_json()
            self.assertEqual(data['success'], False)
            self.assertEqual(data['error'], 'login_in_progress')
            self.assertIs(data['reused'], True)
            self.assertEqual(data['status'], 'pending')
        self.assertFalse(fake.cancelled)

    def test_poll_and_cancel_use_the_current_flow_state(self):
        fake = FakeFlow()
        with patch.object(oauth, '_current_flow', fake):
            polled = self.client.get('/api/codex/login/poll')
            self.assertEqual(polled.status_code, 200)
            self.assertEqual(polled.get_json()['status'], 'pending')
            self.assertEqual(polled.get_json()['success'], True)
            cancelled = self.client.post('/api/codex/login/cancel')
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json(), {'success': True, 'status': 'cancelled'})
        self.assertTrue(fake.cancelled)
        self.assertIsNone(oauth._current_flow)

    def test_start_success_and_failure_envelopes(self):
        success = {'status': 'pending', 'phase': 'waiting', 'flow_mode': 'browser',
                   'authorize_url': 'https://auth.example/fixture'}
        with patch('server.codex_auth.start_login_flow', return_value=success) as start:
            response = self.client.post('/api/codex/login/start')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['success'], True)
        self.assertEqual(response.get_json()['authorize_url'], success['authorize_url'])
        start.assert_called_once()

        with patch('server.codex_auth.start_login_flow',
                   return_value={'status': 'failed', 'error': 'port_busy'}):
            failed = self.client.post('/api/codex/login/start')
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.get_json(), {'success': False, 'error': 'port_busy'})

    def test_device_start_returns_pending_starting_shape(self):
        state = {'status': 'pending', 'phase': 'starting', 'flow_mode': 'device'}
        with patch('server.codex_auth.start_device_login_flow', return_value=state):
            response = self.client.post('/api/codex/login/device/start')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIs(data['success'], True)
        self.assertEqual(data['flow_mode'], 'device')
        self.assertEqual(data['phase'], 'starting')

    def test_unknown_reconnect_account_is_404_before_flow_start(self):
        with patch('server.codex_auth.start_login_flow') as start:
            response = self.client.post(
                '/api/codex/login/start?account_id=openai-codex-nope')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(),
                         {'success': False, 'error': 'account_not_found'})
        start.assert_not_called()


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeQueries:
    def __init__(self):
        self.response = FakeResponse({'rate_limit': {'primary_window': {'used_percent': 12}}})
        self.calls = []
        self.force = None
        self.invalidated = []

    def request(self, account_id, method, url, headers, json_body=None):
        self.calls.append((account_id, method, url, dict(headers), json_body))
        return self.response

    def usage(self, account_id, auth, fetch, *, force=False):
        self.force = force
        return fetch()

    def invalidate(self, account_id):
        self.invalidated.append(account_id)


class QuotaContractTests(LifecycleBase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.add_account()
        self.auth_path = self.accounts_dir / f"{self.account['id']}.json"
        self.auth = SimpleNamespace(
            auth_path=str(self.auth_path),
            get_access_token_sync=lambda: 'fake-access',
            get_account_id=lambda: 'acct-B',
        )
        self.auth_patch = patch.object(self.store, 'get_auth_manager',
                                       return_value=self.auth)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.queries = FakeQueries()
        self.query_patch = patch('utils.api_client.codex.usage.subscription_queries',
                                 self.queries)
        self.query_patch.start()
        self.addCleanup(self.query_patch.stop)

    def usage(self, refresh: bool = False):
        suffix = '?account_id=' + self.account['id']
        if refresh:
            suffix += '&refresh=1'
        return self.client.get('/api/codex/usage' + suffix)

    def test_usage_binds_explicit_account_headers_and_force(self):
        response = self.usage()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIs(data['success'], True)
        self.assertEqual(data['account_id'], self.account['id'])
        self.assertEqual(data['account_hash'], self.account['hash'])
        self.assertEqual(data['usage'], self.queries.response.payload)
        account_id, method, url, headers, body = self.queries.calls[0]
        self.assertEqual(account_id, self.account['id'])
        self.assertEqual(method, 'GET')
        self.assertTrue(url.endswith('/wham/usage'))
        self.assertEqual(headers['Authorization'], 'Bearer fake-access')
        self.assertEqual(headers['originator'], 'codex_cli_rs')
        self.assertEqual(headers['chatgpt-account-id'], 'acct-B')
        self.assertNotIn('OpenAI-Beta', headers)
        self.assertIsNone(body)
        self.assertIs(self.queries.force, False)

        self.assertEqual(self.usage(refresh=True).status_code, 200)
        self.assertIs(self.queries.force, True)

    def test_usage_http_error_maps_to_502(self):
        self.queries.response = FakeResponse({}, status_code=500)
        response = self.usage()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json(),
                         {'success': False, 'error': 'http_500'})

    def test_reset_credits_list_and_consume_contract(self):
        self.queries.response = FakeResponse({
            'credits': [{'id': 'credit-fixture', 'status': 'available'}],
        })
        listed = self.client.get(
            f"/api/codex/reset-credits?account_id={self.account['id']}")
        self.assertEqual(listed.status_code, 200)
        listed_data = listed.get_json()
        self.assertEqual(listed_data['success'], True)
        self.assertEqual(listed_data['account_id'], self.account['id'])
        self.assertEqual(listed_data['credits'][0]['id'], 'credit-fixture')

        missing = self.client.post(
            '/api/codex/reset-credits/consume',
            json={'account_id': self.account['id']},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json(),
                         {'success': False, 'error': 'missing credit_id'})

        consumed = self.client.post(
            '/api/codex/reset-credits/consume',
            json={'account_id': self.account['id'], 'credit_id': 'credit-fixture'},
        )
        self.assertEqual(consumed.status_code, 200)
        consumed_data = consumed.get_json()
        self.assertIs(consumed_data['success'], True)
        self.assertEqual(consumed_data['account_id'], self.account['id'])
        account_id, method, url, headers, body = self.queries.calls[-1]
        self.assertEqual(method, 'POST')
        self.assertTrue(url.endswith('/rate-limit-reset-credits/consume'))
        self.assertEqual(body['credit_id'], 'credit-fixture')
        self.assertEqual(len(body['redeem_request_id']), 36)
        self.assertEqual(self.queries.invalidated, [self.account['id']])
        self.assertEqual(headers['chatgpt-account-id'], 'acct-B')


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Synthetic credentials and mocked HTTP only; no real account or external network."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx

from utils.api_client.codex.auth import CodexAuthManager
from utils.api_client.codex.usage import SubscriptionQueries


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.service = SubscriptionQueries()
        self.addCleanup(self.service.close)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Mock(auth_path=str(Path(self.temp.name) / 'auth.json'))
        Path(self.auth.auth_path).write_text('{}')

    def test_cache_is_account_isolated_and_force_bypasses(self):
        fetch = Mock(return_value={'rate_limit': {'primary_window': {'used_percent': 12}}})
        a = self.service.usage('a', self.auth, fetch)
        a['rate_limit'].clear()
        self.assertTrue(self.service.usage('a', self.auth, fetch)['rate_limit'])
        self.assertEqual(fetch.call_count, 1)
        self.service.usage('b', self.auth, fetch)
        self.service.usage('a', self.auth, fetch, force=True)
        self.assertEqual(fetch.call_count, 3)
        self.service.invalidate('a')
        self.service.usage('a', self.auth, fetch)
        self.assertEqual(fetch.call_count, 4)

    def test_credential_replacement_invalidates_cache(self):
        fetch = Mock(return_value={'rate_limit': {}})
        self.service.usage('a', self.auth, fetch)
        Path(self.auth.auth_path).write_text('{"changed":true}')
        self.service.usage('a', self.auth, fetch)
        self.assertEqual(fetch.call_count, 2)
        Path(self.auth.auth_path).unlink()
        with self.assertRaises(FileNotFoundError):
            self.service.usage('a', self.auth, fetch)

    def test_parallel_reads_share_one_fetch(self):
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            self.assertTrue(release.wait(2))
            return {'rate_limit': {}}
        callback = Mock(side_effect=fetch)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.service.usage, 'a', self.auth, callback)
            self.assertTrue(entered.wait(2))
            second = pool.submit(self.service.usage, 'a', self.auth, callback)
            release.set()
            self.assertEqual(first.result(), second.result())
        self.assertEqual(callback.call_count, 1)

    def test_failed_fetch_does_not_poison_cache(self):
        fetch = Mock(side_effect=[httpx.ConnectTimeout('timeout'), {'rate_limit': {}}])
        with self.assertRaises(httpx.ConnectTimeout):
            self.service.usage('a', self.auth, fetch)
        self.service.usage('a', self.auth, fetch)
        self.assertEqual(fetch.call_count, 2)

    def test_connection_retry_and_reuse_without_post_retry(self):
        first, second = Mock(), Mock()
        first.request.side_effect = httpx.ConnectTimeout('handshake')
        second.request.return_value = httpx.Response(200, json={'rate_limit': {}})
        with patch('utils.api_client.codex.usage.resolve_proxy', return_value='http://proxy'), \
                patch('utils.api_client.codex.usage.httpx.Client', side_effect=[first, second]) as factory:
            self.service.request('a', 'GET', 'https://test/usage', {'Authorization': 'Bearer fake-a'})
            self.service.request('a', 'GET', 'https://test/usage', {'Authorization': 'Bearer fake-a'})
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(second.request.call_count, 2)
            self.assertEqual(factory.call_args.kwargs['timeout'].connect, 5)
        failing = Mock()
        failing.request.side_effect = httpx.ConnectTimeout('handshake')
        with patch('utils.api_client.codex.usage.resolve_proxy', return_value=None), \
                patch('utils.api_client.codex.usage.httpx.Client', return_value=failing):
            with self.assertRaises(httpx.ConnectTimeout):
                self.service.request('b', 'POST', 'https://test/consume', {}, {'credit_id': 'fake'})
            self.assertEqual(failing.request.call_count, 1)

    def test_proxy_change_closes_old_connection(self):
        first, second = Mock(), Mock()
        with patch('utils.api_client.codex.usage.resolve_proxy', side_effect=['http://a', 'http://b']), \
                patch('utils.api_client.codex.usage.httpx.Client', side_effect=[first, second]):
            self.service.request('a', 'GET', 'https://test', {})
            self.service.request('a', 'GET', 'https://test', {})
        first.close.assert_called_once()
        second.request.assert_called_once()


class RefreshTests(unittest.TestCase):
    def test_sync_queries_rotate_refresh_token_once(self):
        def jwt(exp):
            encoded = base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=')
            return f'header.{encoded}.signature'
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'auth.json'
            path.write_text(json.dumps({'tokens': {'access_token': jwt(1), 'refresh_token': 'fake'}}))
            auth = CodexAuthManager(str(path))
            fresh = jwt(int(time.time()) + 3600)
            started, release = threading.Event(), threading.Event()
            original = auth._get_cached
            barrier = threading.Barrier(2)
            def cached():
                value = original()
                barrier.wait(timeout=2)
                return value
            def rotate():
                started.set()
                self.assertTrue(release.wait(2))
                payload = {'tokens': {'access_token': fresh, 'refresh_token': 'rotated'}}
                path.write_text(json.dumps(payload))
                return payload['tokens']
            with patch.object(auth, '_get_cached', side_effect=cached), \
                    patch.object(auth, '_refresh_sync', side_effect=rotate) as refresh:
                with ThreadPoolExecutor(2) as pool:
                    a = pool.submit(auth.get_access_token_sync)
                    b = pool.submit(auth.get_access_token_sync)
                    self.assertTrue(started.wait(2))
                    release.set()
                    self.assertEqual(a.result(), fresh)
                    self.assertEqual(b.result(), fresh)
                self.assertEqual(refresh.call_count, 1)

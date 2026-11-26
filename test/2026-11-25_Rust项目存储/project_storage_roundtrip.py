"""Test project storage through real Rust Web and headless listeners.

The current Python endpoint calls a deleted method. The last Python Host mode
returned zero. Its historical Docker directory traversal is the byte-count
oracle, with outside-workspace file links excluded by the new Host contract.

Run: python3.11 -B test/2026-11-25_Rust项目存储/project_storage_roundtrip.py
"""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import ROOT, fixture_environment, rust

BINARY = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless'))
API = '/api/project-storage'
MB = 1024 * 1024


def register(env, entries, default='alpha'):
    path = Path(env['HOST_WORKSPACES_FILE'])
    path.write_text(json.dumps({
        'default_workspace_id': default,
        'workspaces': [{'workspace_id': name, 'label': name, 'path': str(folder)} for name, folder in entries.items()],
    }), encoding='utf-8')
    return path


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix='storage-', dir=HERE) as temp:
        root = Path(temp).resolve()
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data / 'personalization.json').write_text('{"ui_locale":"en-US"}', encoding='utf-8')
        alpha = Path(env['HOST_PROJECT_PATH'])
        beta = root / 'other-workspace'
        beta.mkdir()
        register(env, {'alpha': alpha, 'beta': beta})
        yield root, env, data, alpha, beta


@contextmanager
def listener(root, env, *, web=True):
    with rust(env, BINARY, 'http://127.0.0.1:1/fixture-unused', root / 'rust.log', web=web) as base:
        yield base


def request(base, path, *, method='GET', headers=None, payload=None, status=200):
    url = urlsplit(base)
    sent = {'Origin': base, **(headers or {})}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        sent['Content-Type'] = 'application/json'
    connection = HTTPConnection(url.hostname, url.port, timeout=8)
    try:
        connection.request(method, path, body, sent)
        with connection.getresponse() as response:
            raw = response.read()
            assert response.status == status, (method, path, status, response.status, raw)
            parsed = json.loads(raw) if raw and response.headers.get('Content-Type', '').startswith('application/json') else raw
            return parsed, response.headers
    finally:
        connection.close()


def login(base):
    csrf, headers = request(base, '/api/csrf-token')
    guest = headers['Set-Cookie'].split(';', 1)[0]
    result, headers = request(base, '/host-login', method='POST', payload={},
                              headers={'Cookie': guest, 'X-CSRF-Token': csrf['token']})
    assert result['success'] is True, result
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    csrf, _ = request(base, '/api/csrf-token', headers={'Cookie': cookie})
    return {'Cookie': cookie, 'X-CSRF-Token': csrf['token']}


def historical_size(folder):
    total = 0
    for item in folder.rglob('*'):
        if item.is_file():
            try:
                total += item.stat().st_size
            except FileNotFoundError:
                continue
    return total


def expected(used, limit_mb):
    return {'success': True, 'data': {
        'used_bytes': used,
        'limit_bytes': limit_mb * MB if limit_mb else None,
        'limit_label': f'{limit_mb}MB' if limit_mb else '未限制',
        'usage_percent': used / (limit_mb * MB) * 100 if limit_mb else None,
    }}


def snapshot(paths):
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


class ProjectStorageTests(unittest.TestCase):
    def test_web_workspace_selection_cache_and_quota(self):
        with fixture() as (root, env, _, alpha, beta):
            runtime = Path(env['ASTRION_DATA_ROOT'])
            runtime.mkdir(exist_ok=True)
            (runtime / 'settings.json').write_text(json.dumps({
                'terminal': {'project_max_storage_mb': 1},
                'env_vars': {'PROJECT_MAX_STORAGE_MB': '3', 'PROJECT_STORAGE_CACHE_TTL': '0.8'},
            }), encoding='utf-8')
            (alpha / '.git').mkdir()
            hidden = alpha / '.git' / 'config'
            hidden.write_bytes(b'fixture config')
            sparse = alpha / 'large-sparse'
            with sparse.open('wb') as stream:
                stream.truncate(2 * MB + 64)
            beta_file = beta / 'other.txt'
            beta_file.write_bytes(b'other workspace')
            registry = Path(env['HOST_WORKSPACES_FILE'])
            immutable = snapshot([hidden, sparse, beta_file, registry])

            with listener(root, env) as base:
                request(base, API, status=401)
                browser = login(base)
                data = Path(env['DATA_DIR'])
                bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
                request(base, API, headers=bearer, status=401)
                headers_beta = {**browser, 'X-Astrion-Workspace-Id': 'beta'}

                def get(path=API, **options):
                    return request(base, path, **{'headers': browser, **options})[0]

                first = expected(historical_size(alpha), 1)
                second = expected(historical_size(beta), 1)
                self.assertGreater(first['data']['usage_percent'], 200)
                self.assertEqual(get(), first)
                self.assertEqual(get(API + '?workspace_id=beta'), second)
                self.assertEqual(get(headers=headers_beta), second)
                self.assertEqual(get(payload={'workspace_id': 'beta'}), second)
                self.assertEqual(get(API + '?workspace_id=%20alpha%20', headers=headers_beta), first)
                self.assertEqual(get(API + '?workspace_id=alpha&workspace_id=beta'), first)
                self.assertEqual(get(API, method='HEAD'), b'')
                self.assertEqual(get(API + '?workspace_id=missing', status=404),
                                 {'success': False, 'error': 'Workspace not found'})
                self.assertEqual(snapshot(immutable), immutable)

                request(base, '/api/host/workspaces/select', method='POST', headers=browser,
                        payload={'workspace_id': 'beta'})
                self.assertEqual(get(), second)
                self.assertEqual(get(API + '?workspace_id=alpha'), first)
                self.assertEqual(get(), second, 'explicit lookup changed browser selection')

                with sparse.open('r+b') as stream:
                    stream.truncate(3 * MB + 128)
                self.assertEqual(get(API + '?workspace_id=alpha'), first, 'hot cache should survive a same-directory edit')
                time.sleep(0.85)
                changed = expected(historical_size(alpha), 1)
                self.assertNotEqual(changed, first)
                self.assertEqual(get(API + '?workspace_id=alpha'), changed)
                self.assertEqual(get(), second)

                replacement = root / 'new-beta-path'
                replacement.mkdir()
                (replacement / 'short').write_bytes(b'new')
                register(env, {'alpha': alpha, 'beta': replacement})
                self.assertEqual(get(), expected(historical_size(replacement), 1), 'same ID changed its registered path')
                self.assertEqual(get(API + '?workspace_id=alpha'), changed)
                register(env, {'alpha': alpha})
                self.assertEqual(get(), changed, 'removed browser selection should fall back to catalog default')
                self.assertEqual(get(API + '?workspace_id=beta', status=404)['success'], False)

                registry.write_bytes(b'{')
                before = snapshot([registry])
                empty = get()
                self.assertEqual((empty['success'], empty['code']), (False, 'no_workspace'))
                self.assertEqual(get(API + '?workspace_id=missing'), empty)
                self.assertEqual(snapshot(before), before, 'malformed registry must remain read-only')
                registry.unlink()
                self.assertEqual(get(), empty)
                self.assertFalse(registry.exists())

    def test_symlinks_match_historical_file_rules_inside_the_workspace(self):
        with fixture() as (root, env, _, alpha, beta):
            env['PROJECT_MAX_STORAGE_MB'] = '0'
            env['PROJECT_STORAGE_CACHE_TTL'] = '0'
            runtime = Path(env['ASTRION_DATA_ROOT'])
            runtime.mkdir(exist_ok=True)
            (runtime / 'settings.json').write_text('{"terminal":{"project_max_storage_mb":2}}', encoding='utf-8')
            nested = alpha / 'nested'
            nested.mkdir()
            (nested / 'inside').write_bytes(b'abcde')
            item = alpha / 'item'
            item.write_bytes(b'count hardlinks and file symlinks')
            os.link(item, alpha / 'hardlink')
            external = beta / 'outside-file'
            external.write_bytes(b'outside this workspace')
            try:
                (nested / 'relative-to-parent').symlink_to('../item')
                (alpha / 'absolute-internal').symlink_to(item)
                (alpha / 'file-chain').symlink_to('nested/relative-to-parent')
                (alpha / 'directory-alias').symlink_to(nested, target_is_directory=True)
                (alpha / 'absolute-external').symlink_to(external)
                (alpha / 'external-directory').symlink_to(beta, target_is_directory=True)
                (alpha / 'outside-directory-hop').symlink_to('external-directory/outside-file')
                (alpha / 'broken').symlink_to('not-found')
                (alpha / 'cycle-a').symlink_to('cycle-b')
                (alpha / 'cycle-b').symlink_to('cycle-a')
            except OSError as error:
                self.skipTest(f'fixture filesystem cannot create symlinks: {error}')
            if hasattr(os, 'mkfifo'):
                os.mkfifo(alpha / 'not-a-regular-file')

            with listener(root, env) as base:
                browser = login(base)
                read = lambda path=API: request(base, path, headers=browser)[0]
                old = historical_size(alpha)
                difference = 2 * external.stat().st_size
                new = old - difference
                self.assertEqual(new, 5 + 5 * item.stat().st_size)
                self.assertEqual(read(), expected(new, 0))
                second = read(API + '?workspace_id=beta')
                self.assertEqual(second, expected(external.stat().st_size, 0))
                beta.rename(root / 'beta-removed')
                self.assertEqual(read(API + '?workspace_id=beta'), expected(0, 0))
                self.assertFalse(beta.exists(), 'read must never recreate a project directory')
                self.assertEqual(read(), expected(new, 0))

    @unittest.skipIf(os.name == 'nt', 'POSIX fixture changes directory search permission')
    def test_scan_failure_returns_stale_only_for_its_workspace_and_recovers(self):
        with fixture() as (root, env, _, alpha, beta):
            env['PROJECT_STORAGE_CACHE_TTL'] = '0'
            lock_dirs = []
            for project in (alpha, beta):
                locked = project / 'locked'
                locked.mkdir()
                (locked / 'target').write_bytes(b'fixture data')
                (project / 'link').symlink_to('locked/target')
                lock_dirs.append(locked)
            with listener(root, env) as base:
                browser = login(base)
                first = request(base, API, headers=browser)[0]
                self.assertEqual(first, expected(historical_size(alpha), 2048))
                modes = [stat.S_IMODE(path.stat().st_mode) for path in lock_dirs]
                try:
                    for path in lock_dirs:
                        path.chmod(0)
                    try:
                        (alpha / 'link').stat()
                    except PermissionError:
                        pass
                    else:
                        self.skipTest('fixture user can still stat within a directory without search permission')
                    stale, _ = request(base, API, headers=browser)
                    self.assertEqual(stale, {**first, 'stale': True})
                    failed, _ = request(base, API + '?workspace_id=beta', headers=browser, status=500)
                    self.assertEqual(failed['success'], False)
                    self.assertTrue(failed['error'])
                finally:
                    for path, mode in zip(lock_dirs, modes):
                        path.chmod(mode)
                (alpha / 'locked' / 'target').write_bytes(b'recovered')
                self.assertEqual(request(base, API, headers=browser)[0], expected(historical_size(alpha), 2048))

    def test_headless_registered_but_neither_anonymous_nor_gateway_bearer_can_reach_storage(self):
        with fixture() as (root, env, data, _, _), listener(root, env, web=False) as base:
            bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
            self.assertTrue(request(base, '/api/tasks', headers=bearer)[0]['success'])
            request(base, '/api/csrf-token', status=404)
            for method in ('GET', 'HEAD'):
                for credentials in ({}, bearer):
                    request(base, API + '?workspace_id=beta', method=method, headers=credentials, status=401)


if __name__ == '__main__':
    unittest.main(verbosity=2)

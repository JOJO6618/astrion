"""Exercise the desktop bridge through real Rust listeners and synthetic loopback shells.

Run: python3.11 -B test/2026-11-25_Rust桌面壳桥接/desktop_bridge_roundtrip.py
"""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import ROOT, fixture_environment, rust

PREFIX = '/api/desktop'
ROUTES = (
    ('GET', '/quick/info'), ('POST', '/quick/configure'),
    ('POST', '/window/drag'), ('POST', '/window/control'), ('GET', '/window/state'),
    ('GET', '/update/check'), ('POST', '/update/install'), ('GET', '/update/progress'),
    ('GET', '/rundata/info'), ('POST', '/rundata/choose'),
    ('POST', '/rundata/apply'), ('POST', '/rundata/restart'),
)
NOT_DESKTOP = {'success': False, 'code': 'not_desktop', 'error': 'Not running inside the desktop app'}
BINARY = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless'))


class Shell(BaseHTTPRequestHandler):
    def reply(self):
        size = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(size)
        payload = json.loads(raw) if raw else None
        self.server.calls.append((self.command, self.path, payload, dict(self.headers.items())))
        status, body, headers = self.server.responses.get((self.command, self.path), (404, {'error': 'not_found'}, {}))
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = reply

    def log_message(self, *args):
        pass


@contextmanager
def shell():
    with ThreadingHTTPServer(('127.0.0.1', 0), Shell) as server:
        server.calls = []
        server.responses = {}
        server.origin = f'http://127.0.0.1:{server.server_port}'
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


def request(base, method, path, *, body=None, headers=None, status=200):
    url = urlsplit(base)
    sent = {'Origin': base, **(headers or {})}
    if body is not None:
        sent['Content-Type'] = 'application/json'
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
    connection = HTTPConnection(url.hostname, url.port, timeout=10)
    try:
        connection.request(method, path, body, sent)
        with connection.getresponse() as response:
            raw = response.read()
            assert response.status == status, (method, path, status, response.status, raw)
            return raw, response.headers
    finally:
        connection.close()


def api(base, method, path, **options):
    raw, _ = request(base, method, PREFIX + path, **options)
    return json.loads(raw)


def login(base):
    raw, headers = request(base, 'GET', '/api/csrf-token')
    csrf = json.loads(raw)['token']
    guest = headers['Set-Cookie'].split(';', 1)[0]
    raw, headers = request(base, 'POST', '/host-login', body={}, headers={'Cookie': guest, 'X-CSRF-Token': csrf})
    assert json.loads(raw)['success'] is True
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    raw, _ = request(base, 'GET', '/api/csrf-token', headers={'Cookie': cookie})
    return {'Cookie': cookie, 'X-CSRF-Token': json.loads(raw)['token']}


@contextmanager
def backend(bridge, *, web, desktop=None, port=None, proxy=None):
    with tempfile.TemporaryDirectory(dir=HERE) as directory:
        root = Path(directory)
        env = fixture_environment(root)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data / 'personalization.json').write_text('{"ui_locale":"en-US"}', encoding='utf-8')
        if desktop is not None:
            env['ASTRION_DESKTOP_VERSION'] = desktop
        if port is not None:
            env['ASTRION_DESKTOP_BRIDGE_PORT'] = str(port)
        if proxy is not None:
            env.update({name: proxy.origin for name in ('HTTP_PROXY', 'ALL_PROXY', 'http_proxy', 'all_proxy')})
        with rust(env, BINARY, bridge.origin + '/fixture-model', root / 'rust.log', web=web) as base:
            yield base, data


class DesktopBridgeTest(unittest.TestCase):
    def test_web_permissions_and_non_desktop_contract(self):
        with shell() as mock, backend(mock, web=True, desktop='  ', port=mock.server_port) as (base, data):
            browser = login(base)
            bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
            for method, path in ROUTES:
                with self.subTest(method=method, path=path):
                    for headers in ({}, bearer):
                        api(base, method, path, headers=headers, status=401 if method == 'GET' else 403)
                    if method == 'POST':
                        api(base, method, path, headers={'Cookie': browser['Cookie']}, status=403)
                    self.assertEqual(api(base, method, path, headers=browser), NOT_DESKTOP)
            spoofed = {
                **browser, 'X-Astrion-Desktop-Version': '7.0.0',
                'X-Astrion-Desktop-Bridge-Port': str(mock.server_port),
            }
            self.assertEqual(api(base, 'GET', f'/window/state?port={mock.server_port}', headers=spoofed), NOT_DESKTOP)
            self.assertEqual(api(base, 'POST', '/rundata/apply', headers=spoofed,
                                 body={'bridge_port': mock.server_port, 'version': '7.0.0'}), NOT_DESKTOP)
            unknown = {'ok': False, 'error': 'unsupported_quick_operation'}
            self.assertEqual(api(base, 'GET', '/quick/configure', headers=browser, status=404), unknown)
            self.assertEqual(api(base, 'POST', '/quick/info', headers=browser, status=404), unknown)
            self.assertEqual(request(base, 'HEAD', PREFIX + '/quick/info', headers=browser, status=404)[0], b'')
            self.assertEqual(mock.calls, [])

    def test_headless_registers_routes_with_session_only_permissions(self):
        with shell() as mock, backend(mock, web=False, desktop='0.9.0', port=mock.server_port) as (base, data):
            bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
            request(base, 'GET', '/api/tasks', status=401)
            self.assertEqual(json.loads(request(base, 'GET', '/api/tasks', headers=bearer)[0])['success'], True)
            request(base, 'GET', '/api/csrf-token', status=404)
            for method, path in ROUTES:
                with self.subTest(method=method, path=path):
                    for headers in ({}, bearer):
                        api(base, method, path, headers=headers, status=401 if method == 'GET' else 403)
            self.assertEqual(mock.calls, [])

    def test_web_shell_forwarding_and_error_contract(self):
        with shell() as mock, shell() as decoy:
            mock.responses.update({
                ('GET', '/quick/info'): (200, {'ok': True, 'enabled': True}, {}),
                ('GET', '/quick/permissions'): (200, {'ok': True, 'input': False}, {}),
                ('POST', '/quick/configure'): (200, {'ok': True}, {}),
                ('POST', '/quick/open'): (200, {'ok': True}, {}),
                ('POST', '/quick/input-permission'): (200, {'ok': True}, {}),
                ('POST', '/window/drag'): (200, {'started': True}, {}),
                ('POST', '/window/control'): (200, {'success': True, 'maximized': False}, {}),
                ('GET', '/window/state'): (200, {'success': True, 'maximized': True}, {}),
                ('POST', '/update/install'): (202, {'started': True}, {}),
                ('GET', '/update/progress'): (200, {'state': 'idle', 'downloaded': 0, 'total': None, 'error': None}, {}),
                ('GET', '/rundata/info'): (200, {'active_path': '/fixture/current', 'env_locked': False}, {}),
                ('POST', '/rundata/choose'): (200, {'success': True, 'canceled': True, 'path': ''}, {}),
                ('POST', '/rundata/apply'): (200, {'success': True, 'restart_required': True}, {}),
                ('POST', '/rundata/restart'): (202, {'success': True, 'restarting': True}, {}),
            })
            with backend(mock, web=True, desktop='0.9.0', port=mock.server_port, proxy=decoy) as (base, data):
                browser = login(base)

                def call(method, path, **options):
                    return api(base, method, path, headers=browser, **options)

                def called(method, path, body=None):
                    got_method, got_path, got_body, received = mock.calls[-1]
                    self.assertEqual((got_method, got_path, got_body), (method, path, body))
                    headers = {key.lower(): value for key, value in received.items()}
                    self.assertEqual(headers['host'], f'127.0.0.1:{mock.server_port}')
                    for private in ('cookie', 'authorization', 'origin', 'x-csrf-token'):
                        self.assertNotIn(private, headers)

                for operation in ('info', 'permissions'):
                    path = f'/quick/{operation}'
                    self.assertEqual(call('GET', path + f'?bridge_port={decoy.server_port}'), mock.responses['GET', path][1])
                    called('GET', path)
                for operation, incoming, forwarded in (
                    ('configure', {'enabled': True}, {'enabled': True}),
                    ('open', ['ignored non-object'], None),
                    ('input-permission', b'{broken', None),
                ):
                    path = f'/quick/{operation}'
                    self.assertEqual(call('POST', path, body=incoming), {'ok': True})
                    called('POST', path, forwarded)
                unreachable = call('POST', '/quick/capture-permission')
                self.assert_bridge_error(unreachable, '404')
                called('POST', '/quick/capture-permission')

                self.assertEqual(call('POST', '/window/drag', body={'ignored': True}), {'success': True, 'data': {'started': True}})
                called('POST', '/window/drag')
                for action in ('minimize', 'maximize-toggle', 'close'):
                    self.assertEqual(call('POST', '/window/control', body={'action': f' {action} ', 'url': decoy.origin}),
                                     {'success': True, 'data': {'success': True, 'maximized': False}})
                    called('POST', '/window/control', {'action': action})
                count = len(mock.calls)
                self.assertEqual(call('POST', '/window/control', body={'action': 'restart'})['code'], 'invalid_action')
                self.assertEqual(len(mock.calls), count)
                state = {'success': True, 'data': {'success': True, 'maximized': True}}
                self.assertEqual(call('GET', '/window/state'), state)
                called('GET', '/window/state')
                request(base, 'HEAD', PREFIX + '/window/state', headers=browser)
                called('GET', '/window/state')

                self.assertEqual(call('POST', '/update/install', status=202), {'success': True, 'data': {'started': True}})
                called('POST', '/update/install')
                mock.responses['POST', '/update/install'] = (200, {'started': False}, {})
                self.assertEqual(call('POST', '/update/install', status=409), {'success': False, 'data': {'started': False}})
                mock.responses['POST', '/update/install'] = (409, {'started': False, 'error': 'manual_update_required'}, {})
                self.assert_bridge_error(call('POST', '/update/install'), '409')
                self.assertEqual(call('GET', '/update/progress'), {'success': True, 'data': mock.responses['GET', '/update/progress'][1]})
                called('GET', '/update/progress')
                if sys.platform not in ('darwin', 'win32'):
                    self.assertEqual(call('GET', '/update/check')['code'], 'unsupported_platform')

                self.assertEqual(call('GET', '/rundata/info'), mock.responses['GET', '/rundata/info'][1])
                called('GET', '/rundata/info')
                self.assertEqual(call('POST', '/rundata/choose', body={'ignored': decoy.origin}),
                                 mock.responses['POST', '/rundata/choose'][1])
                called('POST', '/rundata/choose', {})
                target = data / 'synthetic-never-created'
                patch = {'data_root': str(target), 'migrate': True}
                self.assertEqual(call('POST', '/rundata/apply', body=patch), mock.responses['POST', '/rundata/apply'][1])
                called('POST', '/rundata/apply', patch)
                self.assertFalse(target.exists())
                call('POST', '/rundata/apply', body=b'{broken')
                called('POST', '/rundata/apply', {})
                self.assertEqual(call('POST', '/rundata/restart', body={'ignored': True}, status=202),
                                 mock.responses['POST', '/rundata/restart'][1])
                called('POST', '/rundata/restart', {})
                self.assertEqual(call('GET', '/window/state'), state, 'mock restart should leave Rust running')

                mock.responses['GET', '/window/state'] = (302, {}, {'Location': decoy.origin + '/followed'})
                self.assert_bridge_error(call('GET', '/window/state'), '302')
                self.assertEqual(decoy.calls, [], 'shell HTTP must bypass proxies and refuse redirects')
                mock.responses['GET', '/window/state'] = (200, b'invalid JSON', {})
                self.assert_bridge_error(call('GET', '/window/state'))

    def assert_bridge_error(self, body, status_text=None):
        self.assertEqual({key: body[key] for key in ('success', 'code', 'error')}, {
            'success': False, 'code': 'bridge_unreachable', 'error': 'Cannot reach the update service',
        })
        self.assertTrue(0 < len(body['detail']) <= 200, body)
        if status_text:
            self.assertIn(status_text, body['detail'])

    def test_only_a_numeric_configured_port_is_accepted(self):
        with shell() as mock, shell() as decoy:
            for port in (None, f'1@127.0.0.1:{decoy.server_port}', '65536', '0'):
                with self.subTest(port=port), backend(mock, web=True, desktop='1.2.3', port=port) as (base, _):
                    browser = login(base)
                    body = api(base, 'GET', '/window/state', headers=browser)
                    self.assertEqual(body['code'], 'bridge_unavailable')
                    self.assertEqual(body['success'], False)
            self.assertEqual(mock.calls, [])
            self.assertEqual(decoy.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Exercise GET/POST /api/codex/settings on the real Rust Web/headless binaries.

The fixture owns a synthetic DATA_DIR, runs the production Rust listener with
different proxy environments, then hands the same directory to a Python
listener that mounts the production ``server.codex_auth`` blueprint. Finally the
Rust listener is restarted to verify the Python write survived. Only synthetic
credentials/settings are used; no external service or real account is touched.

Run with the newly built binary (the settings route must already exist):
    python3.11 -B test/2026-11-26_Rust订阅设置/codex_settings_roundtrip.py /path/to/astrion-headless

Or set ``ASTRION_HEADLESS_BIN`` to that path and omit argv[1].
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SETTINGS = '/api/codex/settings'
PROXY_KEYS = ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')
DEFAULT_VERSION = '0.153.4'
_MISSING = object()
OPENER = build_opener(ProxyHandler({}))

# Legacy cache fields that must survive both producers' read-modify-write.
UNKNOWN = {
    'source': 'online',
    'fetched_at': '2026-11-26T00:00:00Z',
    'models': [
        {'slug': 'fixture-luna', 'visibility': 'list', 'supported_in_api': True},
        {'slug': 'fixture-hidden', 'visibility': 'hide', 'supported_in_api': True},
    ],
    'future_extension': {'保留': [1, None, {'revision': 7}], 'empty': {}},
}
SEED = {**UNKNOWN, 'proxy': 'seed-file-proxy', 'client_version': '8.1.0'}


def request(origin, method, path, *, body=_MISSING, raw=None, headers=None, status=200):
    sent = {'Origin': origin, **(headers or {})}
    data = None
    if raw is not None:
        data = raw
    elif body is not _MISSING:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    if data is not None:
        sent.setdefault('Content-Type', 'application/json')
    req = Request(origin + path, data=data, headers=sent, method=method)
    try:
        response = OPENER.open(req, timeout=6)
    except HTTPError as error:
        response = error
    with response:
        payload = response.read()
        assert response.status == status, (method, path, response.status, status, payload[:2000])
        try:
            parsed = json.loads(payload) if payload else None
        except ValueError:
            parsed = payload
        return parsed, response.headers


def login(base: str) -> dict[str, str]:
    challenge, headers = request(base, 'GET', '/api/csrf-token')
    guest = headers.get('Set-Cookie', '').split(';', 1)[0]
    assert guest, 'guest cookie missing'
    logged_in, headers = request(
        base, 'POST', '/host-login', body={},
        headers={'Cookie': guest, 'X-CSRF-Token': challenge['token']},
    )
    assert logged_in['success'] is True, logged_in
    cookie = headers.get('Set-Cookie', '').split(';', 1)[0]
    assert cookie and cookie != guest, (cookie, guest)
    csrf, _ = request(base, 'GET', '/api/csrf-token', headers={'Cookie': cookie})
    return {'Cookie': cookie, 'X-CSRF-Token': csrf['token']}


def assert_get(base: str, browser: dict[str, str], proxy, version: str) -> None:
    payload, _ = request(base, 'GET', SETTINGS, headers=browser)
    assert payload == {'success': True, 'proxy': proxy, 'client_version': version}, payload


def post_settings(base: str, browser: dict[str, str], body=_MISSING, *, raw=None):
    return request(base, 'POST', SETTINGS, body=body, raw=raw, headers=browser)[0]


def stored(data: Path) -> dict:
    return json.loads((data / 'codex_models.json').read_text(encoding='utf-8'))


def assert_unknown(document: dict) -> None:
    assert {key: document.get(key) for key in UNKNOWN} == UNKNOWN, document


def assert_private_mode(data: Path) -> None:
    if os.name == 'posix':
        mode = (data / 'codex_models.json').stat().st_mode & 0o777
        assert mode == 0o600, oct(mode)


def authenticated_browser(base: str, data: Path) -> dict[str, str]:
    """Reject anonymous/bearer/CSRF-less writes, then log in once."""
    before = (data / 'codex_models.json').read_bytes()
    request(base, 'GET', SETTINGS, status=401)
    token = (data / 'host_api_token').read_text(encoding='utf-8').strip()
    assert token, 'host API token missing'
    request(base, 'GET', SETTINGS, headers={'Authorization': 'Bearer ' + token}, status=401)
    # Unsafe methods are CSRF-gated before the session check; no cookie => 403.
    request(base, 'POST', SETTINGS, body={'proxy': 'auth-write'}, status=403)
    browser = login(base)
    request(base, 'POST', SETTINGS, body={'proxy': 'auth-write'},
            headers={'Cookie': browser['Cookie']}, status=403)
    request(base, 'POST', SETTINGS, body={'proxy': 'auth-write'},
            headers={**browser, 'X-CSRF-Token': 'invalid'}, status=403)
    request(base, 'GET', SETTINGS, headers=browser)
    assert (data / 'codex_models.json').read_bytes() == before, 'rejected writes touched the file'
    return browser


def proxy_env(base: dict[str, str], proxies: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in base.items() if key not in PROXY_KEYS}
    env.update(proxies)
    return env


def wait_for_listener(base: str) -> dict:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            payload, _ = request(base, 'GET', SETTINGS, status=200)
            return payload
        except (URLError, ConnectionError, TimeoutError):
            time.sleep(0.05)
    raise AssertionError('Python listener did not become ready')


@contextmanager
def python_listener(env: dict[str, str]):
    """Mount the production settings blueprint on an isolated Flask listener.

    The listener runs in a daemon thread so the sandbox does not need to signal
    a child process. Environment variables and the two auth shims are scoped to
    the listener lifetime, then restored.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    saved = {key: os.environ.get(key) for key in (*env, *PROXY_KEYS)}
    for key in PROXY_KEYS:
        os.environ.pop(key, None)
    os.environ.update(env)
    httpd = None
    thread = None
    serving = False
    try:
        from flask import Flask
        from werkzeug.serving import make_server

        from server import auth_helpers, codex_auth

        original_logged_in = auth_helpers.is_logged_in
        original_admin = auth_helpers.is_admin_user
        app = Flask('codex-settings-contract')
        app.config['SECRET_KEY'] = 'codex-settings-contract'
        app.register_blueprint(codex_auth.codex_auth_bp)
        with ExitStack() as stack:
            # The decorators call these names through auth_helpers' globals.
            # ExitStack restores the real functions even if setup fails.
            stack.enter_context(patch.object(auth_helpers, 'is_logged_in', lambda: True))
            stack.enter_context(patch.object(auth_helpers, 'is_admin_user', lambda: True))
            httpd = make_server('127.0.0.1', 0, app, threaded=True)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            serving = True
            yield f'http://127.0.0.1:{httpd.server_port}'
        assert auth_helpers.is_logged_in is original_logged_in
        assert auth_helpers.is_admin_user is original_admin
    finally:
        if serving:
            httpd.shutdown()
            thread.join(timeout=5)
        if httpd is not None:
            httpd.server_close()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(binary: Path) -> None:
    sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
    from agent_process_roundtrip import ROOT, fixture_environment, rust

    assert ROOT == REPO
    endpoint = 'http://127.0.0.1:1/v1/chat/completions'
    with tempfile.TemporaryDirectory(dir=HERE, prefix='codex-settings-') as directory:
        root = Path(directory)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(REPO)
        data = Path(env['DATA_DIR'])
        data.mkdir(parents=True, exist_ok=True)
        model_file = data / 'codex_models.json'
        model_file.write_text(json.dumps(SEED, ensure_ascii=False, indent=2), encoding='utf-8')
        os.chmod(model_file, 0o600)

        # 1) HTTP_PROXY wins over http_proxy; whitespace-only/empty values are skipped.
        env1 = proxy_env(env, {
            'HTTPS_PROXY': '   ', 'https_proxy': '',
            'HTTP_PROXY': '  http://upper-http  ', 'http_proxy': 'http://lower-http',
        })
        with rust(env1, binary, endpoint, root / 'web-1.log', web=True) as base:
            browser = authenticated_browser(base, data)
            assert_get(base, browser, 'http://upper-http', '8.1.0')

            before = model_file.read_bytes()
            reply = post_settings(base, browser, {'unknown_patch': {'nested': [1, 2]}})
            assert reply == {'success': True, 'proxy': 'http://upper-http'}, reply
            assert model_file.read_bytes() == before, 'unknown-only body caused a write'

            before = model_file.read_bytes()
            reply = post_settings(base, browser, raw=b'{"proxy":')
            assert reply == {'success': True, 'proxy': 'http://upper-http'}, reply
            assert model_file.read_bytes() == before, 'malformed body caused a write'

            reply = post_settings(base, browser, {
                'proxy': '  file-after-rust  ', 'client_version': ' 9.9.9 ',
                'ignored': 'must not be persisted',
            })
            assert reply == {'success': True, 'proxy': 'http://upper-http'}, reply
            document = stored(data)
            assert document['proxy'] == 'file-after-rust', document
            assert document['client_version'] == '9.9.9', document
            assert 'ignored' not in document, document
            assert_unknown(document)
            assert_get(base, browser, 'http://upper-http', '9.9.9')

            reply = post_settings(base, browser, {'proxy': '   '})
            assert reply == {'success': True, 'proxy': 'http://upper-http'}, reply
            document = stored(data)
            assert 'proxy' not in document and document['client_version'] == '9.9.9', document
            assert_unknown(document)
            assert_get(base, browser, 'http://upper-http', '9.9.9')

            reply = post_settings(base, browser, {'client_version': None})
            assert reply == {'success': True, 'proxy': 'http://upper-http'}, reply
            document = stored(data)
            assert set(document) == set(UNKNOWN), document
            assert_unknown(document)
            assert_get(base, browser, 'http://upper-http', DEFAULT_VERSION)

            reply = post_settings(base, browser, {
                'proxy': 'file-from-rust', 'client_version': '9.9.9',
            })
            assert reply == {'success': True, 'proxy': 'http://upper-http'}, reply
            document = stored(data)
            assert set(document) == set(UNKNOWN) | {'proxy', 'client_version'}, document
            assert_unknown(document)
            assert_private_mode(data)

        # 2) https_proxy wins over HTTP_PROXY; empty uppercase HTTPS_PROXY is skipped.
        env2 = proxy_env(env, {
            'HTTPS_PROXY': '', 'https_proxy': '  http://lower-https  ',
            'HTTP_PROXY': 'http://upper-http', 'http_proxy': 'http://lower-http',
        })
        with rust(env2, binary, endpoint, root / 'web-2.log', web=True) as base:
            browser = login(base)
            assert_get(base, browser, 'http://lower-https', '9.9.9')

        # 3) HTTPS_PROXY wins over https_proxy and is trimmed.
        env3 = proxy_env(env, {
            'HTTPS_PROXY': '  http://highest  ', 'https_proxy': 'http://lower-https',
            'HTTP_PROXY': 'http://upper-http', 'http_proxy': 'http://lower-http',
        })
        with rust(env3, binary, endpoint, root / 'web-3.log', web=True) as base:
            browser = login(base)
            assert_get(base, browser, 'http://highest', '9.9.9')

        # 4) Restart without proxy variables: the file value written by Rust must win.
        env4 = proxy_env(env, {})
        with rust(env4, binary, endpoint, root / 'web-4.log', web=True) as base:
            browser = login(base)
            assert_get(base, browser, 'file-from-rust', '9.9.9')

        # 5) Production Python listener takes over the same DATA_DIR, reads the
        #    Rust write, then writes/clears both settings for the next Rust start.
        with python_listener(env4) as python_base:
            payload = wait_for_listener(python_base)
            assert payload == {'success': True, 'proxy': 'file-from-rust', 'client_version': '9.9.9'}, payload
            reply = post_settings(python_base, {}, {
                'proxy': '  python-proxy  ', 'client_version': ' 10.0.0 ',
                'ignored': 'must not be persisted',
            })
            assert reply == {'success': True, 'proxy': 'python-proxy'}, reply
            document = stored(data)
            assert document['proxy'] == 'python-proxy', document
            assert document['client_version'] == '10.0.0', document
            assert 'ignored' not in document, document
            assert_unknown(document)
            assert request(python_base, 'GET', SETTINGS)[0] == {
                'success': True, 'proxy': 'python-proxy', 'client_version': '10.0.0',
            }

            reply = post_settings(python_base, {}, {'proxy': '', 'client_version': '   '})
            assert reply == {'success': True, 'proxy': None}, reply
            assert request(python_base, 'GET', SETTINGS)[0] == {
                'success': True, 'proxy': None, 'client_version': DEFAULT_VERSION,
            }
            document = stored(data)
            assert set(document) == set(UNKNOWN), document
            assert_unknown(document)

            reply = post_settings(python_base, {}, {
                'proxy': 'python-final-proxy', 'client_version': '10.0.1',
            })
            assert reply == {'success': True, 'proxy': 'python-final-proxy'}, reply
            assert_private_mode(data)

        # 6) Rust reads the Python-written settings after restart.
        with rust(env4, binary, endpoint, root / 'web-5.log', web=True) as base:
            browser = login(base)
            assert_get(base, browser, 'python-final-proxy', '10.0.1')
            document = stored(data)
            assert set(document) == set(UNKNOWN) | {'proxy', 'client_version'}, document
            assert_unknown(document)
            final_bytes = model_file.read_bytes()

        # 7) The settings route is Web-only: even an authenticated headless
        #    request gets 404 for both methods and does not touch the file.
        token = (data / 'host_api_token').read_text(encoding='utf-8').strip()
        with rust(env4, binary, endpoint, root / 'headless.log', web=False) as base:
            request(base, 'GET', SETTINGS,
                    headers={'Authorization': 'Bearer ' + token}, status=404)
            request(base, 'POST', SETTINGS, body={'proxy': 'headless-write'},
                    headers={'Authorization': 'Bearer ' + token}, status=404)
            assert model_file.read_bytes() == final_bytes, 'headless request changed the settings file'

    print('PASS: codex settings precedence, writes/clears, auth, restarts, Python takeover and headless 404')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        binary_path = Path(sys.argv[1])
    else:
        binary_path = Path(os.environ['ASTRION_HEADLESS_BIN'])
    run(binary_path.resolve(strict=True))

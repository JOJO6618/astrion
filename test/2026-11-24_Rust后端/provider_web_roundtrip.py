"""Exercise provider administration through the real Web and headless listeners."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_process_roundtrip import HERE, ROOT, fixture_environment, rust
from chat_web_roundtrip import Browser

FIXTURE_KEY = 'fixture-only-private-provider-key'


class ModelList(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != '/v1/models' or self.headers.get('Authorization') != 'Bearer ' + FIXTURE_KEY:
            self.send_error(400)
            return
        payload = json.dumps({'data': [{'id': 'fixture-model', 'max_output_tokens': 4096}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


def check_session_only(base: str, data: Path) -> None:
    token = (data / 'host_api_token').read_text().strip()
    for path in ('/api/providers', '/api/custom-models'):
        req = Request(base + path, headers={'Authorization': 'Bearer ' + token})
        try:
            with urlopen(req, timeout=4) as response:
                raise AssertionError(f'{path} returned {response.status} without browser login')
        except HTTPError as error:
            with error:
                assert error.code == 401, (path, error.code)


def run() -> None:
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temp, ThreadingHTTPServer(('127.0.0.1', 0), ModelList) as upstream:
        root = Path(temp)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir(parents=True, exist_ok=True)
        cache = {'version': 3, 'fetched_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'providers': {}}
        (data / 'modelsdev_cache.json').write_text(json.dumps(cache), encoding='utf-8')
        endpoint = f'http://127.0.0.1:{upstream.server_port}/v1'
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        try:
            payload = {'provider_id': 'fixture', 'name': 'Fixture', 'base_url': endpoint, 'api_key': FIXTURE_KEY}
            with rust(env, binary, endpoint, root / 'web.log', web=True) as base:
                check_session_only(base, data)
                browser = Browser(base)
                browser.request('/api/providers', status=401)
                browser.login()
                token, browser.token = browser.token, ''
                browser.request('/api/providers/connect', payload, status=403)
                assert browser.request('/api/providers')['providers'] == []
                browser.token = token
                connected = browser.request('/api/providers/connect', payload)
                provider_id = connected['provider']['id']
                refreshed = browser.request(f'/api/providers/{provider_id}/refresh', {})
                listing = browser.request('/api/providers')
                assert refreshed['provider']['models'][0]['id'] == 'fixture-model'
                assert listing['providers'][0]['id'] == provider_id
                assert FIXTURE_KEY not in json.dumps([connected, refreshed, listing])
                store = data / 'providers.json'
                assert json.loads(store.read_bytes())['providers'][provider_id]['api_key'] == FIXTURE_KEY
                if os.name == 'posix':
                    assert store.stat().st_mode & 0o777 == 0o600
            saved = store.read_bytes()
            with rust(env, binary, endpoint, root / 'headless.log') as base:
                check_session_only(base, data)
                assert store.read_bytes() == saved
        finally:
            upstream.shutdown()
    print('Provider Web login, CSRF, connect, refresh, masking and headless session boundary passed.')


if __name__ == '__main__':
    run()
